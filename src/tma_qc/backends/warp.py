"""Native affine warping through bounded source ROIs and output patches."""

from __future__ import annotations

import cv2
import numpy as np

from ..io.images import native_array


def warp_region(source, matrix, rectangle, border, *, max_source_pixels=4 * 1024 * 1024):
    """Warp a native-pixel destination rectangle without passing a whole WSI to OpenCV.

    rectangle is (x, y, width, height) on the AF canvas. Large inverse-mapped
    source bounds are subdivided until both working memory and OpenCV's signed
    16-bit remap dimensions are respected, including for extreme scale changes.
    """
    x, y, width, height = rectangle
    inverse = np.linalg.inv(matrix)
    corners = (
        np.array(
            [
                [x, y, 1.0],
                [x + width - 1, y, 1.0],
                [x, y + height - 1, 1.0],
                [x + width - 1, y + height - 1, 1.0],
            ]
        )
        @ inverse.T
    )
    x0 = max(0, int(np.floor(corners[:, 0].min())) - 2)
    y0 = max(0, int(np.floor(corners[:, 1].min())) - 2)
    x1 = min(source.shape[1], int(np.ceil(corners[:, 0].max())) + 3)
    y1 = min(source.shape[0], int(np.ceil(corners[:, 1].max())) + 3)
    dtype = source.dtype.newbyteorder("=")
    shape = (height, width, *source.shape[2:])
    if x1 <= x0 or y1 <= y0:
        return np.full(shape, border, dtype=dtype)
    if (
        width >= 32767
        or height >= 32767
        or (x1 - x0) >= 32767
        or (y1 - y0) >= 32767
        or (x1 - x0) * (y1 - y0) > max_source_pixels
    ):
        result = np.empty(shape, dtype=dtype)
        if width >= height and width > 1:
            split = width // 2
            result[:, :split] = warp_region(
                source, matrix, (x, y, split, height), border, max_source_pixels=max_source_pixels
            )
            result[:, split:] = warp_region(
                source,
                matrix,
                (x + split, y, width - split, height),
                border,
                max_source_pixels=max_source_pixels,
            )
        elif height > 1:
            split = height // 2
            result[:split] = warp_region(
                source, matrix, (x, y, width, split), border, max_source_pixels=max_source_pixels
            )
            result[split:] = warp_region(
                source,
                matrix,
                (x, y + split, width, height - split),
                border,
                max_source_pixels=max_source_pixels,
            )
        else:
            raise ValueError("Native warp working limit is too small for one interpolation stencil")
        return result
    patch = native_array(source[y0:y1, x0:x1], contiguous=True)
    xs = np.arange(x, x + width, dtype=np.float64)[None, :]
    ys = np.arange(y, y + height, dtype=np.float64)[:, None]
    sx = inverse[0, 0] * xs + inverse[0, 1] * ys + inverse[0, 2]
    sy = inverse[1, 0] * xs + inverse[1, 1] * ys + inverse[1, 2]
    # Quantize GLOBAL source coordinates before subtracting the integer ROI
    # origin. Re-estimating a local affine matrix changes OpenCV's rounding at
    # block boundaries. These fixed-point maps make interpolation independent
    # of output partitioning and source-ROI subdivisions.
    bits = cv2.INTER_BITS
    bins = 1 << bits
    qx = np.rint(np.clip(sx, x0 - 2, x1 + 1) * bins).astype(np.int64) - x0 * bins
    qy = np.rint(np.clip(sy, y0 - 2, y1 + 1) * bins).astype(np.int64) - y0 * bins
    maps = np.empty((height, width, 2), np.int16)
    maps[..., 0], maps[..., 1] = qx // bins, qy // bins
    fractions = (((qy & (bins - 1)) << bits) | (qx & (bins - 1))).astype(np.uint16)
    color = (border, border, border) if source.ndim == 3 else border
    result = cv2.remap(
        patch, maps, fractions, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=color
    )
    if not np.isfinite(result).all():
        raise ValueError("Nonfinite registered H&E pixels")
    return result
