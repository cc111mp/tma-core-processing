"""Native linear blending in bounded row bands, with explicit coverage."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

from ..io.images import read_image
from ..storage import canvas_shape, require_space


def _blend(jobs, positions, *, alpha, chunk_rows, max_pixels, storage, reader):
    first = reader(next(iter(jobs[0][0].values())))
    h, w = first.shape
    shape = canvas_shape(positions, (h, w))
    pixels = shape[0] * shape[1]
    if pixels > max_pixels:
        raise ValueError(
            "Mosaic exceeds configured pixel limit; review geometry or raise the limit explicitly"
        )
    if alpha <= 0 or not np.isfinite(alpha):
        raise ValueError("Blend alpha must be finite and positive")
    if type(chunk_rows) is not int or chunk_rows <= 0:
        raise ValueError("Blend chunk_rows must be a positive integer")
    requests = []
    for _, output, coverage in jobs:
        requests.extend(
            [
                (output.parent, pixels * 4 + 4096, "Float32 mosaic"),
                (coverage.parent, pixels + 4096, "Coverage map"),
            ]
        )
    require_space(requests, storage)
    ay = np.minimum(np.arange(h) + 1, h - np.arange(h)).astype(np.float64)
    ax = np.minimum(np.arange(w) + 1, w - np.arange(w)).astype(np.float64)
    weight = (ay[:, None] * ax[None, :]) ** alpha
    outputs, coverages = [], []
    validated = set()
    try:
        for _, output, coverage in jobs:
            outputs.append(
                tifffile.memmap(
                    output,
                    shape=shape,
                    dtype=np.float32,
                    bigtiff=True,
                    metadata={
                        "axes": "YX",
                        "formula": f"linear alpha={alpha}",
                        "no_clipping": True,
                        "no_smoothing": True,
                    },
                )
            )
            coverages.append(
                tifffile.memmap(
                    coverage,
                    shape=shape,
                    dtype=np.uint8,
                    bigtiff=True,
                    metadata={"axes": "YX", "meaning": "geometric acquisition coverage only"},
                )
            )
        for y in range(0, shape[0], chunk_rows):
            bottom = min(y + chunk_rows, shape[0])
            band_shape = (bottom - y, shape[1])
            denominator = np.zeros(band_shape, np.float64)
            numerators = [np.zeros(band_shape, np.float64) for _ in jobs]
            # Preserve the original tile addition order at every native pixel.
            for p in positions:
                top, end = max(y, p["y"]), min(bottom, p["y"] + h)
                if top >= end:
                    continue
                src = np.s_[top - p["y"] : end - p["y"], :]
                dst = np.s_[top - y : end - y, p["x"] : p["x"] + w]
                weights = weight[src]
                denominator[dst] += weights
                for n, (frames, _, _) in enumerate(jobs):
                    image = reader(frames[p["id"]])
                    key = n, p["id"]
                    if key not in validated:
                        if image.shape != (h, w) or not np.isfinite(image).all():
                            raise ValueError("Inconsistent or nonfinite FOV for native blending")
                        validated.add(key)
                    numerators[n][dst] += np.asarray(image[src], np.float64) * weights
            present = denominator > 0
            for numerator, output, coverage in zip(numerators, outputs, coverages):
                values = np.divide(
                    numerator, denominator, out=np.zeros_like(numerator), where=present
                ).astype(np.float32)
                if not np.isfinite(values).all():
                    raise ValueError("Native float blend overflow")
                output[y:bottom] = values
                coverage[y:bottom] = present
        for image in outputs + coverages:
            image.flush()
    finally:
        outputs.clear()
        coverages.clear()
    return shape


def blend(
    frames,
    positions,
    output: Path,
    coverage: Path,
    scratch: Path,
    alpha=1.5,
    chunk_rows=256,
    max_pixels=500_000_000,
    storage=None,
    reader=None,
):
    """Blend one frame set. The scratch argument is kept for API compatibility."""
    return _blend(
        [(frames, output, coverage)],
        positions,
        alpha=alpha,
        chunk_rows=chunk_rows,
        max_pixels=max_pixels,
        storage=storage,
        reader=reader or read_image,
    )


def blend_pair(
    raw,
    selected,
    positions,
    raw_output,
    output,
    raw_coverage,
    coverage,
    *,
    alpha=1.5,
    chunk_rows=256,
    max_pixels=500_000_000,
    storage=None,
    reader=None,
):
    """Share weights and row-band denominators for the same raw/selected poses."""
    return _blend(
        [(raw, raw_output, raw_coverage), (selected, output, coverage)],
        positions,
        alpha=alpha,
        chunk_rows=chunk_rows,
        max_pixels=max_pixels,
        storage=storage,
        reader=reader or read_image,
    )
