"""Native core registration adapted from the local ``tmareg`` research workflow.

The initializer, optional RoMa matcher and bounded B-spline/MI refinement retain
their research status. Pair identity is supplied by the manifest; held-out
landmarks are deliberately absent from every fitting interface.
"""

from __future__ import annotations

import csv
import importlib.metadata
from pathlib import Path

import numpy as np

from ...provenance import sha256, write_json
from ...registration_config import resolve_core_config
from ...schema import NeedsInput
from .images import prepare_view, transform_points
from .matching import BackendUnavailable, RomaMatcher, cached_weights
from .refinement import refine
from .registration import register_global
from .transforms import Transform


def effective_config(settings):
    """Use one selected initializer while preserving all fitting/refinement settings."""
    return resolve_core_config(settings)


def runtime_identity(settings):
    """Return content identities for the workflow's registration input fingerprint.

    This is read-only. An unavailable optional runtime is an explicit blocked
    prerequisite, rather than a reason to reuse a cached result or switch method.
    """
    config = effective_config(settings)
    identity = {"effective_config": config.to_dict(), "optional_versions": {}}
    if settings.get("core_config"):
        path = Path(settings["core_config"])
        identity["config_asset"] = {"path": str(path), "sha256": sha256(path)}
    packages = []
    if settings["core_method"] == "geometry_roma":
        packages.extend(["romatch", "torch", "torchvision"])
        try:
            identity["model_weights"] = cached_weights()
        except BackendUnavailable as error:
            raise NeedsInput(str(error)) from error
    if config.refinement.backend != "none":
        packages.append("SimpleITK")
    for package in packages:
        try:
            identity["optional_versions"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise NeedsInput(f"Registration requires optional package {package}") from error
    return identity


def _mpp(metadata, key):
    value = metadata.get(key)
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not np.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"Pair metadata.{key} must be finite positive isotropic micrometres/pixel")
    return value


def fit(af, he, settings, metadata, out, scratch):
    """Fit one explicitly paired core without accessing any evaluation landmarks."""
    config = effective_config(settings)
    af_mpp, he_mpp = _mpp(metadata, "af_mpp"), _mpp(metadata, "he_mpp")
    try:
        fixed = prepare_view(af, "af", config.matching.max_dimension, None, af_mpp, scratch)
        moving = prepare_view(he, "he", config.matching.max_dimension, None, he_mpp, scratch)
    except ValueError as error:
        write_json(out / "preparation.json", {"status": "not_assessed", "reason": str(error)})
        raise NeedsInput(f"Core registration inputs need review: {error}") from error
    roma = (
        RomaMatcher(config.roma, config.seed)
        if settings["core_method"] == "geometry_roma"
        else None
    )
    try:
        result = register_global(fixed, moving, settings["core_method"], config, roma)
    except BackendUnavailable as error:
        raise NeedsInput(str(error)) from error
    candidates = out / "candidates.json"
    write_json(candidates, [candidate.record() for candidate in result.candidates])
    if result.best is None:
        reasons = sorted(
            {reason for candidate in result.candidates for reason in candidate.reasons}
        )
        raise NeedsInput("Core registration found no eligible consensus: " + ", ".join(reasons))
    best = result.best
    transform = Transform(best.matrix, fixed.to_native, moving.to_native)
    if result.ambiguous:
        local = {"status": "skipped_ambiguous_orientation", "backend": config.refinement.backend}
    else:
        try:
            field, local = refine(fixed, moving, best.matrix, config)
            transform.pull_displacement = field
        except BackendUnavailable as error:
            raise NeedsInput(str(error)) from error
        except (RuntimeError, ValueError) as error:
            local = {"status": "error_global_retained", "reason": str(error)}
    refinement = out / "refinement.json"
    write_json(refinement, local)
    outputs = {"candidates": candidates, "refinement": refinement}
    if transform.pull_displacement is not None:
        field = out / "local-pull-displacement.npy"
        np.save(field, transform.pull_displacement, allow_pickle=False)
        outputs["local_pull_displacement"] = field
    correspondences = out / "fit-correspondences.csv"
    he_points = transform_points(moving.to_native, best.matches.moving)
    af_points = transform_points(fixed.to_native, best.matches.fixed)
    with correspondences.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["he_x", "he_y", "af_x", "af_y", "fit_confidence", "fit_inlier"])
        for h, a, confidence, keep in zip(
            he_points, af_points, best.matches.confidence, best.inliers
        ):
            writer.writerow([*h, *a, confidence, int(keep)])
    outputs["fit_correspondences"] = correspondences
    diagnostics = {
        "source": "integrated_core_registration_research_workflow",
        "method": settings["core_method"],
        "config": config.to_dict(),
        "scale_source": result.scale_source,
        "ambiguous_orientation": result.ambiguous,
        "refinement": local,
        "af_native_shape_yx": list(af.shape[:2]),
        "he_native_shape_yx": list(he.shape[:2]),
        "af_working_shape_yx": list(fixed.gray.shape),
        "he_working_shape_yx": list(moving.gray.shape),
        "foreground_masks_are_validated_tissue_segmentations": False,
        **best.metrics,
    }
    if roma is not None:
        diagnostics["model"] = roma.metadata
    return transform, diagnostics, outputs
