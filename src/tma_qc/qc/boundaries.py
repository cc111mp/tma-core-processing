"""Paired intensity-step diagnostics at known tile-overlap midlines.

Measurements are neither a GRID classifier nor tissue segmentation. Anatomical
edges can create steps too; nearby control cuts are retained for interpretation.
All support is frozen from the raw reconstruction and acquisition coverage.
"""

from __future__ import annotations

import numpy as np

from ..io.coordinates import overlap_pairs
from ..io.images import covered_sample


def _summary(values):
    return {
        "count": len(values),
        "median": float(np.median(values)) if values else None,
        "p95": float(np.percentile(values, 95)) if values else None,
        "maximum": float(max(values)) if values else None,
    }


def _cut(raw, corrected, coverage, bbox, axis, cut, band, threshold, scale):
    x0, y0, x1, y1 = bbox
    lower, upper = (x0, x1) if axis == "x" else (y0, y1)
    if cut - band < lower or cut + band > upper:
        return None
    region = (
        np.s_[y0:y1, cut - band : cut + band]
        if axis == "x"
        else np.s_[cut - band : cut + band, x0:x1]
    )
    a = np.asarray(raw[region], np.float64)
    b = np.asarray(corrected[region], np.float64)
    covered = np.asarray(coverage[region], bool)
    if axis == "y":
        a, b, covered = a.T, b.T, covered.T
    valid = covered.all(axis=1) & np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
    left, right = a[:, :band].mean(axis=1), a[:, band:].mean(axis=1)
    selections = {
        "raw_supported": valid & (left > threshold) & (right > threshold),
        "low_signal": valid & (left <= threshold) & (right <= threshold),
        "mixed_or_unknown": valid & ((left > threshold) != (right > threshold)),
    }
    result = {"cut_native_pixel": cut, "strata": {}}
    for name, mask in selections.items():
        count = int(mask.sum())
        metrics = {
            "line_samples": count,
            "assessment": "review" if count >= 8 else "not_assessed",
            "reason": None if count >= 8 else "Fewer than eight supported native lines",
        }
        for label, image in (("raw", a), ("corrected", b)):
            delta = image[:, band:].mean(axis=1) - image[:, :band].mean(axis=1)
            selected = np.flatnonzero(mask)
            worst = selected[np.argmax(np.abs(delta[selected]))] if count >= 8 else None
            metrics[label] = {
                "median_absolute_step_over_raw_range": float(np.median(np.abs(delta[mask])) / scale)
                if count >= 8
                else None,
                "p95_absolute_step_over_raw_range": float(
                    np.percentile(np.abs(delta[mask]), 95) / scale
                )
                if count >= 8
                else None,
                "maximum_absolute_step_over_raw_range": float(np.max(np.abs(delta[mask])) / scale)
                if count >= 8
                else None,
                "maximum_step_line_native_pixel": int((y0 if axis == "x" else x0) + worst)
                if worst is not None
                else None,
                "median_signed_step_counts": float(np.median(delta[mask])) if count >= 8 else None,
            }
        result["strata"][name] = metrics
    return result


def evaluate_boundaries(raw, corrected, coverage, positions, tile_shape, band=8):
    """Compare steps at native overlap midlines and nearby control cuts.

    Raw/corrected mosaics share geometry and a coverage map. ``band`` is the
    width in native pixels on each side of a cut; ``tile_shape`` is height/width.
    Signal strata and the normalization range come from raw counts. Results
    retain per-seam tails and locations, rather than an affected-area estimate.
    Work uses bounded strips instead of another full-resolution canvas.
    """
    if raw.ndim != 2 or corrected.shape != raw.shape or coverage.shape != raw.shape:
        raise ValueError("Boundary QC requires matching 2D raw, corrected and coverage images")
    if type(band) is not int or band < 1:
        raise ValueError("Boundary band must be a positive native pixel count")
    values, reference_sampling = covered_sample(raw, coverage, 1024)
    if not len(values):
        raise ValueError("Boundary QC has no finite acquired samples")
    low, high = np.percentile(values, [1, 99])
    scale = float(max(high - low, 1.0))
    threshold = float(low + 0.12 * scale)
    seams, skipped = [], []
    for a, b, bbox in overlap_pairs(positions, tile_shape):
        dx, dy = abs(a["x"] - b["x"]), abs(a["y"] - b["y"])
        if dx > 2 * dy:
            axis, cut = "x", (bbox[0] + bbox[2]) // 2
        elif dy > 2 * dx:
            axis, cut = "y", (bbox[1] + bbox[3]) // 2
        else:
            skipped.append(
                {
                    "a": a["id"],
                    "b": b["id"],
                    "reason": "Diagonal overlap; no row/column seam assigned",
                }
            )
            continue
        center = _cut(raw, corrected, coverage, bbox, axis, cut, band, threshold, scale)
        if center is None:
            skipped.append(
                {"a": a["id"], "b": b["id"], "reason": "Overlap narrower than the measurement band"}
            )
            continue
        controls = [
            value
            for offset in (-2 * band, 2 * band)
            if (
                value := _cut(
                    raw, corrected, coverage, bbox, axis, cut + offset, band, threshold, scale
                )
            )
            is not None
        ]
        seams.append(
            {
                "id": len(seams) + 1,
                "a": a["id"],
                "b": b["id"],
                "axis": axis,
                "overlap_bbox_xyxy": list(bbox),
                "midline": center,
                "nearby_controls": controls,
            }
        )
    summaries, worst_seams = {}, {}
    for stratum in ("raw_supported", "low_signal", "mixed_or_unknown"):
        summaries[stratum], worst_seams[stratum] = {}, {}
        assessed_seams = [
            row for row in seams if row["midline"]["strata"][stratum]["assessment"] == "review"
        ]
        for label in ("raw", "corrected"):
            # Retain the original summary of per-seam medians. Tail summaries
            # describe per-seam statistics, not pooled lines or artifact area.
            summaries[stratum][label] = _summary(
                [
                    row["midline"]["strata"][stratum][label]["median_absolute_step_over_raw_range"]
                    for row in assessed_seams
                ]
            )
            for statistic, key in (
                ("per_seam_p95", "p95_absolute_step_over_raw_range"),
                ("per_seam_maximum", "maximum_absolute_step_over_raw_range"),
            ):
                summaries[stratum][label][statistic] = _summary(
                    [row["midline"]["strata"][stratum][label][key] for row in assessed_seams]
                )
            ranked = sorted(
                assessed_seams,
                key=lambda row: (
                    row["midline"]["strata"][stratum][label][
                        "maximum_absolute_step_over_raw_range"
                    ],
                    row["midline"]["strata"][stratum][label]["p95_absolute_step_over_raw_range"],
                ),
                reverse=True,
            )[:5]
            worst_seams[stratum][label] = [
                {
                    "seam_id": row["id"],
                    "a": row["a"],
                    "b": row["b"],
                    "axis": row["axis"],
                    "overlap_bbox_xyxy": row["overlap_bbox_xyxy"],
                    "cut_native_pixel": row["midline"]["cut_native_pixel"],
                    "line_samples": row["midline"]["strata"][stratum]["line_samples"],
                    **row["midline"]["strata"][stratum][label],
                }
                for row in ranked
            ]
    measurement_counts = {stratum: summaries[stratum]["raw"]["count"] for stratum in summaries}
    assessed = sum(
        any(value["assessment"] == "review" for value in row["midline"]["strata"].values())
        for row in seams
    )
    return {
        "assessment": "review" if assessed else "not_assessed",
        "assessed_seams": assessed,
        "measurement_counts": measurement_counts,
        "coordinate_space": "native_af_pixels",
        "band_width_native_pixels": band,
        "raw_support_threshold_counts": threshold,
        "raw_normalization_range_counts": scale,
        "raw_reference_sampling": reference_sampling,
        "raw_reference_samples": len(values),
        "minimum_line_samples": 8,
        "seams": seams,
        "skipped_pairs": skipped,
        "summaries": summaries,
        "worst_seams": worst_seams,
        "worst_seam_ranking": "Per-seam maximum absolute step, then p95 absolute step; at most five per stratum and image. Isolated peaks require review, not automatic rejection.",
        "rules_calibrated": False,
        "grid_probability": None,
        "artifact_area_fraction": None,
        "interpretation": "Intensity steps at known overlap midlines and nearby controls. Anatomy, blur and misalignment can also change these values. Low signal is not confirmed background; this is not a periodic GRID detector or tissue mask.",
    }
