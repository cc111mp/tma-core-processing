"""Fit and freeze one core's field; held-out FOVs remain stitching inputs."""

import numpy as np

from ..illumination_config import PROFILE_NAME
from ..io.images import write_tiff
from ..provenance import write_json
from ..schema import NeedsInput, Payload


def membership(core, settings):
    declared = core.illumination_holdout_fov_ids
    if settings["holdout"] == "none" and declared:
        raise NeedsInput("Declared illumination holdouts require illumination.holdout = declared")
    if settings["holdout"] == "declared" and not declared:
        raise NeedsInput("Declare illumination_holdout_fov_ids for this core")
    held = set(declared)
    result = {f.id: "held_out" if f.id in held else "in_fit" for f in core.fovs}
    if not any(value == "in_fit" for value in result.values()):
        raise NeedsInput("At least one core FOV must remain in the fit")
    return result


def run(ctx, out):
    settings = ctx.config["illumination"]
    members = membership(ctx.core, settings)
    indices = [i for i, f in enumerate(ctx.core.fovs) if members[f.id] == "in_fit"]
    s, d, receipt = ctx.fitter().fit(
        ctx.stack_path, indices, settings["basicpy"], ctx.stack_directory / "fit"
    )
    shape = tuple(ctx.config["profile"]["shape"])
    if s.shape != shape or d.shape != shape or not np.isfinite(s).all() or not np.isfinite(d).all():
        raise ValueError("Fitter must return finite native-shape fields")
    receipt.update(
        profile=PROFILE_NAME,
        sample_scope="core",
        core=ctx.core.uid,
        settings=settings["basicpy"],
        tile_count=len(indices),
        stack_size=len(ctx.core.fovs),
        holdout=settings["holdout"],
        sources=[
            {
                "id": f.id,
                "sha256": ctx.source_hashes[str(ctx.manifest.raw_path(f))],
                "membership": members[f.id],
            }
            for f in ctx.core.fovs
        ],
        flatfield_minimum=float(s.min()),
        flatfield_maximum=float(s.max()),
        darkfield_minimum=float(d.min()),
        darkfield_maximum=float(d.max()),
        non_positive_flatfield_fraction=float(np.mean(s <= 0)),
        min_tiles_warning=settings["min_tiles_warning"],
        quality_controls="not_passed",
        absolute_intensity_preservation_validated=False,
    )
    if len(indices) < settings["min_tiles_warning"]:
        receipt["field_identifiability"] = "limited"
    paths = {name: out / (name + ".tif") for name in ("flatfield", "darkfield")}
    write_tiff(paths["flatfield"], s)
    write_tiff(paths["darkfield"], d)
    paths.update(fit=out / "fit.json", membership=out / "membership.json")
    write_json(paths["fit"], receipt)
    write_json(paths["membership"], {"sample_scope": "core", "fovs": members})
    return Payload(
        paths, receipt, ["Core-dependent fitted field; quality controls remain not passed."]
    )
