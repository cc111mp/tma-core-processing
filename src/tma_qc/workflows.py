"""Explicit stage order for one AF core and one AF–H&E section pair.

Read this module to follow the scientific workflow. The pipeline runner owns
checkpointing, provenance, failure isolation, and report publication.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from .context import Context
from .io.images import read_image
from .io.tile_cache import file_identity
from .qc.integrity import verify_core, verify_file
from .schema import Core, Execution, NeedsInput, SectionPair, StageResult
from .stages import (
    geometry,
    illumination,
    illumination_fit,
    index,
    mosaic,
    registration,
    tile_qc,
    tissue,
)

if TYPE_CHECKING:
    from .pipeline import Runner


def _asset_hashes(manifest, paths, *, identities=None):
    result = {}
    for path in paths:
        if path:
            frozen = Path(path)
            if not frozen.is_absolute():
                frozen = manifest.resolve(path)
            result[str(frozen)] = verify_file(frozen, identities=identities)
    return result


def process_core(runner: Runner, core: Core) -> dict[str, StageResult]:
    """Run the selected, explicit AF sequence in dependency order.

    Every call to runner.stage records its declared dependencies and parameters.
    Missing inputs fail within their stage; unrelated stages/cores can continue.
    The context releases decoded arrays before the next core starts.
    """
    sample_fit = runner.config["illumination"]["mode"] == "basicpy"
    with Context(runner.manifest, runner.config, runner.root, runner.scratch, core=core) as ctx:
        ctx.fitter = runner.fitter
        started = time.perf_counter()
        try:
            if not core.blocked_reasons:
                if sample_fit:
                    ctx.allocate_stack()
                ctx.verified, ctx.source_hashes = verify_core(
                    core,
                    runner.manifest,
                    runner.config["profile"],
                    reader=read_image if sample_fit else ctx.tiles.read,
                    identities=ctx.source_identities,
                    paths=ctx.raw_frames(),
                    stack=ctx.raw_stack,
                    read_threads=runner.config["runtime"]["read_threads"] if sample_fit else 1,
                )
                if sample_fit:
                    ctx.retain_stack()
        except Exception as exc:
            ctx.source_error = str(exc)
        ctx.timings["read_verify_seconds"] = time.perf_counter() - started
        base = runner.root / "acquisitions" / core.acquisition / core.core / core.channel / core.uid
        runner.stage(
            ctx,
            core.uid,
            base,
            "index",
            index.run,
            config_sections=("profile",),
            extra={
                "sources": ctx.source_hashes,
                "source_error": ctx.source_error,
                "ordered_fovs": [
                    {
                        "id": f.id,
                        "path": str(runner.manifest.raw_path(f)),
                        "sha256": f.sha256,
                        "metadata": f.metadata,
                    }
                    for f in core.fovs
                ],
                "core_metadata": core.metadata,
                "blocked_reasons": core.blocked_reasons,
            },
        )
        runner.stage(
            ctx,
            core.uid,
            base,
            "tile_qc",
            tile_qc.run,
            deps=("index",),
            config_sections=("tile_qc",),
        )

        def geometry_inputs():
            values = {
                "assets": _asset_hashes(
                    runner.manifest,
                    [] if sample_fit else [core.positions],
                    identities=ctx.source_identities,
                ),
                "grid": core.metadata.get("grid"),
                "grid_reviewed": core.metadata.get("grid_reviewed"),
                "geometry_reviewed": core.geometry_reviewed,
            }
            if runner.config["geometry"]["backend"] == "mist":
                from .backends.mist import classpath_files

                values["mist_jars"] = {
                    str(p): verify_file(p, identities=ctx.source_identities)
                    for p in classpath_files(runner.config["geometry"], required=sample_fit)
                }
            return values

        def geometry_stages():
            runner.stage(
                ctx,
                core.uid,
                base,
                "geometry",
                geometry.run,
                deps=("index", "illumination_fit", "illumination") if sample_fit else ("index",),
                config_sections=("geometry",),
                extra=geometry_inputs,
            )
            runner.stage(
                ctx,
                core.uid,
                base,
                "geometry_qc",
                geometry.evaluate,
                deps=("index", "geometry"),
                config_sections=("artifact_qc",),
            )

        def illumination_stage():
            group = runner.manifest.group(core.group)
            runner.stage(
                ctx,
                core.uid,
                base,
                "illumination",
                illumination.run,
                deps=("index", "illumination_fit") if sample_fit else ("index",),
                config_sections=("illumination",),
                extra=None
                if sample_fit
                else lambda: {
                    "group": asdict(group),
                    "assets": _asset_hashes(
                        runner.manifest,
                        [group.flatfield, group.darkfield, group.model_metadata],
                        identities=ctx.source_identities,
                    ),
                },
            )

        if sample_fit:

            def fit_inputs():
                if ctx.results["index"].execution != Execution.COMPLETE:
                    raise NeedsInput("Verified core stack unavailable")
                illumination_fit.membership(core, runner.config["illumination"])
                return {
                    "fitter": runner.fitter().identity,
                    "held_out_fov_ids": core.illumination_holdout_fov_ids,
                }

            runner.stage(
                ctx,
                core.uid,
                base,
                "illumination_fit",
                illumination_fit.run,
                deps=("index",),
                config_sections=("illumination",),
                extra=fit_inputs,
            )
            illumination_stage()
            geometry_stages()
        else:
            geometry_stages()
            illumination_stage()
        runner.stage(
            ctx,
            core.uid,
            base,
            "illumination_qc",
            illumination.evaluate,
            deps=("index", "geometry", "illumination"),
            config_sections=("artifact_qc",),
        )
        runner.stage(
            ctx,
            core.uid,
            base,
            "mosaic",
            mosaic.run,
            deps=("geometry", "illumination"),
            config_sections=("mosaic",),
        )
        runner.stage(
            ctx,
            core.uid,
            base,
            "mosaic_qc",
            mosaic.evaluate,
            deps=("mosaic",),
            config_sections=("report", "artifact_qc"),
        )
        runner.stage(
            ctx, core.uid, base, "tissue", tissue.run, deps=("mosaic",), config_sections=("tissue",)
        )
        runner.cohort.finish(core.uid)
    runner.tile_cache_statistics.append(
        {
            "unit": core.uid,
            **ctx.tiles.statistics(),
            "reused_raw_overlap_pairs": ctx.raw_overlaps.hits,
        }
    )
    return ctx.results


def process_pair(runner: Runner, pair: SectionPair, af_results: dict[str, StageResult]) -> None:
    """Run H&E source QC, registration, then independent landmark evaluation."""
    ctx = Context(
        runner.manifest,
        runner.config,
        runner.root,
        runner.scratch,
        pair=pair,
        af_results=af_results,
    )
    try:
        ctx.source_hashes = {
            str(ctx.he_path): verify_file(
                ctx.he_path, pair.he_sha256, identities=ctx.source_identities
            )
        }
    except Exception as exc:
        ctx.source_error = str(exc)
    base = runner.root / "pairs" / pair.uid

    def he_inputs():
        if ctx.source_error:
            raise ValueError(ctx.source_error)
        return {"sources": ctx.source_hashes, "pair": asdict(pair)}

    runner.stage(
        ctx,
        pair.uid,
        base,
        "he_qc",
        registration.he_qc,
        config_sections=("report",),
        extra=he_inputs,
    )

    def registration_inputs():
        af_result = af_results.get("mosaic")
        if not af_result or af_result.execution != Execution.COMPLETE:
            raise NeedsInput("AF mosaic unavailable")
        transform_assets = []
        if pair.transform:
            from .qc.registration import transform_asset_paths

            transform_path = Path(pair.transform)
            verify_file(transform_path, identities=ctx.source_identities)
            transform_assets = transform_asset_paths(transform_path)
        values = {
            "af": {k: v["sha256"] for k, v in af_result.outputs.items()},
            "transform_assets": _asset_hashes(
                runner.manifest, transform_assets, identities=ctx.source_identities
            ),
            "section_relationship": pair.relationship,
        }
        if runner.config["registration"]["backend"] == "core" and not pair.transform:
            from .backends.core_registration import runtime_identity

            if runner.config["registration"]["core_config"]:
                verify_file(
                    runner.config["registration"]["core_config"],
                    identities=ctx.source_identities,
                )
            values["core_registration"] = runtime_identity(runner.config["registration"])
            for record in values["core_registration"].get("model_weights", {}).values():
                path = Path(record["path"])
                if record.get("identity") is not None:
                    identity = tuple(record["identity"])
                    file_identity(path, identity)
                    ctx.source_identities[path] = identity
                else:
                    verify_file(path, record["sha256"], identities=ctx.source_identities)
        return values

    runner.stage(
        ctx,
        pair.uid,
        base,
        "registration",
        registration.run,
        deps=("he_qc",),
        config_sections=("registration", "report"),
        extra=registration_inputs,
    )
    runner.stage(
        ctx,
        pair.uid,
        base,
        "registration_qc",
        registration.evaluate,
        deps=("registration",),
        config_sections=("qc",),
        extra=lambda: {
            "assets": _asset_hashes(
                runner.manifest, [pair.landmarks], identities=ctx.source_identities
            ),
            "geometry_fingerprint": af_results.get("geometry").fingerprint
            if af_results.get("geometry")
            else None,
            "declared_geometry_fingerprint": pair.metadata.get("landmarks_geometry_fingerprint"),
        },
    )
    runner.cohort.finish(pair.uid)
