import numpy as np
from PIL import Image, ImageDraw

from ..backends.blend import blend_pair
from ..io.images import display, read_image, reduced, window, write_tiff
from ..provenance import write_json
from ..qc.boundaries import evaluate_boundaries
from ..qc.periodicity import evaluate_periodicity
from ..reporting.evidence import boundary_view, projection_view
from ..schema import Payload
from ..storage import canvas_shape, require_space


def run(ctx, out):
    settings = ctx.config["mosaic"]
    raw = out / "raw-mosaic.tif"
    selected = out / "mosaic.tif"
    coverage = out / "coverage.tif"
    shape = canvas_shape(ctx.positions(), ctx.config["profile"]["shape"])
    pixels = shape[0] * shape[1]
    require_space(
        [(out, pixels * 10 + 16384, "Both mosaics and coverage maps")], ctx.config["storage"]
    )
    shape = blend_pair(
        ctx.raw_frames(),
        ctx.frames(),
        ctx.positions(),
        raw,
        selected,
        out / "raw-coverage.tif",
        coverage,
        **settings,
        storage=ctx.config["storage"],
        reader=ctx.tiles.read,
    )
    return Payload(
        {"raw": raw, "mosaic": selected, "coverage": coverage},
        {
            "shape_yx": list(shape),
            "dtype": "float32",
            "alpha": settings["alpha"],
            "illumination_mode": ctx.config["illumination"]["mode"],
            "cropped": False,
        },
        [
            "Full acquisition canvas retained; coverage marks missing acquisition separately from zero counts.",
            "Native blending preserves negative and above-65535 float counts.",
        ],
    )


def evaluate(ctx, out):
    raw = read_image(ctx.asset("mosaic", "raw"))
    image = read_image(ctx.asset("mosaic", "mosaic"))
    coverage = read_image(ctx.asset("mosaic", "coverage"))
    size = ctx.config["report"]["preview_size"]
    limits = window(raw, coverage)
    a, b = display(reduced(raw, size), limits), display(reduced(image, size), limits)
    valid = reduced(coverage, size).astype(bool)
    a[~valid], b[~valid] = 0, 0
    h, w = a.shape
    canvas = Image.new("RGB", (2 * w, h + 32), "#171b22")
    canvas.paste(Image.fromarray(a).convert("RGB"), (0, 32))
    canvas.paste(Image.fromarray(b).convert("RGB"), (w, 32))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 8), "Raw reconstruction", fill="white")
    draw.text((w + 8, 8), "Selected output (shared raw count window)", fill="white")
    comparison = out / "comparison.png"
    canvas.save(comparison)
    preview = out / "preview.png"
    Image.fromarray(b).save(preview)
    scientific_preview = write_tiff(
        out / "preview-counts.tif", reduced(image, size).astype(np.float32)
    )
    # Sampling statistics are explicitly not a full-resolution tissue/quality claim.
    metrics = {
        "display_window": list(limits),
        "preview_shape_yx": list(b.shape),
        "preview_geometric_coverage_fraction": float(np.mean(valid)),
        "preview_grid_missed_acquired_pixels": not bool(np.any(valid)),
        "minimum_preview_count": float(reduced(image, size).min()),
        "maximum_preview_count": float(reduced(image, size).max()),
    }
    record = out / "mosaic-qc.json"
    boundaries = evaluate_boundaries(
        raw,
        image,
        coverage,
        ctx.positions(),
        ctx.config["profile"]["shape"],
        ctx.config["artifact_qc"]["boundary_band_pixels"],
    )
    periodicity = evaluate_periodicity(
        raw, image, coverage, ctx.positions(), ctx.config["profile"]["shape"]
    )
    boundary_record, periodicity_record = out / "boundary-qc.json", out / "periodicity-qc.json"
    write_json(boundary_record, boundaries)
    write_json(periodicity_record, periodicity)
    boundary_preview, projection_preview = out / "tile-boundaries.png", out / "tile-projections.png"
    local_preview = out / "local-projections.png"
    boundary_view(raw, image, coverage, boundaries, limits, boundary_preview, size)
    projection_view(periodicity, projection_preview)
    selected_strips = []
    for axis in ("x", "y"):
        for stratum in ("raw_supported", "low_signal"):
            choices = [
                row
                for row in periodicity["local_measurements"]
                if row["axis"] == axis and row["stratum"] == stratum
            ]
            selected_strips.append(
                max(
                    choices,
                    key=lambda row: max(row["corrected"]["harmonic_amplitudes_counts"])
                    if row["corrected"] is not None
                    else -1,
                )
            )
    projection_view(
        {"measurements": selected_strips},
        local_preview,
        title="Strongest corrected local strips: raw orange / corrected cyan; review measurements",
    )
    metrics.update(
        boundary_assessment=boundaries["assessment"],
        boundary_summaries=boundaries["summaries"],
        boundary_measurement_counts=boundaries["measurement_counts"],
        boundary_assessed_seams=boundaries["assessed_seams"],
        worst_seams=boundaries["worst_seams"],
        periodicity_assessment=periodicity["assessment"],
        strongest_local_patterns=periodicity["strongest_local_patterns"],
        periodicity_fit_sampling=periodicity["fit_sampling"],
        periodicity_measurements=[
            {
                k: v
                for k, v in row.items()
                if k
                in (
                    "axis",
                    "stratum",
                    "assessment",
                    "reason",
                    "tile_pitch_native_pixels",
                    "supported_bins",
                    "total_bins",
                    "raw",
                    "corrected",
                )
            }
            for row in periodicity["measurements"]
        ],
        periodicity_local_measurements=[
            {
                k: v
                for k, v in row.items()
                if k
                in (
                    "axis",
                    "stratum",
                    "scope",
                    "strip_index",
                    "region_bbox_xyxy",
                    "assessment",
                    "reason",
                    "tile_pitch_native_pixels",
                    "supported_bins",
                    "total_bins",
                    "raw",
                    "corrected",
                )
            }
            for row in periodicity["local_measurements"]
        ],
        periodicity_assessed_global_measurements=sum(
            row["assessment"] == "review" for row in periodicity["measurements"]
        ),
        periodicity_assessed_local_measurements=sum(
            row["assessment"] == "review" for row in periodicity["local_measurements"]
        ),
        grid_probability=None,
        artifact_area_fraction=None,
    )
    write_json(record, metrics)
    return Payload(
        {
            "comparison": comparison,
            "preview": preview,
            "preview_counts": scientific_preview,
            "measurements": record,
            "boundaries": boundary_record,
            "periodicity": periodicity_record,
            "boundary_evidence": boundary_preview,
            "projection_evidence": projection_preview,
            "local_projection_evidence": local_preview,
        },
        metrics,
        [
            "Visual review required for seams, grid, uneven illumination, direction and core completeness.",
            "A single global image-quality score is not used to certify the core.",
            "Tile-boundary steps and tile-pitch projection fits are uncalibrated diagnostics; anatomy can produce similar responses. Low signal is not confirmed background.",
            "Tail-sensitive seam summaries retain localized defects; local strip profiles accompany global projections to expose spatial cancellation.",
        ]
        + (
            ["Preview grid missed acquired pixels; inspect the native TIFFs and coverage map."]
            if metrics["preview_grid_missed_acquired_pixels"]
            else []
        ),
    )
