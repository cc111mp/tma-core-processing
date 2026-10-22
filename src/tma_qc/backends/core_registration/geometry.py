# Adapted from the local tma-core-registration research workflow (tmareg 0.1.0).
"""Weak shape initialization. Circular cores deliberately have no inferred orientation."""

from __future__ import annotations

import cv2
import numpy as np

from .images import transform_points


def centroid(mask):
    moments = cv2.moments(mask.astype(np.uint8), binaryImage=True)
    if moments["m00"] <= 0:
        raise ValueError("Cannot initialize geometry from an empty mask")
    return np.array([moments["m10"], moments["m01"]]) / moments["m00"]


def scale_estimate(fixed, moving):
    if fixed.mpp is not None and moving.mpp is not None:
        scale = (moving.mpp / fixed.mpp) * np.sqrt(
            np.linalg.det(moving.to_native[:2, :2]) / np.linalg.det(fixed.to_native[:2, :2])
        )
        return float(scale), "calibrated_isotropic_pixel_sizes"
    return float(np.sqrt(fixed.mask.sum() / moving.mask.sum())), "provisional_foreground_area"


def initialize(fixed, moving, angle):
    scale, _ = scale_estimate(fixed, moving)
    radians = np.deg2rad(angle)
    rotation = np.array([[np.cos(radians), -np.sin(radians)], [np.sin(radians), np.cos(radians)]])
    matrix = np.eye(3)
    matrix[:2, :2] = rotation * scale
    matrix[:2, 2] = centroid(fixed.mask) - matrix[:2, :2] @ centroid(moving.mask)
    return matrix


def spatial_coverage(points, mask):
    if len(points) < 3:
        return 0.0
    hull = cv2.convexHull(np.float32(points))
    hull_mask = np.zeros(mask.shape, np.uint8)
    cv2.fillConvexPoly(hull_mask, np.int32(np.rint(hull)), 1)
    return float(np.count_nonzero(hull_mask & mask) / max(1, mask.sum()))


def geometric_checks(matrix, expected_scale, settings):
    if matrix is None or np.shape(matrix) != (3, 3) or not np.isfinite(matrix).all():
        return ["invalid_transform"]
    linear = matrix[:2, :2]
    if np.linalg.det(linear) <= 0:
        return ["reflection_or_singular_transform"]
    singular = np.linalg.svd(linear, compute_uv=False)
    errors = []
    if singular[0] / singular[1] > settings.max_anisotropy:
        errors.append("excessive_anisotropy")
    relative = np.sqrt(np.linalg.det(linear)) / expected_scale
    if not settings.min_relative_scale <= relative <= settings.max_relative_scale:
        errors.append("implausible_relative_scale")
    return errors


def distinct_pose(a, b, moving_mask, threshold):
    ys, xs = np.nonzero(moving_mask)
    points = np.array(
        [
            [xs.min(), ys.min()],
            [xs.max(), ys.min()],
            [xs.min(), ys.max()],
            [xs.max(), ys.max()],
            centroid(moving_mask),
        ]
    )
    distance = np.linalg.norm(transform_points(a, points) - transform_points(b, points), axis=1)
    return bool(np.median(distance) > threshold)
