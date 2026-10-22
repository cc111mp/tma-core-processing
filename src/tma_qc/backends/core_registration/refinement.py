# Adapted from the local tma-core-registration research workflow (tmareg 0.1.0).
"""Optional smooth, bounded B-spline/MI baseline; this is not VALIS or RegWSI."""

from __future__ import annotations

import cv2
import numpy as np
from scipy.ndimage import distance_transform_edt

from .images import warp
from .matching import BackendUnavailable


def deformation_metrics(field):
    # Native sampling interpolates this pull field bilinearly. Central finite
    # differences at nodes can miss reversed cells, especially after the support
    # taper. The bilinear Jacobian determinant is affine within each cell, so
    # checking all four corners gives its exact minimum over that cell.
    field = np.asarray(field)
    if (
        field.dtype.kind != "f"
        or field.ndim != 3
        or field.shape[2] != 2
        or min(field.shape[:2]) < 2
    ):
        raise ValueError("Deformation metrics require a finite floating Y,X,XY grid")
    # Keep derivative temporaries bounded even at the 4096-pixel working limit.
    minima = np.empty((field.shape[0] - 1, field.shape[1] - 1), np.float32)
    minimum, maximum_displacement, folded = np.inf, 0.0, 0
    for y in range(0, field.shape[0] - 1, 128):
        patch = np.asarray(field[y : y + 129], np.float64)
        if not np.isfinite(patch).all():
            raise ValueError("Deformation metrics require a finite floating Y,X,XY grid")
        a, b, c, d = patch[:-1, :-1], patch[:-1, 1:], patch[1:, :-1], patch[1:, 1:]
        dx0, dx1, dy0, dy1 = b - a, d - c, c - a, d - b
        block = np.full(a.shape[:2], np.inf)
        for dx, dy in ((dx0, dy0), (dx0, dy1), (dx1, dy0), (dx1, dy1)):
            jacobian = (1 + dx[..., 0]) * (1 + dy[..., 1]) - dx[..., 1] * dy[..., 0]
            np.minimum(block, jacobian, out=block)
        minima[y : y + block.shape[0]] = block
        minimum = min(minimum, float(block.min()))
        folded += int(np.count_nonzero(block <= 0))
        maximum_displacement = max(maximum_displacement, float(np.linalg.norm(patch, axis=2).max()))
    # The half-pixel boundary strips use nearest extension of the field. One
    # derivative vanishes there, so these strip determinants need separate checks.
    edge_jacobian = np.concatenate(
        [
            1 + np.diff(np.asarray(field[0, :, 0], np.float64)),
            1 + np.diff(np.asarray(field[-1, :, 0], np.float64)),
            1 + np.diff(np.asarray(field[:, 0, 1], np.float64)),
            1 + np.diff(np.asarray(field[:, -1, 1], np.float64)),
        ]
    )
    folded_cells = folded / minima.size
    folded_edges = float(np.mean(edge_jacobian <= 0))
    return {
        "minimum_jacobian": float(min(1.0, minimum, edge_jacobian.min())),
        "jacobian_p01": float(np.percentile(minima, 1)),
        "minimum_edge_strip_jacobian": float(edge_jacobian.min()),
        "nearest_corner_patch_jacobian": 1.0,
        "jacobian_sampling": "exact_minimum_over_bilinear_cell_corners_and_nearest_edge_strips",
        "jacobian_cells": int(minima.size),
        "maximum_displacement_view_pixels": maximum_displacement,
        "folded_grid_fraction": folded_cells,
        "folded_cell_fraction": folded_cells,
        "folded_edge_strip_fraction": folded_edges,
    }


def refine(fixed, moving, matrix, config):
    settings = config.refinement
    if settings.backend == "none":
        return None, {"status": "disabled", "backend": "none"}
    try:
        import SimpleITK as sitk
    except ImportError as error:
        raise BackendUnavailable(
            "Install the [registration-refine] extra for B-spline refinement"
        ) from error
    shape = fixed.gray.shape
    moved = warp(moving.gray, matrix, shape)
    moved_mask = warp(moving.mask, matrix, shape, nearest=True).astype(bool)
    support = fixed.mask & moved_mask
    support = cv2.erode(support.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    if support.sum() < 512:
        return None, {"status": "skipped", "reason": "insufficient_geometric_support"}
    fi = sitk.GetImageFromArray(fixed.gray.astype(np.float32))
    mo = sitk.GetImageFromArray(moved.astype(np.float32))
    mask = sitk.GetImageFromArray(support.astype(np.uint8))
    bspline = sitk.BSplineTransformInitializer(fi, [settings.mesh_size] * 2, order=3)
    registration = sitk.ImageRegistrationMethod()
    registration.SetNumberOfThreads(config.threads)
    registration.SetNumberOfWorkUnits(config.threads)
    registration.SetMetricAsMattesMutualInformation(32)
    registration.SetMetricFixedMask(mask)
    registration.SetMetricSamplingStrategy(registration.REGULAR)
    registration.SetMetricSamplingPercentage(0.3, config.seed)
    registration.SetInterpolator(sitk.sitkLinear)
    registration.SetInitialTransform(bspline)
    registration.SetOptimizerAsLBFGSB(
        numberOfIterations=settings.iterations,
        maximumNumberOfFunctionEvaluations=settings.iterations * 5,
        lowerBound=-settings.max_displacement_view_pixels / np.sqrt(2),
        upperBound=settings.max_displacement_view_pixels / np.sqrt(2),
    )
    registration.SetShrinkFactorsPerLevel([2, 1])
    registration.SetSmoothingSigmasPerLevel([1, 0])
    transform = registration.Execute(fi, mo)
    dense = sitk.TransformToDisplacementField(
        transform,
        sitk.sitkVectorFloat64,
        fi.GetSize(),
        fi.GetOrigin(),
        fi.GetSpacing(),
        fi.GetDirection(),
    )
    field = sitk.GetArrayFromImage(dense).astype(np.float32)
    taper = np.minimum(distance_transform_edt(support) / settings.support_taper_view_pixels, 1)
    field *= taper[..., None]
    if not np.isfinite(field).all():
        return None, {"status": "rejected", "reason": "nonfinite_deformation"}
    metrics = deformation_metrics(field)
    metrics.update(
        {
            "backend": "SimpleITK_bspline_Mattes_MI",
            "optimizer_stop": registration.GetOptimizerStopConditionDescription(),
            "support_is_verified_correspondence": False,
            "regularization": "coarse_Bspline_basis_bounded_coefficients_and_support_taper",
        }
    )
    if metrics["minimum_jacobian"] < settings.min_jacobian:
        return None, {**metrics, "status": "rejected", "reason": "invalid_or_folded_deformation"}
    if metrics["maximum_displacement_view_pixels"] > settings.max_displacement_view_pixels + 1e-4:
        return None, {**metrics, "status": "rejected", "reason": "excessive_displacement"}
    # Evaluate the same full-resolution MI objective before/after the FINAL tapered field.
    # This is a fitting diagnostic, never an independent accuracy measurement.
    evaluator = sitk.ImageRegistrationMethod()
    evaluator.SetNumberOfThreads(config.threads)
    evaluator.SetMetricAsMattesMutualInformation(32)
    evaluator.SetMetricFixedMask(mask)
    evaluator.SetInterpolator(sitk.sitkLinear)
    evaluator.SetInitialTransform(sitk.Transform(2, sitk.sitkIdentity))
    before = evaluator.MetricEvaluate(fi, mo)
    displacement = sitk.GetImageFromArray(field.astype(np.float64), isVector=True)
    evaluator.SetInitialTransform(sitk.DisplacementFieldTransform(displacement))
    after = evaluator.MetricEvaluate(fi, mo)
    metrics.update(
        {"fit_MI_cost_before": before, "fit_MI_cost_after": after, "independently_validated": False}
    )
    if not np.isfinite(after) or after >= before:
        return None, {**metrics, "status": "rejected", "reason": "fit_objective_did_not_improve"}
    return field, {**metrics, "status": "applied_experimental"}
