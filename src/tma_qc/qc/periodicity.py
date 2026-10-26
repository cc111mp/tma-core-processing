"""Tile-pitch projection diagnostics with raw-defined, coverage-aware support.

Harmonic fractions describe a projection fit, not GRID probability. Biological
patterns and missing/uneven support can mimic periodic illumination.
"""

from __future__ import annotations

import numpy as np

from ..io.coordinates import overlap_pairs
from ..io.images import covered_sample


def _pitch(positions, tile_shape, axis):
    nearest = {}
    other = "y" if axis == "x" else "x"
    for a, b, _ in overlap_pairs(positions, tile_shape):
        distance = abs(a[axis] - b[axis])
        if distance <= 2 * abs(a[other] - b[other]):
            continue
        for p in (a, b):
            nearest[p["id"]] = min(nearest.get(p["id"], distance), distance)
    values = list(nearest.values())
    if not values:
        return None
    pitch = float(np.median(values))
    if np.percentile(np.abs(np.asarray(values) - pitch), 90) > 0.1 * pitch:
        return None
    return pitch


def _fit(values, coordinates, counts, pitch):
    # Remove the DC level before least squares. Otherwise a large constant
    # intensity can leave numerical residuals that look like periodic variation.
    values = values - values[0] if np.ptp(values) == 0 else values - values.mean()
    signal_scale = float(np.max(np.abs(values)))
    if signal_scale:
        values = values / signal_scale
    weights = np.sqrt(counts / counts.max())
    x = (coordinates - coordinates.mean()) / max(float(np.ptp(coordinates)), 1.0)
    baseline = np.column_stack([np.ones(len(x)), x])
    terms = [baseline]
    for harmonic in range(1, 4):
        phase = 2 * np.pi * harmonic * coordinates / pitch
        terms.extend([np.cos(phase)[:, None], np.sin(phase)[:, None]])
    design = np.column_stack(terms)
    weighted_design = design * weights[:, None]
    if (
        np.linalg.matrix_rank(weighted_design) < design.shape[1]
        or np.linalg.cond(weighted_design) > 1e8
    ):
        return None
    y = values * weights
    base_fit = np.linalg.lstsq(baseline * weights[:, None], y, rcond=None)[0]
    fit = np.linalg.lstsq(weighted_design, y, rcond=None)[0]
    original = float(np.sum((y - (baseline @ base_fit) * weights) ** 2))
    remaining = float(np.sum((y - (design @ fit) * weights) ** 2))
    # Compare residual energies in normalized coordinates. A fixed count-unit
    # floor otherwise reports zero harmonic fraction after uniform compression.
    roundoff = np.finfo(np.float64).eps * float(np.sum(y**2))
    fraction = float(np.clip(1 - remaining / original, 0, 1)) if original > roundoff else 0.0
    return {
        "harmonic_fraction_of_detrended_projection": fraction,
        "harmonic_amplitudes_counts": [
            float(np.hypot(*fit[i : i + 2]) * signal_scale) for i in (2, 4, 6)
        ],
    }


def _native_projections(raw, corrected, coverage, threshold):
    """Accumulate all native pixels using bounded patches and O(height + width) storage."""
    height, width = raw.shape
    projections = {}
    for axis, length, orthogonal in (("x", width, height), ("y", height, width)):
        bounds = np.linspace(0, orthogonal, 5, dtype=int)
        regions = [(0, orthogonal), *zip(bounds[:-1], bounds[1:])]
        projections[axis] = [
            {
                "scope": "global" if i == 0 else "strip",
                "strip_index": None if i == 0 else i,
                "orthogonal_range": (int(start), int(end)),
                "counts": np.zeros((2, length), np.int64),
                "sums": np.zeros((2, 2, length), np.float64),
            }
            for i, (start, end) in enumerate(regions)
        ]
    # No full-size floating image or semantic mask is constructed. Native
    # profiles retain every phase: preview decimation cannot alias the fit.
    patch_size = 512
    for y0 in range(0, height, patch_size):
        for x0 in range(0, width, patch_size):
            y1, x1 = min(y0 + patch_size, height), min(x0 + patch_size, width)
            a = np.asarray(raw[y0:y1, x0:x1], np.float64)
            b = np.asarray(corrected[y0:y1, x0:x1], np.float64)
            valid = np.asarray(coverage[y0:y1, x0:x1], bool) & np.isfinite(a) & np.isfinite(b)
            masks = (valid & (a > threshold), valid & (a <= threshold))
            for axis, dimension, lower, upper, other_lower, other_upper in (
                ("x", 0, x0, x1, y0, y1),
                ("y", 1, y0, y1, x0, x1),
            ):
                for region in projections[axis]:
                    start, end = region["orthogonal_range"]
                    begin, stop = max(start, other_lower), min(end, other_upper)
                    if begin >= stop:
                        continue
                    selected = (
                        np.s_[begin - y0 : stop - y0, :]
                        if axis == "x"
                        else np.s_[:, begin - x0 : stop - x0]
                    )
                    for stratum, mask in enumerate(masks):
                        supported = mask[selected]
                        region["counts"][stratum, lower:upper] += supported.sum(axis=dimension)
                        for label, image in enumerate((a, b)):
                            region["sums"][stratum, label, lower:upper] += np.sum(
                                image[selected], axis=dimension, where=supported
                            )
    return projections


def _profile_preview(profiles, coordinates, counts, usable, size):
    """Average adjacent native bins for display only; never bridge a support gap."""
    step = max(1, int(np.ceil(len(coordinates) / size)))
    starts = np.arange(0, len(coordinates), step)
    lengths = np.minimum(step, len(coordinates) - starts)
    weights = np.add.reduceat(counts, starts)
    valid = np.add.reduceat(usable.astype(int), starts) == lengths
    averages = [
        np.divide(
            np.add.reduceat(profile * counts, starts),
            weights,
            out=np.zeros(len(starts)),
            where=weights > 0,
        )
        for profile in profiles
    ]
    return {
        "preview_bin_width_native_pixels": step,
        "coordinate_native_pixels": (starts + (lengths - 1) / 2).tolist(),
        "support_sample_counts": weights.tolist(),
        "raw_profile_counts": [float(v) if ok else None for v, ok in zip(averages[0], valid)],
        "corrected_profile_counts": [float(v) if ok else None for v, ok in zip(averages[1], valid)],
    }


def _measurement(axis, stratum, counts, sums, pitch, region, shape, size):
    coordinates = np.arange(len(counts))
    usable = counts >= 8
    profiles = [
        np.divide(values, counts, out=np.zeros(len(counts)), where=counts > 0) for values in sums
    ]
    reason = None
    if pitch is None:
        reason = "No sufficiently regular pitch from overlapping tile neighbours"
    elif pitch < 8:
        reason = "Tile pitch is undersampled at native resolution"
    elif usable.sum() < 32:
        reason = "Insufficient supported projection bins"
    elif np.ptp(coordinates[usable]) < 2 * pitch:
        reason = "Fewer than two supported tile periods"
    start, end = region["orthogonal_range"]
    height, width = shape
    row = {
        "axis": axis,
        "stratum": stratum,
        "scope": region["scope"],
        "strip_index": region["strip_index"],
        "region_bbox_xyxy": [0, start, width, end] if axis == "x" else [start, 0, end, height],
        "tile_pitch_native_pixels": pitch,
        "assessment": "not_assessed" if reason else "review",
        "reason": reason,
        "supported_bins": int(usable.sum()),
        "total_bins": len(counts),
        **_profile_preview(profiles, coordinates, counts, usable, size),
        "raw": None,
        "corrected": None,
    }
    if reason is None:
        for name, profile in zip(("raw", "corrected"), profiles):
            row[name] = _fit(profile[usable], coordinates[usable], counts[usable], pitch)
        if row["raw"] is None or row["corrected"] is None:
            row.update(
                assessment="not_assessed",
                reason="Supported bins do not resolve independent tile phases",
                raw=None,
                corrected=None,
            )
    return row


def evaluate_periodicity(raw, corrected, coverage, positions, tile_shape, size=1024):
    """Fit tile-pitch harmonics to native global and local intensity projections.

    Raw/corrected mosaics share coverage and raw-defined signal strata. Geometry
    supplies pitch in native pixels. Fits report amplitudes in counts and the
    fraction of detrended variation explained by three harmonics. ``size`` only
    limits serialized display profiles; fits always use native bins. Unsupported
    or ambiguous layouts abstain, and fitted patterns require anatomical review.
    """
    if raw.ndim != 2 or corrected.shape != raw.shape or coverage.shape != raw.shape:
        raise ValueError("Periodicity QC requires matching 2D reconstructions and coverage")
    if type(size) is not int or size < 1:
        raise ValueError("Projection preview size must be a positive integer")
    # This bounded raw sample selects an advisory intensity stratum only. The
    # harmonic fits below use all finite, acquired native pixels in that stratum.
    sample, reference_sampling = covered_sample(raw, coverage, 1024)
    if not len(sample):
        raise ValueError("Periodicity QC has no finite acquired samples")
    lo, hi = np.percentile(sample, [1, 99])
    threshold = float(lo + 0.12 * max(hi - lo, 1.0))
    projections = _native_projections(raw, corrected, coverage, threshold)
    measurements, local = [], []
    for axis in ("x", "y"):
        pitch = _pitch(positions, tile_shape, axis)
        for stratum_index, stratum in enumerate(("raw_supported", "low_signal")):
            for region in projections[axis]:
                row = _measurement(
                    axis,
                    stratum,
                    region["counts"][stratum_index],
                    region["sums"][stratum_index],
                    pitch,
                    region,
                    raw.shape,
                    size,
                )
                (measurements if region["scope"] == "global" else local).append(row)
    strongest = []
    for axis in ("x", "y"):
        for stratum in ("raw_supported", "low_signal"):
            for label in ("raw", "corrected"):
                available = [
                    r
                    for r in local
                    if r["axis"] == axis and r["stratum"] == stratum and r[label] is not None
                ]
                if not available:
                    continue
                row = max(available, key=lambda r: max(r[label]["harmonic_amplitudes_counts"]))
                strongest.append(
                    {
                        key: row[key]
                        for key in (
                            "axis",
                            "stratum",
                            "strip_index",
                            "region_bbox_xyxy",
                            "tile_pitch_native_pixels",
                            "assessment",
                            "supported_bins",
                        )
                    }
                    | {"image": label, "fit": row[label]}
                )
    return {
        "assessment": "review"
        if any(r["assessment"] == "review" for r in measurements + local)
        else "not_assessed",
        "sampling_step_native_pixels": 1,
        "fit_sampling": "Every supported native row/column bin; preview averaging is display only",
        "working_patch_shape_yx": [512, 512],
        "raw_support_threshold_counts": threshold,
        "support_threshold_source": "Bounded raw-only percentile sample; identical native pixel masks for raw and corrected",
        "raw_reference_sampling": reference_sampling,
        "raw_reference_samples": len(sample),
        "minimum_samples_per_bin": 8,
        "harmonics": 3,
        "sampling_rules": {
            "maximum_pitch_relative_deviation_p90": 0.1,
            "minimum_supported_bins": 32,
            "minimum_supported_periods": 2,
            "minimum_native_samples_per_period": 8,
            "maximum_design_condition": 1e8,
            "orthogonal_strip_count": 4,
        },
        "measurements": measurements,
        "local_measurements": local,
        "strongest_local_patterns": strongest,
        "rules_calibrated": False,
        "grid_probability": None,
        "artifact_area_fraction": None,
        "interpretation": "Mask-normalized native intensity projections fitted at the estimated tile pitch and three harmonics after linear detrending. Global fits and four orthogonal strips are retained; low scores cannot exclude patterns within a strip or at other periods. Fractions measure projection fit, not artifact probability or biological fidelity. Low signal is not confirmed background; missing native bins are excluded, not zero-filled or interpolated. Preview bins containing a support gap are hidden.",
    }
