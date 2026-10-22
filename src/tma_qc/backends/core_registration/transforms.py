# Adapted from the local tma-core-registration research workflow (tmareg 0.1.0).
"""Explicit pixel-centre transforms and one-pass native resampling in bounded patches."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import tifffile
from scipy.ndimage import map_coordinates

from .images import transform_points


@dataclass
class Transform:
    he_to_af_view: np.ndarray
    af_view_to_native: np.ndarray
    he_view_to_native: np.ndarray
    pull_displacement: np.ndarray | None = None  # AF-view grid -> globally aligned HE-view grid.

    @classmethod
    def matrices_from_record(cls, record):
        if (
            record.get("direction_global") != "he_to_af"
            or record.get("research_coordinate_space", record.get("coordinate_space"))
            != "original_native_pixel_centres_xy"
        ):
            raise ValueError("Unsupported transform direction or coordinate convention")
        matrices = [
            np.asarray(record[key], np.float64)
            for key in ("affine_he_view_to_af_view", "af_view_to_native", "he_view_to_native")
        ]
        for matrix in matrices:
            if (
                matrix.shape != (3, 3)
                or not np.isfinite(matrix).all()
                or not np.allclose(matrix[2], [0, 0, 1])
                or np.linalg.det(matrix[:2, :2]) <= 1e-12
            ):
                raise ValueError("Invalid saved transform matrix")
        return matrices

    @classmethod
    def load(cls, path):
        """Reload a saved transform with the same coordinate and residual-map semantics."""
        # Callers have already frozen this pathname for hashing/source pins.
        # Do not follow a changed ancestor and substitute a different name here.
        path = Path(os.path.abspath(path))
        record = json.loads(path.read_text())
        matrices = cls.matrices_from_record(record)
        field = None
        if name := record.get("local_pull_displacement_file"):
            if not isinstance(name, str) or Path(name).is_absolute():
                raise ValueError(
                    "Residual field must be stored beside transform.json or inside its bundle"
                )
            field_path = Path(os.path.abspath(path.parent / name))
            if field_path == path.parent or not field_path.is_relative_to(path.parent):
                raise ValueError(
                    "Residual field must be stored beside transform.json or inside its bundle"
                )
            relative = field_path.relative_to(path.parent)
            components = [
                path.parent.joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts) + 1)
            ]
            if any(
                part.is_symlink() for part in components
            ) or not field_path.resolve().is_relative_to(path.parent.resolve()):
                raise ValueError(
                    "Residual field must remain inside the transform bundle; symlinks are not supported"
                )
            field = np.load(field_path, allow_pickle=False, mmap_mode="r")
            if not isinstance(field, np.ndarray):
                if hasattr(field, "close"):
                    field.close()
                raise ValueError(
                    "Residual displacement must be a floating NumPy array, not an archive"
                )
            if (
                field.dtype.kind != "f"
                or field.dtype.itemsize not in {4, 8}
                or field.ndim != 3
                or field.shape[2] != 2
            ):
                raise ValueError("Invalid residual displacement field")
            if min(field.shape[:2]) < 2 or (
                record.get("local_field_shape_yx") is not None
                and list(field.shape[:2]) != record["local_field_shape_yx"]
            ):
                raise ValueError("Residual field dimensions disagree with the saved AF-view grid")
            if max(field.shape[:2]) > 4096:
                raise ValueError("Residual field exceeds the supported 4096-pixel working grid")
            for y in range(0, field.shape[0], 128):
                if not np.isfinite(field[y : y + 128]).all():
                    raise ValueError("Nonfinite residual displacement field")
        return cls(*matrices, field)

    def validate_af_domain(self, shape):
        """Reject residual extrapolation beyond its declared working AF grid."""
        if self.pull_displacement is None:
            return
        height, width = shape[:2]
        corners = np.array(
            [[0, 0], [width - 1, 0], [0, height - 1], [width - 1, height - 1]], float
        )
        view = transform_points(np.linalg.inv(self.af_view_to_native), corners)
        fh, fw = self.pull_displacement.shape[:2]
        if (
            not np.isfinite(view).all()
            or np.any(view < -0.5 - 1e-7)
            or np.any(view[:, 0] > fw - 0.5 + 1e-7)
            or np.any(view[:, 1] > fh - 0.5 + 1e-7)
        ):
            raise ValueError("Residual AF working grid does not cover the actual native AF canvas")

    @property
    def affine_native(self):
        return self.af_view_to_native @ self.he_to_af_view @ np.linalg.inv(self.he_view_to_native)

    def displacement_at(self, points):
        if self.pull_displacement is None:
            return np.zeros_like(points, dtype=np.float64)
        points = np.asarray(points)
        coords = np.array([points[:, 1], points[:, 0]])
        return np.stack(
            [
                map_coordinates(
                    self.pull_displacement[..., i], coords, order=1, mode="nearest", prefilter=False
                )
                for i in range(2)
            ],
            axis=1,
        )

    def af_to_he(self, points):
        view = transform_points(np.linalg.inv(self.af_view_to_native), points)
        if self.pull_displacement is not None:
            height, width = self.pull_displacement.shape[:2]
            if (
                not np.isfinite(view).all()
                or np.any(view < -0.5 - 1e-7)
                or np.any(view[:, 0] > width - 0.5 + 1e-7)
                or np.any(view[:, 1] > height - 0.5 + 1e-7)
            ):
                raise ValueError("AF coordinates are outside the residual working-grid domain")
        view += self.displacement_at(view)
        return transform_points(self.he_view_to_native @ np.linalg.inv(self.he_to_af_view), view)

    def he_to_af(self, points):
        aligned = transform_points(
            self.he_to_af_view @ np.linalg.inv(self.he_view_to_native), points
        )
        if self.pull_displacement is None:
            return transform_points(self.af_view_to_native, aligned), np.ones(len(aligned), bool)
        # Invert the residual pull map for evaluation only. Failed inversions remain explicit.
        estimate = aligned.copy()
        for _ in range(100):
            update = aligned - self.displacement_at(estimate)
            difference = np.linalg.norm(update - estimate, axis=1)
            estimate = 0.5 * (estimate + update)
            if np.all(difference < 1e-6):
                break
        residual = np.linalg.norm(estimate + self.displacement_at(estimate) - aligned, axis=1)
        height, width = self.pull_displacement.shape[:2]
        inside = (
            (estimate[:, 0] >= -0.5)
            & (estimate[:, 0] <= width - 0.5)
            & (estimate[:, 1] >= -0.5)
            & (estimate[:, 1] <= height - 0.5)
        )
        return transform_points(self.af_view_to_native, estimate), (residual < 1e-3) & inside

    def record(self, field_file=None):
        return {
            "direction_global": "he_to_af",
            "coordinate_space": "original_native_pixel_centres_xy",
            "affine_he_to_af_native": self.affine_native.tolist(),
            "affine_he_view_to_af_view": self.he_to_af_view.tolist(),
            "af_view_to_native": self.af_view_to_native.tolist(),
            "he_view_to_native": self.he_view_to_native.tolist(),
            "local_pull_displacement_file": field_file,
            "local_field_shape_yx": None
            if self.pull_displacement is None
            else list(self.pull_displacement.shape[:2]),
            "local_field_domain": "AF-view pixel-centre support [-0.5,width-0.5] x [-0.5,height-0.5]; native AF canvas must be fully contained",
            "local_field_contract": (
                "At AF-view pixel q, sample globally aligned HE at q + displacement[q]. "
                "Field axes are Y,X,XY; values are AF-view pixels. Original HE is sampled by "
                "HE_view_to_native @ inverse(affine_HE_view_to_AF_view) @ (q + displacement[q])."
            ),
            "resampling": "native originals sampled once; intermediate warps are working views",
            "quality_status": "REVIEW",
        }


def sample_native(image, source_points, shape, fill):
    """Sample global source coordinates with bounded ROIs and stable interpolation bins."""
    points = np.asarray(source_points, np.float64).reshape(-1, 2)
    if len(points) != int(np.prod(shape)) or not np.isfinite(points).all():
        raise ValueError("Nonfinite or inconsistent native registration coordinates")
    height, width = image.shape[:2]
    valid = (
        (points[:, 0] >= 0)
        & (points[:, 0] <= width - 1)
        & (points[:, 1] >= 0)
        & (points[:, 1] <= height - 1)
    )
    channels = image.shape[2:]
    dtype = image.dtype.newbyteorder("=")
    interpolation_support = (
        (points[:, 0] >= -1)
        & (points[:, 0] <= width)
        & (points[:, 1] >= -1)
        & (points[:, 1] <= height)
    )
    if not interpolation_support.any():
        return np.full((*shape, *channels), fill, dtype=dtype), valid.reshape(shape)
    x0 = max(0, int(np.floor(points[:, 0].min())) - 2)
    x1 = min(width, int(np.ceil(points[:, 0].max())) + 3)
    y0 = max(0, int(np.floor(points[:, 1].min())) - 2)
    y1 = min(height, int(np.ceil(points[:, 1].max())) + 3)
    if max(x1 - x0, y1 - y0, *shape) >= 32767 or (x1 - x0) * (y1 - y0) > 4 * 1024**2:
        # A small output patch can inverse-map across a very large native source.
        # Split the output grid until OpenCV and source-ROI memory limits both hold.
        grid = points.reshape(*shape, 2)
        result = np.empty((*shape, *channels), dtype=dtype)
        if shape[1] >= shape[0] and shape[1] > 1:
            split = shape[1] // 2
            result[:, :split], _ = sample_native(image, grid[:, :split], (shape[0], split), fill)
            result[:, split:], _ = sample_native(
                image, grid[:, split:], (shape[0], shape[1] - split), fill
            )
        elif shape[0] > 1:
            split = shape[0] // 2
            result[:split], _ = sample_native(image, grid[:split], (split, shape[1]), fill)
            result[split:], _ = sample_native(
                image, grid[split:], (shape[0] - split, shape[1]), fill
            )
        else:
            raise ValueError("Native registration ROI exceeds the one-pixel interpolation limit")
        return result, valid.reshape(shape)
    source = np.asarray(image[y0:y1, x0:x1], dtype=dtype, order="C")
    bins = 1 << cv2.INTER_BITS
    qx = (
        np.rint(np.clip(points[:, 0].reshape(shape), x0 - 2, x1 + 1) * bins).astype(np.int64)
        - x0 * bins
    )
    qy = (
        np.rint(np.clip(points[:, 1].reshape(shape), y0 - 2, y1 + 1) * bins).astype(np.int64)
        - y0 * bins
    )
    maps = np.empty((*shape, 2), np.int16)
    maps[..., 0], maps[..., 1] = qx // bins, qy // bins
    fractions = (((qy & (bins - 1)) << cv2.INTER_BITS) | (qx & (bins - 1))).astype(np.uint16)
    border = tuple([float(fill)] * image.shape[2]) if channels else float(fill)
    result = cv2.remap(
        source,
        maps,
        fractions,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border,
    )
    if not np.isfinite(result).all():
        raise ValueError("Nonfinite registered H&E pixels")
    return result, valid.reshape(shape)


def export_native(image, shape, transform, out: Path, chunk=512):
    """H&E candidate and source-FOV coverage, never a verified tissue correspondence mask."""
    image_path, coverage_path = out / "he-registered.tif", out / "source-coverage.tif"
    temp_image = image_path.with_suffix(".partial.tif")
    temp_coverage = coverage_path.with_suffix(".partial.tif")
    result = coverage = None
    try:
        result = tifffile.memmap(
            temp_image,
            shape=(*shape[:2], *image.shape[2:]),
            dtype=image.dtype.newbyteorder("="),
            bigtiff=True,
            photometric="rgb" if image.ndim == 3 else "minisblack",
        )
        coverage = tifffile.memmap(temp_coverage, shape=shape[:2], dtype=np.uint8, bigtiff=True)
        fill = np.iinfo(image.dtype).max if image.dtype.kind == "u" else 1.0
        for y in range(0, shape[0], chunk):
            for x in range(0, shape[1], chunk):
                h, w = min(chunk, shape[0] - y), min(chunk, shape[1] - x)
                xx, yy = np.meshgrid(np.arange(x, x + w), np.arange(y, y + h))
                points = transform.af_to_he(np.column_stack([xx.ravel(), yy.ravel()]))
                pixels, valid = sample_native(image, points, (h, w), fill)
                result[y : y + h, x : x + w] = pixels
                coverage[y : y + h, x : x + w] = valid
        result.flush()
        coverage.flush()
    finally:
        for array in (result, coverage):
            if array is not None:
                array._mmap.close()
    os.replace(temp_image, image_path)
    os.replace(temp_coverage, coverage_path)
    return [image_path.name, coverage_path.name]
