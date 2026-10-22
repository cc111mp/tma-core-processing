from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

import cv2
import numpy as np

from ..io.coordinates import overlap_pairs
from ..io.images import read_image
from ..io.tile_cache import file_identity


def relative_mean_difference(a, b):
    """Scale-invariant mean mismatch; two zero means have no relative scale."""
    scale = max(abs(a), abs(b))
    return abs(a - b) / scale if scale else None


def _selection_reason(samples, correlation):
    if samples < 256:
        return "insufficient_raw_support"
    if correlation is None:
        return "raw_correlation_unavailable"
    if correlation < 0.8:
        return "raw_correlation_below_selection"
    return None


def _directional_rank(image):
    """A necessary local direction check, not proof of unique translation."""
    image = np.asarray(image, np.float64)
    dx = image[:-1, 1:] - image[:-1, :-1]
    dy = image[1:, :-1] - image[:-1, :-1]
    scale = max(float(np.max(np.abs(dx))), float(np.max(np.abs(dy))))
    if not scale:
        return 0
    gradients = np.column_stack((dx.ravel() / scale, dy.ravel() / scale))
    return int(np.linalg.matrix_rank(gradients))


def _translation_symmetry(image, radius=2):
    """Find exact invariance on a translated overlap within a bounded radius.

    Only half the integer-shift square is needed because equality is symmetric.
    Untested larger translations and approximate/repetitive patterns can still
    be ambiguous when this exact local probe finds nothing.
    """
    height, width = image.shape
    for dy in range(radius + 1):
        for dx in range(-radius, radius + 1):
            if dy == 0 and dx <= 0:
                continue
            x0, x1 = max(0, -dx), min(width, width - dx)
            a = image[: height - dy, x0:x1]
            b = image[dy:, x0 + dx : x1 + dx]
            # Exact invariance requires these anchors too. Random texture can
            # reject most candidates without allocating a patch comparison.
            if a[0, 0] != b[0, 0] or a[-1, -1] != b[-1, -1]:
                continue
            if np.array_equal(a, b):
                return [dx, dy]
    return None


class OverlapMeasurements:
    """Per-core raw geometry measurements, keyed by inputs and sampling.

    No tissue masks or corrected-image measurements are retained. A skipped or
    resumed geometry QC stage simply leaves this cache empty; evaluation still
    computes everything it needs from the current raw inputs.
    """

    def __init__(self):
        self.identity = None
        self.edges = {}
        self.hits = 0

    def prepare(self, frames, positions, max_sample):
        identity = (
            tuple(
                (key, str(Path(path).resolve()), file_identity(Path(path)))
                for key, path in frames.items()
            ),
            tuple((p["id"], p["x"], p["y"]) for p in positions),
            max_sample,
        )
        if identity != self.identity:
            self.edges.clear()
            self.identity = identity
        return self.edges

    def clear(self):
        self.identity = None
        self.edges.clear()


def evaluate_overlaps(
    frames,
    positions,
    max_sample=512,
    masks=None,
    eligible=None,
    *,
    reader=None,
    measurements=None,
    exclusion_reasons=None,
):
    reader = reader or read_image
    reused = measurements.prepare(frames, positions, max_sample) if measurements is not None else {}
    shape = reader(next(iter(frames.values()))).shape
    edges = []
    neighbors = {r["id"]: set() for r in positions}
    for a, b, (x0, y0, x1, y1) in overlap_pairs(positions, shape):
        neighbors[a["id"]].add(b["id"])
        neighbors[b["id"]].add(a["id"])
        ia, ib = reader(frames[a["id"]]), reader(frames[b["id"]])
        aa = np.asarray(ia[y0 - a["y"] : y1 - a["y"], x0 - a["x"] : x1 - a["x"]], np.float32)
        bb = np.asarray(ib[y0 - b["y"] : y1 - b["y"], x0 - b["x"] : x1 - b["x"]], np.float32)
        step = max(1, int(np.ceil(max(aa.shape) / max_sample)))
        aa, bb = aa[::step, ::step], bb[::step, ::step]
        pair = (a["id"], b["id"])
        base = reused.get(pair)
        if base is None:
            corr = None
            shift = None
            response = None
            ranks = {"a": None, "b": None}
            symmetry = {
                "performed": False,
                "radius_sample_pixels": 2,
                "sampling_step_native_pixels": step,
                "shift_sample_xy": {"a": None, "b": None},
            }
            reason = "overlap_too_small" if min(aa.shape) < 8 else "insufficient_overlap_variation"
            if (
                min(aa.shape) >= 8
                and min(float(np.std(aa, dtype=np.float64)), float(np.std(bb, dtype=np.float64)))
                > 1e-6
            ):
                corr = float(np.clip(np.corrcoef(aa.ravel(), bb.ravel())[0, 1], -1, 1))
                corr = corr if np.isfinite(corr) else None
                ranks = {"a": _directional_rank(aa), "b": _directional_rank(bb)}
                reason = "directional_texture_degenerate"
                if min(ranks.values()) == 2:
                    symmetry["performed"] = True
                    symmetry["shift_sample_xy"] = {
                        "a": _translation_symmetry(aa),
                        "b": _translation_symmetry(bb),
                    }
                    reason = "translation_texture_degenerate"
                if min(ranks.values()) == 2 and not any(symmetry["shift_sample_xy"].values()):
                    # Windowing an uncentred image turns its DC intensity into a
                    # shared spatial pattern that can dominate weak real texture.
                    phase_a, phase_b = aa.astype(np.float64), bb.astype(np.float64)
                    phase_a -= phase_a.mean()
                    phase_b -= phase_b.mean()
                    phase_a /= np.max(np.abs(phase_a))
                    phase_b /= np.max(np.abs(phase_b))
                    win = cv2.createHanningWindow((aa.shape[1], aa.shape[0]), cv2.CV_64F)
                    delta, response = cv2.phaseCorrelate(phase_a, phase_b, win)
                    shift = [float(v * step) for v in delta]
                    response = float(response) if np.isfinite(response) else None
                    reason = None
                    if not all(np.isfinite(shift)) or response is None:
                        shift = None
                        reason = "nonfinite_phase_measurement"
            ma, mb = float(np.mean(aa, dtype=np.float64)), float(np.mean(bb, dtype=np.float64))
            base = {
                "a": a["id"],
                "b": b["id"],
                "overlap_pixels": (x1 - x0) * (y1 - y0),
                "pearson": corr,
                "phase_residual_native_pixels": shift,
                "phase_response": response,
                "mean_a": ma,
                "mean_b": mb,
                "relative_mean_difference": relative_mean_difference(ma, mb),
                "qualification": "Diagnostic; numerical rank and bounded exact-symmetry checks do not prove unique correspondence. Larger-period and near-periodic texture can remain ambiguous.",
                "coordinate_space": "native_af_pixels",
                "overlap_bbox_xyxy": [x0, y0, x1, y1],
                "sampling_step_native_pixels": step,
                "geometry_assessment": "review" if shift is not None else "not_assessed",
                "directional_texture_rank": ranks,
                "translation_symmetry_probe": symmetry,
                "geometry_exclusion_reason": reason,
                "phase_preparation": "Mean-centred, amplitude-normalized float64 overlaps before Hanning windowing",
            }
            if measurements is not None:
                reused[pair] = deepcopy(base)
        elif measurements is not None:
            measurements.hits += 1
        region_mask = masks.get((a["id"], b["id"])) if masks is not None else None
        tissue_difference = None
        tissue_pixels = int(np.count_nonzero(region_mask)) if region_mask is not None else 0
        tissue_corr = None
        absolute_median = absolute_p95 = None
        if tissue_pixels >= 32:
            pa, pb = aa[region_mask], bb[region_mask]
            va, vb = float(np.std(pa, dtype=np.float64)), float(np.std(pb, dtype=np.float64))
            if va > 1e-6 and vb > 1e-6:
                tissue_corr = float(np.clip(np.corrcoef(pa, pb)[0, 1], -1, 1))
                tissue_corr = tissue_corr if np.isfinite(tissue_corr) else None
            tma, tmb = float(np.mean(pa, dtype=np.float64)), float(np.mean(pb, dtype=np.float64))
            tissue_difference = relative_mean_difference(tma, tmb)
            absolute = np.abs(pa.astype(np.float64) - pb.astype(np.float64))
            absolute_median, absolute_p95 = [float(v) for v in np.percentile(absolute, [50, 95])]
        reason = _selection_reason(tissue_pixels, tissue_corr)
        if eligible is not None:
            included = bool(eligible.get(pair, False))
            reason = (
                None
                if included
                else (exclusion_reasons or {}).get(pair, "excluded_by_raw_selection")
            )
        else:
            included = reason is None
        edge = deepcopy(base)
        edge.update(
            raw_supported_evaluation_pair=included,
            raw_support_sample_pixels=tissue_pixels,
            support_pearson=tissue_corr,
            support_relative_mean_difference=tissue_difference,
            support_absolute_difference_median_counts=absolute_median,
            support_absolute_difference_p95_counts=absolute_p95,
            photometric_assessment="review" if included else "not_assessed",
            photometric_exclusion_reason=reason,
        )
        edges.append(edge)
    remaining = set(neighbors)
    components = 0
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            for n in neighbors[stack.pop()] & remaining:
                remaining.remove(n)
                stack.append(n)
    return {
        "overlapping_pairs": len(edges),
        "geometric_components": components,
        "edges": edges,
        "geometry_assessed_pairs": sum(
            e["phase_residual_native_pixels"] is not None for e in edges
        ),
        "geometry_candidate_pairs": len(edges),
        "geometry_assessed_pair_fraction": sum(
            e["phase_residual_native_pixels"] is not None for e in edges
        )
        / len(edges)
        if edges
        else None,
        "geometry_exclusions_by_reason": dict(
            Counter(e["geometry_exclusion_reason"] for e in edges if e["geometry_exclusion_reason"])
        ),
        "geometry_assessment": "review"
        if any(e["phase_residual_native_pixels"] is not None for e in edges)
        else "not_assessed",
        "rules_calibrated": False,
    }


def raw_support_masks(frames, positions, max_sample=512, *, reader=None):
    """Freeze an advisory overlap-evaluation subset from raw counts only.

    This is not a tissue segmentation or a denominator for artifact-area ratios.
    """
    reader = reader or read_image
    samples = [np.asarray(reader(path)[::32, ::32]).ravel() for path in frames.values()]
    values = np.concatenate(samples)
    low, high = np.percentile(values, [1, 99])
    threshold = float(low + 0.12 * max(high - low, 1.0))
    shape = reader(next(iter(frames.values()))).shape
    masks = {}
    for a, b, (x0, y0, x1, y1) in overlap_pairs(positions, shape):
        ia, ib = reader(frames[a["id"]]), reader(frames[b["id"]])
        aa = np.asarray(ia[y0 - a["y"] : y1 - a["y"], x0 - a["x"] : x1 - a["x"]], np.float32)
        bb = np.asarray(ib[y0 - b["y"] : y1 - b["y"], x0 - b["x"] : x1 - b["x"]], np.float32)
        step = max(1, int(np.ceil(max(aa.shape) / max_sample)))
        masks[a["id"], b["id"]] = (cv2.GaussianBlur(aa, (0, 0), 2)[::step, ::step] > threshold) & (
            cv2.GaussianBlur(bb, (0, 0), 2)[::step, ::step] > threshold
        )
    return masks, {
        "raw_threshold_counts": threshold,
        "minimum_support_samples": 256,
        "minimum_raw_support_pearson": 0.8,
        "rules_calibrated": False,
        "scope": "Raw-defined subset for photometric comparison; not a tissue mask or independent registration validation",
    }
