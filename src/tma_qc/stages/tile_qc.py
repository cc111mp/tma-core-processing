from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..io.images import display
from ..provenance import write_json
from ..qc.focus import tile_windows
from ..schema import Payload


def _native_histogram(image):
    """All native AF counts, with bounded temporary arrays and no stride alias."""
    if image.dtype.kind != "u" or image.dtype.itemsize != 2:
        raise ValueError("Tile QC requires native uint16 AF counts")
    histogram = np.zeros(65536, np.int64)
    for y in range(0, image.shape[0], 512):
        for x in range(0, image.shape[1], 512):
            histogram += np.bincount(image[y : y + 512, x : x + 512].ravel(), minlength=65536)
    return histogram


def _percentiles(histogram, quantiles):
    cumulative = np.cumsum(histogram)
    rank = (int(cumulative[-1]) - 1) * np.asarray(quantiles, np.float64) / 100
    lower, upper = np.floor(rank).astype(np.int64), np.ceil(rank).astype(np.int64)
    low = np.searchsorted(cumulative, lower, side="right")
    high = np.searchsorted(cumulative, upper, side="right")
    return low + (rank - lower) * (high - low)


def _interval_coverage(intervals):
    covered, end = 0, 0
    for start, stop in sorted(set(intervals)):
        covered += max(0, stop - max(start, end))
        end = max(end, stop)
    return covered


def _sampling(rows, shape):
    # tile_windows is a Cartesian grid, so its rectangle union is separable.
    sampled = _interval_coverage((r["x"], r["x"] + r["width"]) for r in rows)
    sampled *= _interval_coverage((r["y"], r["y"] + r["height"]) for r in rows)
    regions = [row["measurement_region"] for row in rows]
    interior = _interval_coverage((r["x"], r["x"] + r["width"]) for r in regions)
    interior *= _interval_coverage((r["y"], r["y"] + r["height"]) for r in regions)
    total = int(np.prod(shape))
    return {
        "sampling_basis": "window_bounds_before_filter_margin",
        "sampled_native_pixels": sampled,
        "total_native_pixels": total,
        "omitted_native_pixels": total - sampled,
        "fraction": sampled / total,
        "measurement_interior_native_pixels": interior,
        "measurement_interior_fraction": interior / total,
    }


def _combined_sampling(summaries):
    totals = {
        key: sum(row["detail_sampling"][key] for row in summaries)
        for key in (
            "sampled_native_pixels",
            "total_native_pixels",
            "omitted_native_pixels",
            "measurement_interior_native_pixels",
        )
    }
    return {
        "sampling_basis": "window_bounds_before_filter_margin",
        **totals,
        "fraction": totals["sampled_native_pixels"] / totals["total_native_pixels"],
        "measurement_interior_fraction": (
            totals["measurement_interior_native_pixels"] / totals["total_native_pixels"]
        ),
    }


def _grid_preview(image):
    scale = min(1.0, 128 / max(image.shape))
    shape = (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale)))
    if shape == image.shape[::-1]:
        return image
    # A stride-only preview can miss all acquired periodic content. This is a
    # display-only area average; scientific detail measurements stay native.
    return cv2.resize(image, shape, interpolation=cv2.INTER_AREA)


def _measure(fov_id, image, settings):
    histogram = _native_histogram(image)
    rows = [
        {"fov": fov_id, **row}
        for row in tile_windows(
            image, settings["window"], settings["stride"], settings["prefilter_sigma"]
        )
    ]
    p01, p99 = _percentiles(histogram, [1, 99])
    unavailable = Counter(
        row["detail_unavailable_reason"]
        for row in rows
        if row["detail_unavailable_reason"] is not None
    )
    summary = {
        "fov": fov_id,
        "p01": float(p01),
        "p99": float(p99),
        "percentile_sampling": "all_native_uint16_pixels",
        "saturation_fraction": float(histogram[-1] / image.size),
        "zero_fraction": float(histogram[0] / image.size),
        "detail_sampling": _sampling(rows, image.shape),
        "detail_assessed_windows": len(rows) - sum(unavailable.values()),
        "detail_unavailable_windows": sum(unavailable.values()),
        "detail_unavailable_by_reason": dict(unavailable),
    }
    return rows, summary, histogram


def _measure_tiles(ctx):
    """Bound outstanding images and preserve manifest order, including failures."""
    settings = ctx.config["tile_qc"]
    workers = min(ctx.config["runtime"]["tile_workers"], len(ctx.core.fovs))
    if workers <= 1:
        for fov in ctx.core.fovs:
            yield _measure(fov.id, ctx.tiles.read(ctx.manifest.raw_path(fov)), settings)
        return
    fovs = iter(ctx.core.fovs)
    pending = deque()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tile-qc") as pool:

        def submit():
            fov = next(fovs, None)
            if fov is not None:
                # Read/cache access stays on this thread; workers only see arrays.
                image = ctx.tiles.read(ctx.manifest.raw_path(fov))
                pending.append(pool.submit(_measure, fov.id, image, settings))

        try:
            for _ in range(workers):
                submit()
            while pending:
                yield pending.popleft().result()
                submit()
        finally:
            for future in pending:
                future.cancel()


def run(ctx, out):
    rows, summaries = [], []
    histogram = np.zeros(65536, np.int64)
    for measurements, summary, counts in _measure_tiles(ctx):
        rows.extend(measurements)
        summaries.append(summary)
        histogram += counts
    lo, hi = _percentiles(histogram, [1, 99.5])
    limits = [float(lo), float(max(hi, lo + 1))]
    sampling = _combined_sampling(summaries)
    unavailable = Counter()
    for summary in summaries:
        unavailable.update(summary["detail_unavailable_by_reason"])
    columns = min(8, len(ctx.core.fovs))
    cell = 144
    sheet = Image.new(
        "RGB", (columns * cell, ((len(ctx.core.fovs) + columns - 1) // columns) * cell), "#171b22"
    )
    draw = ImageDraw.Draw(sheet)
    for n, fov in enumerate(ctx.core.fovs):
        a = ctx.tiles.read(ctx.manifest.raw_path(fov))
        tile = Image.fromarray(display(_grid_preview(a), limits)).convert("RGB")
        tile.thumbnail((128, 120))
        x, y = (n % columns) * cell, (n // columns) * cell
        sheet.paste(tile, (x + 4, y + 20))
        draw.text((x + 4, y + 3), f"FOV {fov.id}", fill="white")
    grid = out / "tile-grid.png"
    sheet.save(grid)
    path = out / "tile-measurements.json"
    write_json(
        path,
        {
            "tiles": summaries,
            "windows": rows,
            "display_window": limits,
            "percentile_sampling": "all_native_uint16_pixels",
            "tile_grid_sampling": "area_averaged_preview",
            "detail_sampling": sampling,
            "detail_unavailable_by_reason": dict(unavailable),
            "blur_segmentation_validated": False,
            "blurred_area_ratio": None,
        },
    )
    return Payload(
        {"measurements": path, "tile_grid": grid},
        {
            "FOVs": len(summaries),
            "windows": len(rows),
            "detail_assessed_windows": sum(
                row["gradient_over_variance"] is not None for row in rows
            ),
            "detail_unavailable_windows": sum(unavailable.values()),
            "detail_unavailable_by_reason": dict(unavailable),
            "detail_sampling": sampling,
            "percentile_sampling": "all_native_uint16_pixels",
            "tile_grid_sampling": "area_averaged_preview",
            "display_window": limits,
            "blur_segmentation_validated": False,
        },
        [
            "Local detail measurements are contrast-normalized diagnostics, not calibrated defocus labels.",
            "No blur/fold mask or blurred-tissue percentage is inferred.",
            "Sampling coverage describes native window bounds and filter interiors, including unavailable windows; it is not tissue or valid-detail area.",
            "The FOV grid averages native counts for display; it is not a native-resolution detail map.",
        ],
    )
