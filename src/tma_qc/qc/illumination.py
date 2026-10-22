from __future__ import annotations

from collections import Counter

import numpy as np

from ..io.images import read_image
from .stitching import evaluate_overlaps, raw_support_masks


def _signal_diagnostics(raw, corrected, reader, max_sample=256):
    """Describe output scale on identical sampled coordinates, without acceptance rules."""
    fovs = []
    for ident, path in raw.items():
        before, after = reader(path), reader(corrected[ident])
        if before.shape != after.shape:
            raise ValueError(f"FOV {ident}: raw and corrected shapes differ")
        step = max(1, int(np.ceil(max(before.shape) / max_sample)))
        a = np.asarray(before[::step, ::step], np.float64)
        b = np.asarray(after[::step, ::step], np.float64)
        p_raw = np.percentile(a, [1, 50, 99])
        p_corrected = np.percentile(b, [1, 50, 99])
        raw_span, corrected_span = (
            float(p_raw[2] - p_raw[0]),
            float(p_corrected[2] - p_corrected[0]),
        )
        raw_min, raw_max = float(np.min(before)), float(np.max(before))
        corrected_min, corrected_max = float(np.min(after)), float(np.max(after))
        collapsed = raw_max > raw_min and corrected_max == corrected_min
        fovs.append(
            {
                "fov": ident,
                "sampling_step_native_pixels": step,
                "sample_pixels": int(a.size),
                "raw_percentiles_p01_p50_p99_counts": p_raw.tolist(),
                "corrected_percentiles_p01_p50_p99_counts": p_corrected.tolist(),
                "raw_span_p99_p01_counts": raw_span,
                "corrected_span_p99_p01_counts": corrected_span,
                "span_ratio_corrected_over_raw": corrected_span / raw_span if raw_span else None,
                "raw_minimum_counts": raw_min,
                "raw_maximum_counts": raw_max,
                "corrected_minimum_counts": corrected_min,
                "corrected_maximum_counts": corrected_max,
                "review_flags": ["corrected_signal_collapsed"] if collapsed else [],
            }
        )
    ratios = [
        r["span_ratio_corrected_over_raw"]
        for r in fovs
        if r["span_ratio_corrected_over_raw"] is not None
    ]
    return {
        "assessment": "review" if fovs else "not_assessed",
        "assessed_fovs": len(fovs),
        "span_ratio_assessed_fovs": len(ratios),
        "span_ratio_minimum_median_maximum": [
            float(np.min(ratios)),
            float(np.median(ratios)),
            float(np.max(ratios)),
        ]
        if ratios
        else None,
        "collapsed_fov_ids": [r["fov"] for r in fovs if r["review_flags"]],
        "fovs": fovs,
        "sampling": "Percentiles use identical raw/corrected grid coordinates; extrema use every native pixel",
        "interpretation": "Signal-scale diagnostics do not establish biological intensity preservation; no acceptance thresholds are applied",
    }


def evaluate_correction(raw, corrected, positions, *, reader=None, raw_measurements=None):
    reader = reader or read_image
    masks, selection = raw_support_masks(raw, positions, reader=reader)
    before = evaluate_overlaps(
        raw, positions, masks=masks, reader=reader, measurements=raw_measurements
    )
    eligible = {(r["a"], r["b"]): r["raw_supported_evaluation_pair"] for r in before["edges"]}
    reasons = {(r["a"], r["b"]): r["photometric_exclusion_reason"] for r in before["edges"]}
    after = evaluate_overlaps(
        corrected,
        positions,
        masks=masks,
        eligible=eligible,
        exclusion_reasons=reasons,
        reader=reader,
    )

    def differences(report):
        values = [
            r["support_relative_mean_difference"]
            for r in report["edges"]
            if r["raw_supported_evaluation_pair"]
            and r["support_relative_mean_difference"] is not None
        ]
        return values

    raw_values, corrected_values = differences(before), differences(after)
    compared = []
    for a, b in zip(before["edges"], after["edges"]):
        if a["raw_supported_evaluation_pair"]:
            compared.append(
                {
                    "a": a["a"],
                    "b": a["b"],
                    "overlap_bbox_xyxy": a["overlap_bbox_xyxy"],
                    "raw_relative_difference": a["support_relative_mean_difference"],
                    "corrected_relative_difference": b["support_relative_mean_difference"],
                    "raw_absolute_difference_p95_counts": a[
                        "support_absolute_difference_p95_counts"
                    ],
                    "corrected_absolute_difference_p95_counts": b[
                        "support_absolute_difference_p95_counts"
                    ],
                    "change_corrected_minus_raw": b["support_relative_mean_difference"]
                    - a["support_relative_mean_difference"]
                    if b["support_relative_mean_difference"] is not None
                    and a["support_relative_mean_difference"] is not None
                    else None,
                }
            )
    worst = max(
        compared, key=lambda row: row["corrected_absolute_difference_p95_counts"], default=None
    )

    return {
        "raw_overlap_relative_difference_median": float(np.median(raw_values))
        if raw_values
        else None,
        "corrected_overlap_relative_difference_median": float(np.median(corrected_values))
        if corrected_values
        else None,
        "raw_overlap_relative_difference_p95": float(np.percentile(raw_values, 95))
        if raw_values
        else None,
        "corrected_overlap_relative_difference_p95": float(np.percentile(corrected_values, 95))
        if corrected_values
        else None,
        "worst_corrected_pair": worst,
        "raw_matched_pixel_p95_difference_counts_median": float(
            np.median([r["raw_absolute_difference_p95_counts"] for r in compared])
        )
        if compared
        else None,
        "corrected_matched_pixel_p95_difference_counts_median": float(
            np.median([r["corrected_absolute_difference_p95_counts"] for r in compared])
        )
        if compared
        else None,
        "compared_pairs": compared,
        "candidate_pairs": len(eligible),
        "assessed_pairs": len(compared),
        "raw_supported_pairs": sum(eligible.values()),
        "excluded_pairs": len(eligible) - sum(eligible.values()),
        "assessed_pair_fraction": len(compared) / len(eligible) if eligible else None,
        "exclusions_by_reason": dict(Counter(reason for reason in reasons.values() if reason)),
        "relative_difference_assessed_pairs": {
            "raw": len(raw_values),
            "corrected": len(corrected_values),
            "paired": sum(r["change_corrected_minus_raw"] is not None for r in compared),
        },
        "matched_pixel_p95_assessed_pairs": len(compared),
        "signal_diagnostics": _signal_diagnostics(raw, corrected, reader),
        "assessment": "review" if compared else "not_assessed",
        "evaluation_selection": selection,
        "raw": before,
        "corrected": after,
        "absolute_intensity_preservation_validated": False,
        "interpretation": "Overlap agreement alone does not establish correct biological intensities",
    }
