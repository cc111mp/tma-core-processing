"""Contrast-normalized detail diagnostics, NOT validated defocus classifications."""

from __future__ import annotations

from math import isfinite
from numbers import Integral, Real

import cv2
import numpy as np


def detail_metrics(image, sigma=1.0):
    a = np.asarray(image, np.float64)
    if a.ndim != 2 or min(a.shape) < 8:
        raise ValueError("Detail measurement requires a 2D patch at least 8 pixels wide")
    if isinstance(sigma, bool) or not isinstance(sigma, Real) or not isfinite(sigma) or sigma <= 0:
        raise ValueError("Detail prefilter sigma must be finite and positive")
    margin = min(8, min(a.shape) // 4)
    region = {
        "x": margin,
        "y": margin,
        "width": a.shape[1] - 2 * margin,
        "height": a.shape[0] - 2 * margin,
    }
    finite_fraction = float(np.count_nonzero(np.isfinite(a)) / a.size)
    result = {
        "gradient_energy": None,
        "local_variance": None,
        "gradient_over_variance": None,
        "p95_minus_p05": None,
        "residual_mad": None,
        "measurement_region": region,
        "finite_fraction": finite_fraction,
        "detail_unavailable_reason": "nonfinite_input",
        "interpretation": "experimental_detail_not_blur_probability",
    }
    if finite_fraction < 1:
        return result
    # Remove DC before filtering. Float32 filtering at native count offsets can
    # manufacture gradients in weak texture; the ratio must not depend on DC.
    with np.errstate(over="ignore", invalid="ignore"):
        centered = a - a[0, 0]
    if not np.isfinite(centered).all():
        result["detail_unavailable_reason"] = "nonfinite_measurement"
        return result
    filtered = cv2.GaussianBlur(centered, (0, 0), float(sigma))
    gx = cv2.Sobel(filtered, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(filtered, cv2.CV_64F, 0, 1, ksize=3)
    inner = np.s_[margin:-margin, margin:-margin]
    with np.errstate(over="ignore", invalid="ignore"):
        variance = float(np.var(filtered[inner], dtype=np.float64))
        energy = float(np.mean(gx[inner] ** 2 + gy[inner] ** 2))
        p05, p95 = np.percentile(a, [5, 95])
        residual = centered - cv2.medianBlur(centered.astype(np.float32), 3)
        noise = float(1.4826 * np.median(np.abs(residual - np.median(residual))))
        spread = float(p95 - p05)
    if not all(isfinite(v) for v in (variance, energy, spread, noise)):
        result["detail_unavailable_reason"] = "nonfinite_measurement"
        return result
    score = energy / variance if variance > 1e-12 else None
    if score is not None and not isfinite(score):
        result["detail_unavailable_reason"] = "nonfinite_measurement"
        return result
    reason = None
    if score is None:
        reason = "constant_input" if not np.any(centered) else "insufficient_filtered_variance"
    result.update(
        gradient_energy=energy,
        local_variance=variance,
        gradient_over_variance=score,
        p95_minus_p05=spread,
        residual_mad=noise,
        detail_unavailable_reason=reason,
    )
    return result


def tile_windows(image, size=256, stride=256, sigma=1.0):
    if (
        isinstance(size, bool)
        or not isinstance(size, Integral)
        or size < 8
        or isinstance(stride, bool)
        or not isinstance(stride, Integral)
        or stride < 1
    ):
        raise ValueError("Detail window size must be an integer >= 8 and stride a positive integer")
    image = np.asarray(image)
    if image.ndim != 2 or min(image.shape) < 8:
        raise ValueError("Detail measurement requires a 2D image at least 8 pixels wide")
    h, w = image.shape
    ys = list(range(0, max(1, h - size + 1), stride))
    xs = list(range(0, max(1, w - size + 1), stride))
    if h >= size and ys[-1] != h - size:
        ys.append(h - size)
    if w >= size and xs[-1] != w - size:
        xs.append(w - size)
    for y in ys:
        for x in xs:
            a = image[y : min(h, y + size), x : min(w, x + size)]
            metrics = detail_metrics(a, sigma)
            metrics["measurement_region"]["x"] += x
            metrics["measurement_region"]["y"] += y
            yield {
                "x": x,
                "y": y,
                "width": a.shape[1],
                "height": a.shape[0],
                **metrics,
            }
