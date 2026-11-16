import numpy as np

from ..backends.cidre import apply_field, validate_fields
from ..io.images import read_image, write_tiff
from ..provenance import read_json, sha256, write_json
from ..qc.illumination import evaluate_correction
from ..reporting.evidence import overlap_sheet
from ..schema import NeedsInput, Payload
from ..storage import require_space


def run(ctx, out):
    group = ctx.manifest.group(ctx.core.group)
    mode = ctx.config["illumination"]["mode"]
    if mode == "none":
        path = out / "frames.json"
        write_json(path, {f.id: str(ctx.manifest.raw_path(f)) for f in ctx.core.fovs})
        return Payload(
            {"frames": path},
            {"mode": "none", "corrected": False},
            ["Illumination correction explicitly disabled; selected frames are original counts."],
        )
    if mode == "basicpy":
        metadata = read_json(ctx.asset("illumination_fit", "fit"))
        if metadata.get("converged") is not True:
            raise NeedsInput("Core BaSiCPy fit did not converge; illumination cannot be applied")
        s_path, d_path = (
            ctx.asset("illumination_fit", name) for name in ("flatfield", "darkfield")
        )
    else:
        if not group.flatfield or not group.darkfield:
            raise NeedsInput(f"Group {group.id} requires explicit flatfield and darkfield assets")
        s_path, d_path = (
            ctx.manifest.resolve(group.flatfield),
            ctx.manifest.resolve(group.darkfield),
        )
        metadata = (
            read_json(ctx.manifest.resolve(group.model_metadata)) if group.model_metadata else {}
        )
    s, d = validate_fields(read_image(s_path), read_image(d_path))
    h, w = ctx.config["profile"]["shape"]
    needed = len(ctx.core.fovs) * (h * w * 4 + 4096) + s.nbytes + d.nbytes + 16384
    require_space([(out, needed, "Corrected FOVs and frozen fields")], ctx.config["storage"])
    for key, path in [("flatfield", s_path), ("darkfield", d_path)]:
        expected = metadata.get(key + "_sha256")
        if expected and sha256(path) != expected:
            raise ValueError(f"{key} differs from the frozen model receipt")
    write_tiff(out / "flatfield.tif", s)
    write_tiff(out / "darkfield.tif", d)
    index = {}
    minimum, maximum = float("inf"), float("-inf")
    for fov in ctx.core.fovs:
        image = ctx.tiles.read(ctx.manifest.raw_path(fov))
        corrected = apply_field(image, s, d)
        path = out / f"FOV_{fov.id}.tif"
        write_tiff(
            path, corrected, metadata={"axes": "YX", "formula": "(I-D)/S", "no_clipping": True}
        )
        index[fov.id] = path.name
        minimum, maximum = (
            min(minimum, float(corrected.min())),
            max(maximum, float(corrected.max())),
        )
    frames = out / "frames.json"
    write_json(frames, index)
    model = out / "model.json"
    model_record = {
        "group": group.id,
        "formula": "(I-D)/S",
        "source_metadata": metadata,
        "quality_controls": "not_passed",
        "flatfield_source_sha256": sha256(s_path),
        "darkfield_source_sha256": sha256(d_path),
    }
    if mode == "basicpy":
        model_record.update(
            sample_scope="core", core=ctx.core.uid, absolute_intensity_preservation_validated=False
        )
    write_json(model, model_record)
    return Payload(
        {"frames": frames, "model": model},
        {
            "mode": mode,
            "FOVs": len(index),
            "minimum_counts": minimum,
            "maximum_counts": maximum,
            "gain_p01_p50_p99": np.percentile(1 / s.astype(np.float64), [1, 50, 99]).tolist(),
            "absolute_intensity_preservation_validated": False,
        },
        [
            "Original frozen field formula applied without clipping, per-core gain matching or output smoothing.",
            ("BaSiCPy" if mode == "basicpy" else "CIDRE")
            + " method quality controls remain not passed; outputs require review.",
        ],
    )


def evaluate(ctx, out):
    sample_fit = ctx.config["illumination"]["mode"] == "basicpy"
    members = read_json(ctx.asset("illumination_fit", "membership"))["fovs"] if sample_fit else None
    report = evaluate_correction(
        ctx.raw_frames(),
        ctx.frames(),
        ctx.positions(),
        reader=ctx.tiles.read,
        raw_measurements=ctx.raw_overlaps,
        membership=members,
    )
    report["correction_mode"] = ctx.config["illumination"]["mode"]
    report["model_gain_p01_p50_p99"] = ctx.results["illumination"].metrics.get("gain_p01_p50_p99")
    if sample_fit:
        from collections import Counter

        fit = read_json(ctx.asset("illumination_fit", "fit"))
        counts = dict(Counter(p["fit_membership"] for p in report["compared_pairs"]))
        report.update(
            photometric_independence="in_sample"
            if ctx.config["illumination"]["holdout"] == "none"
            else "membership_dependent",
            fit_membership_counts=counts,
            stack_size=fit["stack_size"],
            fit_tile_count=fit["tile_count"],
            fitter_settings=fit["settings"],
            interpretation="In-sample overlap mismatch is a fit residual. Only both-held-out pairs are independent of illumination fitting; no field confirmation is implied.",
        )
        if "field_identifiability" in fit:
            report["field_identifiability"] = fit["field_identifiability"]
    path = out / "illumination-qc.json"
    settings = ctx.config["artifact_qc"]
    selected = sorted(
        report["compared_pairs"],
        key=lambda e: e["corrected_absolute_difference_p95_counts"],
        reverse=True,
    )
    if sample_fit:
        report["worst_overlaps"] = selected[: settings["evidence_pairs"]]
    preview = out / "photometric-overlaps.png"
    report["evidence"] = overlap_sheet(
        ctx.raw_frames(),
        ctx.positions(),
        selected,
        preview,
        reader=ctx.tiles.read,
        corrected=ctx.frames(),
        count=settings["evidence_pairs"],
        crop=settings["crop_size"],
    )
    write_json(path, report)
    findings = [
        "Same frozen geometry and raw-selected pixels used for raw/corrected overlap comparison.",
        "Excluded overlap pairs remain unknown; overlap-pair assessment coverage is not tissue-area coverage.",
        "Improved seam agreement does not prove preservation of biological signal.",
    ]
    if report["signal_diagnostics"]["collapsed_fov_ids"]:
        findings.append(
            "Review flag: corrected signal has exactly zero native dynamic range in FOVs "
            + ", ".join(report["signal_diagnostics"]["collapsed_fov_ids"])
        )
    return Payload(
        {"overlaps": path, "evidence": preview},
        {
            k: v
            for k, v in report.items()
            if k not in {"raw", "corrected", "compared_pairs", "evidence"}
        },
        findings,
    )
