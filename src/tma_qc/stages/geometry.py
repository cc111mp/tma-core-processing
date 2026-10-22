from ..backends.mist import estimate_positions, validate_layout
from ..io.coordinates import load_positions, position_document
from ..provenance import write_json
from ..qc.stitching import evaluate_overlaps
from ..reporting.evidence import overlap_sheet
from ..schema import NeedsInput, Payload
from ..storage import require_space


def run(ctx, out):
    if ctx.core.positions:
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
                    len(ctx.core.fovs) * ((h // factor) * (w // factor) * 2 + 4096) + 1024 * 1024,
                    "MIST registration inputs",
                )
            ],
            ctx.config["storage"],
        )
        rows = estimate_positions(
            ctx.core, ctx.manifest, out, ctx.config["geometry"], reader=ctx.tiles.read
        )
        path = out / "mist-native-positions.json"
        write_json(path, position_document(rows))
        positions = load_positions(path, [f.id for f in ctx.core.fovs])
        origin = "MIST_raw_only"
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
            "geometry_reviewed": ctx.core.geometry_reviewed,
        },
    )
    return Payload(
        {"positions": path},
        {"tiles": len(positions), "source": origin, "reviewed_input": ctx.core.geometry_reviewed},
        [
            "Integer native tile positions preserved. Image-based geometry QC is evaluated separately."
        ],
    )


def evaluate(ctx, out):
    report = evaluate_overlaps(
        ctx.raw_frames(), ctx.positions(), reader=ctx.tiles.read, measurements=ctx.raw_overlaps
    )
    path = out / "overlap-qc.json"
    settings = ctx.config["artifact_qc"]
    assessed = [e for e in report["edges"] if e["phase_residual_native_pixels"] is not None]
    assessed.sort(key=lambda e: sum(v * v for v in e["phase_residual_native_pixels"]), reverse=True)
    preview = out / "geometry-overlaps.png"
    evidence = overlap_sheet(
        ctx.raw_frames(),
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
        },
        [
            "Overlap correlation and phase residuals are advisory; inspect tissue and scan direction.",
            "Geometry estimation matches are not independent validation landmarks.",
            "A full phase vector is unavailable when overlap texture has fewer than two independent gradient directions.",
        ],
    )
