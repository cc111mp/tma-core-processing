# Adapted from the local tma-core-registration research workflow (tmareg 0.1.0).
"""Geometry hypotheses -> correspondences -> robust global fitting; no evaluation labels."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .geometry import (
    distinct_pose,
    geometric_checks,
    initialize,
    scale_estimate,
    spatial_coverage,
)
from .images import feature_image, transform_points, warp
from .matching import Matches, foreground_matches, sparse_matches


@dataclass
class Candidate:
    angle: float
    matrix: np.ndarray | None = None  # Original moving VIEW -> original fixed VIEW.
    matches: Matches | None = None  # Original view coordinates, before geometry warping.
    inliers: np.ndarray | None = None
    metrics: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0

    def record(self):
        return {
            "rotation_hypothesis_degrees": self.angle,
            "matrix_he_view_to_af_view": None if self.matrix is None else self.matrix.tolist(),
            "fit_eligible": not self.reasons,
            "fit_checks": self.reasons,
            "score_for_initialization_only": self.score,
            **self.metrics,
        }


@dataclass
class GlobalResult:
    best: Candidate | None
    candidates: list[Candidate]
    ambiguous: bool
    scale_source: str


def fit_consensus(matches, expected_scale, fixed_mask, moving_mask, settings, seed):
    candidate = Candidate(0, matches=matches)
    candidate.metrics["fit_matches"] = len(matches.moving)
    if len(matches.moving) < settings.min_inliers:
        candidate.reasons = ["insufficient_matches"]
        return candidate
    cv2.setRNGSeed(seed)
    estimator = (
        cv2.estimateAffinePartial2D if settings.transform == "similarity" else cv2.estimateAffine2D
    )
    fitted, inliers = estimator(
        matches.moving,
        matches.fixed,
        method=cv2.RANSAC,
        ransacReprojThreshold=settings.ransac_view_pixels,
        maxIters=5000,
        confidence=0.999,
        refineIters=10,
    )
    if fitted is None or inliers is None:
        candidate.reasons = ["no_consensus"]
        return candidate
    if not np.isfinite(fitted).all():
        candidate.reasons = ["degenerate_consensus"]
        return candidate
    candidate.matrix = np.vstack([fitted, [0, 0, 1]])
    candidate.inliers = inliers.ravel().astype(bool)
    source = matches.moving[candidate.inliers]
    target = matches.fixed[candidate.inliers]
    errors = np.linalg.norm(transform_points(candidate.matrix, source) - target, axis=1)
    coverage_fixed = spatial_coverage(target, fixed_mask)
    coverage_moving = spatial_coverage(source, moving_mask)
    fraction = len(source) / len(matches.moving)
    candidate.metrics.update(
        {
            "fit_inliers": len(source),
            "fit_inlier_fraction": fraction,
            "fit_hull_coverage_af": coverage_fixed,
            "fit_hull_coverage_he": coverage_moving,
            "fit_median_residual_view_pixels": float(np.median(errors)),
            "fit_features_are_independent_validation": False,
        }
    )
    candidate.reasons = geometric_checks(candidate.matrix, expected_scale, settings)
    if len(source) < settings.min_inliers:
        candidate.reasons.append("insufficient_inliers")
    if fraction < settings.min_inlier_fraction:
        candidate.reasons.append("low_inlier_fraction")
    if min(coverage_fixed, coverage_moving) < settings.min_hull_coverage:
        candidate.reasons.append("clustered_spatial_support")
    candidate.score = float(
        len(source)
        * np.sqrt(coverage_fixed * coverage_moving)
        * np.sqrt(fraction)
        / (1 + np.median(errors) / settings.ransac_view_pixels)
    )
    return candidate


def register_global(fixed, moving, method, config, roma=None):
    settings = config.matching
    use_geometry = method.startswith("geometry_")
    rotations = settings.rotations if use_geometry else (0,)
    scale, scale_source = scale_estimate(fixed, moving)
    fixed_features = feature_image(fixed.gray, settings.feature_view)
    moving_features = feature_image(moving.gray, settings.feature_view)
    candidates = []
    for angle in rotations:
        start = time.perf_counter()
        initial = initialize(fixed, moving, angle) if use_geometry else np.eye(3)
        if use_geometry:
            source = warp(moving_features, initial, fixed.gray.shape)
            source_mask = warp(moving.mask, initial, fixed.gray.shape, nearest=True).astype(bool)
        else:
            source, source_mask = moving_features, moving.mask
        if method == "geometry_roma":
            if roma is None:
                raise ValueError("A RoMa matcher is required for geometry_roma")
            matches = roma.match(source, fixed_features, settings.max_matches)
        else:
            matches = sparse_matches(
                source, fixed_features, "orb" if method == "orb" else "sift", settings
            )
        matches = foreground_matches(matches, source_mask, fixed.mask, settings.max_matches)
        # Fit in ORIGINAL moving-view coordinates, retaining all crop/resize/geometry changes.
        matches = Matches(
            transform_points(np.linalg.inv(initial), matches.moving),
            matches.fixed,
            matches.confidence,
        )
        candidate = fit_consensus(matches, scale, fixed.mask, moving.mask, settings, config.seed)
        candidate.angle = float(angle)
        candidate.metrics["elapsed_seconds"] = time.perf_counter() - start
        candidates.append(candidate)
    eligible = sorted((c for c in candidates if not c.reasons), key=lambda c: c.score, reverse=True)
    best = eligible[0] if eligible else None
    ambiguous = False
    if best:
        for other in eligible[1:]:
            close_score = other.score >= best.score * (1 - settings.ambiguity_score_fraction)
            different = distinct_pose(
                best.matrix, other.matrix, moving.mask, settings.ambiguity_view_pixels
            )
            ambiguous |= close_score and different
    return GlobalResult(best, candidates, bool(ambiguous), scale_source)
