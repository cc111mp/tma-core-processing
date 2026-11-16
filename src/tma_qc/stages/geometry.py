from ..backends.mist import estimate_positions, validate_layout
from ..io.coordinates import load_positions, position_document
from ..provenance import write_json
from ..qc.stitching import evaluate_overlaps, raw_support_masks
from ..reporting.evidence import overlap_sheet
from ..schema import NeedsInput, Payload
from ..storage import require_space


def run(ctx, out):
    corrected = ctx.config["geometry"]["input"] == "corrected"
    timings = {}
    if ctx.core.positions and not corrected:
        positions = load_positions(
            ctx.manifest.resolve(ctx.core.positions), [f.id for f in ctx.core.fovs]
        )
        origin = "provided_native_positions"
    elif ctx.config["geometry"]["backend"] == "mist":
        _, factor = validate_layout(
            ctx.core, ctx.config["geometry"], ctx.config["profile"]["shape"]
        )
        h, w = ctx.config["profile"]["shape"]
        require_space(
            [
                (
                    out,
                    len(ctx.core.fovs)
                    * ((h // factor) * (w // factor) * (4 if corrected else 2) + 4096)
                    + 1024 * 1024,
                    "MIST registration inputs",
                )
            ],
            ctx.config["storage"],
        )
        arguments = {"frames": ctx.frames(), "timings": timings} if corrected else {}
        rows = estimate_positions(
            ctx.core, ctx.manifest, out, ctx.config["geometry"], reader=ctx.tiles.read, **arguments
        )
        path = out / "mist-native-positions.json"
        write_json(path, position_document(rows))
        positions = load_positions(path, [f.id for f in ctx.core.fovs])
        origin = "MIST_corrected_sample" if corrected else "MIST_raw_only"
    else:
        raise NeedsInput("Provide native positions, or configure MIST with a reviewed grid")
    path = out / "positions.json"
    write_json(
        path,
        {
            "coordinate_space": "native_pixels",
            "axes": "XY",
            "origin": "upper_left",
            "positions": positions,
            "source": origin,
            "geometry_reviewed": False if corrected else ctx.core.geometry_reviewed,
        },
    )
    return Payload(
        {"positions": path},
        {
            "tiles": len(positions),
            "source": origin,
            "reviewed_input": ctx.core.metadata.get("grid_reviewed", False)
            if corrected
            else ctx.core.geometry_reviewed,
            **timings,
        },
        [
            "Integer native tile positions preserved. Image-based geometry QC is evaluated separately."
        ],
    )


def evaluate(ctx, out):
    corrected = ctx.config["geometry"]["input"] == "corrected"
    frames = ctx.frames() if corrected else ctx.raw_frames()
    if corrected:
        masks, _ = raw_support_masks(ctx.raw_frames(), ctx.positions(), reader=ctx.tiles.read)
        report = evaluate_overlaps(frames, ctx.positions(), reader=ctx.tiles.read, masks=masks)
        labels = {"estimation_inputs": "corrected_float32", "geometry_independence": "in_sample"}
        report.update(labels)
    else:
        report = evaluate_overlaps(
            frames, ctx.positions(), reader=ctx.tiles.read, measurements=ctx.raw_overlaps
        )
        labels = {}
    path = out / "overlap-qc.json"
    settings = ctx.config["artifact_qc"]
    assessed = [e for e in report["edges"] if e["phase_residual_native_pixels"] is not None]
    assessed.sort(key=lambda e: sum(v * v for v in e["phase_residual_native_pixels"]), reverse=True)
    if corrected:
        report["largest_phase_residuals"] = assessed[: settings["evidence_pairs"]]
    preview = out / "geometry-overlaps.png"
    evidence = overlap_sheet(
        frames,
        ctx.positions(),
        assessed,
        preview,
        reader=ctx.tiles.read,
        count=settings["evidence_pairs"],
        crop=settings["crop_size"],
    )
    report["evidence"] = evidence
    maximum = max(
        (sum(v * v for v in e["phase_residual_native_pixels"]) ** 0.5 for e in assessed),
        default=None,
    )
    write_json(path, report)
    return Payload(
        {"overlaps": path, "evidence": preview},
        {
            "overlapping_pairs": report["overlapping_pairs"],
            "geometric_components": report["geometric_components"],
            "geometry_assessed_pairs": len(assessed),
            "geometry_candidate_pairs": report["geometry_candidate_pairs"],
            "geometry_assessed_pair_fraction": report["geometry_assessed_pair_fraction"],
            "geometry_exclusions_by_reason": report["geometry_exclusions_by_reason"],
            "maximum_phase_residual_native_pixels": maximum,
            "assessment": report["geometry_assessment"],
            **labels,
        },
        [
            "Overlap correlation and phase residuals are advisory; inspect tissue and scan direction.",
            "Geometry estimation matches are not independent validation landmarks.",
            "A full phase vector is unavailable when overlap texture has fewer than two independent gradient directions.",
        ],
    )
