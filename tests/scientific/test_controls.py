"""Necessary controlled properties, not biological detector validation."""

import numpy as np
import pytest
from scipy import ndimage

from tma_qc.backends.blend import blend
from tma_qc.backends.cidre import apply_field
from tma_qc.io.images import read_image, write_tiff
from tma_qc.qc.focus import detail_metrics
from tma_qc.stages.registration import estimate_affine


def test_contrast_only_transform_preserves_normalized_detail():
    rng = np.random.default_rng(17)
    for _ in range(12):
        image = (
            ndimage.gaussian_filter(rng.random((128, 128)).astype(np.float32), 0.6) * 3000 + 5000
        )
        half = image.mean() + 0.5 * (image - image.mean())
        before = detail_metrics(image)
        after = detail_metrics(half)
        assert after["gradient_energy"] / before["gradient_energy"] == pytest.approx(0.25, rel=1e-5)
        assert after["gradient_over_variance"] / before["gradient_over_variance"] == pytest.approx(
            1.0, rel=1e-5
        )
    assert "not_blur_probability" in after["interpretation"]


def test_known_spatial_blur_response_is_separate_from_contrast_control():
    rng = np.random.default_rng(4)
    sharp = ndimage.gaussian_filter(rng.random((256, 256)).astype(np.float32), 0.7) * 4000 + 5000
    blurred = ndimage.gaussian_filter(sharp, 3.0)
    assert (
        detail_metrics(sharp)["gradient_over_variance"]
        > detail_metrics(blurred)["gradient_over_variance"] * 2
    )


def test_known_field_application_preserves_negative_and_high_counts():
    raw = np.array([[0, 100], [50000, 65535]], np.uint16)
    s = np.full((2, 2), 0.5, np.float32)
    d = np.full((2, 2), 100.0, np.float32)
    actual = apply_field(raw, s, d)
    np.testing.assert_array_equal(actual, [[-200.0, 0.0], [99800.0, 130870.0]])
    assert actual.dtype == np.float32
    for invalid in [np.zeros_like(s), -s, np.full_like(s, np.nan)]:
        with pytest.raises(ValueError):
            apply_field(raw, invalid, d)


def test_native_blend_preserves_counts_and_marks_unacquired_holes(tmp_path):
    a = np.full((8, 8), -20, np.float32)
    b = np.full((8, 8), 90000, np.float32)
    write_tiff(tmp_path / "a.tif", a)
    write_tiff(tmp_path / "b.tif", b)
    positions = [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 4, "y": 4}]
    blend(
        {"a": tmp_path / "a.tif", "b": tmp_path / "b.tif"},
        positions,
        tmp_path / "blend.tif",
        tmp_path / "coverage.tif",
        tmp_path / "cache",
        chunk_rows=3,
    )
    result = read_image(tmp_path / "blend.tif")
    coverage = read_image(tmp_path / "coverage.tif")
    assert result.dtype == np.float32
    assert result[0, 0] == -20
    assert result[11, 11] == 90000
    assert coverage[0, 11] == 0
    assert coverage[0, 0] == 1
    # Equal linear weights at this overlapping pixel give the independent mean.
    assert result[5, 6] == pytest.approx((-20 + 90000) / 2)


def test_affine_backend_reports_transform_in_native_pixels_across_preview_scales():
    import cv2

    rng = np.random.default_rng(42)
    af = ndimage.gaussian_filter(rng.uniform(2000, 20000, (500, 600)).astype(np.float32), 1.2)
    y, x = np.mgrid[:500, :600]
    af += 2000 * np.sin(x / 13) * np.cos(y / 19)
    true_matrix = cv2.getRotationMatrix2D((300, 250), 4, 1.02)
    true_matrix[:, 2] += [8, -5]
    true_matrix = np.vstack([true_matrix, [0, 0, 1]])
    he = cv2.warpAffine(
        af, np.linalg.inv(true_matrix)[:2], (600, 500), borderMode=cv2.BORDER_REFLECT
    )
    he = np.uint8(np.clip(255 - (he - 1000) / 100, 0, 255))
    he = np.stack([he, np.uint8(0.7 * he + 30), np.uint8(0.9 * he + 10)], axis=2)
    matrix, info = estimate_affine(
        af, he, {"max_dimension": 256, "min_matches": 12, "ransac_pixels": 3.0}
    )
    independent = np.array([[100, 100, 1], [500, 100, 1], [100, 400, 1], [500, 400, 1]])
    errors = np.linalg.norm(
        (independent @ matrix.T)[:, :2] - (independent @ true_matrix.T)[:, :2], axis=1
    )
    assert errors.max() < 1.0
    assert not info["fit_features_are_independent_validation"]
