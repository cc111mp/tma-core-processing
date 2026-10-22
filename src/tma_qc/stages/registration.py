"""Conservative global registration baseline for explicit AF/HE section pairs."""

from __future__ import annotations

import shutil
from contextlib import ExitStack

import cv2
import numpy as np
import tifffile

from ..backends.core_registration import fit as fit_core
from ..backends.core_registration.transforms import Transform, sample_native
from ..backends.warp import warp_region
from ..io.images import (
    display,
    mapped_image,
    match_image,
    native_array,
    reduced,
    save_preview,
    structural_gray,
    window,
)
from ..provenance import write_json
from ..qc.registration import (
    evaluate_landmarks,
    load_registration_transform,
    validate_transform,
)
from ..schema import NeedsInput, Payload


def _validate_he(he):
    unsigned = he.dtype.kind == "u" and he.dtype.itemsize in {1, 2}
    gray_float = he.ndim == 2 and he.dtype.kind == "f" and he.dtype.itemsize in {4, 8}
    if not unsigned and not gray_float:
        raise ValueError("H&E requires uint8/uint16 pixels, or finite float32/float64 grayscale")
    if gray_float:
        for y in range(0, he.shape[0], 512):
            for x in range(0, he.shape[1], 512):
                block = he[y : y + 512, x : x + 512]
                if not np.isfinite(block).all():
                    raise ValueError("Nonfinite H&E pixels in native image")
                if np.max(np.abs(block)) > np.finfo(np.float32).max:
                    raise ValueError(
                        "H&E values exceed the finite float32 range used for structural features"
                    )


def he_qc(ctx, out):
    with mapped_image(ctx.he_path, ctx.scratch, rgb=True) as he:
        _validate_he(he)
        small = native_array(reduced(he, ctx.config["report"]["preview_size"]), contiguous=True)
        preview = out / "he-preview.png"
        save_preview(preview, small)
        metrics = {
            "shape": list(he.shape),
            "dtype": str(he.dtype),
            "finite_validation": "native_all_pixels"
            if he.dtype.kind == "f"
            else "finite_integer_encoding",
            "fold_segmentation_validated": False,
            "focus_segmentation_validated": False,
        }
    return Payload(
        {"preview": preview},
        metrics,
        [
            "Review original H&E for folds, focus, tears and core identity.",
            "Neighboring-section differences must not be automatically labelled as folds.",
        ],
    )


def estimate_affine(af, he, settings):
    af_small = reduced(af, settings["max_dimension"])
    he_small = reduced(he, settings["max_dimension"])
    af_step = max(1, int(np.ceil(max(af.shape[:2]) / settings["max_dimension"])))
    he_step = max(1, int(np.ceil(max(he.shape[:2]) / settings["max_dimension"])))
    sift = cv2.SIFT_create(nfeatures=6000)
    ka, da = sift.detectAndCompute(match_image(af_small), None)
    kh, dh = sift.detectAndCompute(match_image(he_small), None)
    if da is None or dh is None or min(len(da), len(dh)) < 2:
        raise NeedsInput("Insufficient shared structures for global AF-HE feature matching")
    matches = [m for m, n in cv2.BFMatcher().knnMatch(dh, da, k=2) if m.distance < 0.7 * n.distance]
    if len(matches) < settings["min_matches"]:
        raise NeedsInput("Insufficient AF-HE feature matches; supply reviewed native transform")
    points_he = np.float32([kh[m.queryIdx].pt for m in matches])
    points_af = np.float32([ka[m.trainIdx].pt for m in matches])
    cv2.setRNGSeed(20261018)
    matrix, inliers = cv2.estimateAffine2D(
        points_he,
        points_af,
        method=cv2.RANSAC,
        ransacReprojThreshold=settings["ransac_pixels"],
        maxIters=5000,
        confidence=0.999,
    )
    if matrix is None or int(inliers.sum()) < settings["min_matches"]:
        raise NeedsInput("Global AF-HE consensus failed; require review")
    matrix = (
        np.diag([af_step, af_step, 1.0])
        @ validate_transform(matrix)
        @ np.diag([1 / he_step, 1 / he_step, 1.0])
    )
    keep = inliers.ravel().astype(bool)
    span = np.ptp(points_af[keep], axis=0) / np.array([af_small.shape[1], af_small.shape[0]])
    return validate_transform(matrix), {
        "fit_matches": len(matches),
        "fit_inliers": int(keep.sum()),
        "fit_feature_span_fraction_xy": span.tolist(),
        "fit_features_are_independent_validation": False,
    }


def run(ctx, out):
    with ExitStack() as stack:
        af = stack.enter_context(mapped_image(ctx.af_asset("mosaic", "mosaic"), ctx.scratch))
        he = stack.enter_context(mapped_image(ctx.he_path, ctx.scratch, rgb=True))
        coverage = stack.enter_context(
            mapped_image(ctx.af_asset("mosaic", "coverage"), ctx.scratch)
        )
        _validate_he(he)
        return _register(ctx, out, af, he, coverage)


def _register(ctx, out, af, he, coverage):
    settings = ctx.config["registration"]
    outputs = {}
    chunk = 512
    if ctx.pair.transform:
        transform, provided_record = load_registration_transform(
            ctx.manifest.resolve(ctx.pair.transform)
        )
        for key, actual in (
            ("af_native_shape_yx", af.shape[:2]),
            ("he_native_shape_yx", he.shape[:2]),
        ):
            if provided_record.get(key) is not None and list(actual) != provided_record[key]:
                raise ValueError(f"Provided transform {key} disagrees with the actual source image")
        matrix = validate_transform(transform.affine_native)
        diagnostics = {"source": "provided_native_transform"}
        if transform.pull_displacement is not None:
            field = out / "local-pull-displacement.npy"
            np.save(field, transform.pull_displacement, allow_pickle=False)
            outputs["local_pull_displacement"] = field
    elif settings["backend"] == "sift_affine":
        matrix, diagnostics = estimate_affine(af, he, settings)
        transform = Transform(matrix, np.eye(3), np.eye(3))
        diagnostics["source"] = "experimental_structural_SIFT_affine"
    elif settings["backend"] == "core":
        transform, diagnostics, outputs = fit_core(
            af, he, settings, ctx.pair.metadata, out, ctx.scratch
        )
        matrix = validate_transform(transform.affine_native)
        chunk = diagnostics["config"]["export_chunk"]
    else:
        raise NeedsInput(
            "Provide a reviewed HE-to-AF transform or explicitly configure a registration backend"
        )
    nonrigid = transform.pull_displacement is not None
    transform.validate_af_domain(af.shape)
    field_file = outputs["local_pull_displacement"].name if nonrigid else None
    transform_path = out / "transform.json"
    record = {
        **transform.record(field_file),
        "matrix": matrix.tolist(),
        "direction": "he_to_af",
        "coordinate_space": "native_pixels",
        "native_coordinate_convention": "pixel_centres_xy",
        "section_relationship": ctx.pair.relationship,
        "diagnostics": diagnostics,
        "nonrigid_applied": nonrigid,
        "quality_validated": False,
        "af_native_shape_yx": list(af.shape[:2]),
        "he_native_shape_yx": list(he.shape[:2]),
    }
    # Transform.load retains the original research contract separately from the
    # public native-affine contract. matrix is global-only when a local field exists.
    record["research_coordinate_space"] = "original_native_pixel_centres_xy"
    write_json(transform_path, record)
    shape = af.shape[:2]
    required = int(np.prod(shape)) * (
        1 + (he.dtype.itemsize * int(np.prod(he.shape[2:])) if settings["export_aligned_he"] else 0)
    )
    if shutil.disk_usage(out).free < required + 64 * 1024**2:
        raise NeedsInput(
            f"Insufficient space for registration exports: need {required} bytes plus 64 MiB"
        )
    overlap_path = out / "geometric-overlap.tif"
    overlap = tifffile.memmap(overlap_path, shape=shape, dtype=np.uint8, bigtiff=True)
    aligned = None
    outputs.update({"transform": transform_path, "geometric_overlap": overlap_path})
    try:
        if settings["export_aligned_he"]:
            aligned_path = out / "aligned-he.tif"
            aligned = tifffile.memmap(
                aligned_path,
                shape=(*shape, *he.shape[2:]),
                dtype=he.dtype.newbyteorder("="),
                bigtiff=True,
                photometric="rgb" if he.ndim == 3 else "minisblack",
            )
            outputs["aligned_he"] = aligned_path
        white = int(np.iinfo(he.dtype).max) if np.issubdtype(he.dtype, np.integer) else 1.0
        for y in range(0, shape[0], chunk):
            for x in range(0, shape[1], chunk):
                h, w = min(chunk, shape[0] - y), min(chunk, shape[1] - x)
                yy, xx = np.meshgrid(np.arange(y, y + h), np.arange(x, x + w), indexing="ij")
                source = transform.af_to_he(np.column_stack([xx.ravel(), yy.ravel()]))
                valid = (
                    (source[:, 0] >= 0)
                    & (source[:, 0] <= he.shape[1] - 1)
                    & (source[:, 1] >= 0)
                    & (source[:, 1] <= he.shape[0] - 1)
                ).reshape(h, w)
                region = np.s_[y : y + h, x : x + w]
                overlap[region] = valid & np.asarray(coverage[region], bool)
                if aligned is not None:
                    if nonrigid:
                        aligned[region], _ = sample_native(he, source, (h, w), white)
                    else:
                        aligned[region] = warp_region(he, matrix, (x, y, w, h), white)
    finally:
        for image in (overlap, aligned):
            if image is not None:
                image.flush()
                image._mmap.close()
    size = ctx.config["report"]["preview_size"]
    step = max(1, int(np.ceil(max(shape) / size)))
    fixed = reduced(af, size)
    yy, xx = np.meshgrid(
        np.arange(fixed.shape[0]) * step, np.arange(fixed.shape[1]) * step, indexing="ij"
    )
    source = transform.af_to_he(np.column_stack([xx.ravel(), yy.ravel()]))
    sampled, _ = sample_native(he, source, fixed.shape, white)
    moving = structural_gray(sampled)
    green, magenta = display(fixed, window(fixed)), display(moving, window(moving))
    from PIL import Image

    overlay = out / "overlay.png"
    Image.fromarray(np.dstack([magenta, green, magenta])).save(overlay)
    outputs["overlay"] = overlay
    findings = [
        "Geometric overlap is not a verified correspondence or usable-tissue mask.",
        "Shared anatomy requires independent local verification; neighboring sections can differ.",
    ]
    if diagnostics.get("ambiguous_orientation"):
        findings.append(
            "Competing orientations remain ambiguous; local refinement was skipped. Review the candidate transform."
        )
    if diagnostics.get("refinement", {}).get("status") in {"rejected", "error_global_retained"}:
        findings.append(
            "Local refinement was not applied; the global candidate was retained. Inspect refinement.json for the reason."
        )
    return Payload(
        outputs,
        {
            "section_relationship": ctx.pair.relationship,
            "diagnostics": diagnostics,
            "affine_determinant": float(np.linalg.det(matrix[:2, :2])),
            "native_warp": "bounded_source_roi",
            "output_chunk_shape_yx": [chunk, chunk],
            "nonrigid_applied": nonrigid,
        },
        findings,
    )


def evaluate(ctx, out):
    transform, record = load_registration_transform(ctx.asset("registration", "transform"))
    path = ctx.manifest.resolve(ctx.pair.landmarks) if ctx.pair.landmarks else None
    settings = ctx.config["qc"]
    report = evaluate_landmarks(
        transform,
        path,
        settings["landmark_tolerance_pixels"],
        settings["minimum_landmarks"],
        af_shape=record.get("af_native_shape_yx"),
        he_shape=record.get("he_native_shape_yx"),
    )
    measured = out / "landmark-errors.json"
    write_json(measured, report)
    unknown_path = out / "correspondence-unknown.tif"
    with mapped_image(ctx.af_asset("mosaic", "coverage"), ctx.scratch) as coverage:
        required = int(np.prod(coverage.shape))
        if shutil.disk_usage(out).free < required + 64 * 1024**2:
            raise NeedsInput("Insufficient space for correspondence QC export")
        unknown = tifffile.memmap(
            unknown_path,
            shape=coverage.shape,
            dtype=np.uint8,
            bigtiff=True,
            metadata={
                "meaning": "All acquired AF tissue remains unverified for dense correspondence"
            },
        )
        try:
            for y in range(0, coverage.shape[0], 256):
                unknown[y : y + 256] = coverage[y : y + 256]
        finally:
            unknown.flush()
            unknown._mmap.close()
    return Payload(
        {"landmarks": measured, "correspondence_unknown": unknown_path},
        report,
        [
            "Evaluation landmarks never enter registration fitting.",
            "No dense trusted region is inferred from sparse landmarks or tissue-outline overlap.",
            "Provisional tolerances require calibration; final correspondence remains REVIEW.",
        ],
    )
