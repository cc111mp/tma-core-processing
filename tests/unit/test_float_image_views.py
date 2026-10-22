"""Supported grayscale encodings must retain their visible and matching texture."""

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from tma_qc.io.images import display, match_image, save_preview, structural_gray, window
from tma_qc.stages.registration import estimate_affine


def textured_counts():
    random = np.random.default_rng(108).random((256, 256))
    return np.rint(1000 + gaussian_filter(random, 0.8) * 40000).astype(np.uint16)


def test_float64_preview_centres_before_narrowing_precision(tmp_path):
    image = textured_counts()
    shifted = image.astype(np.float64) + 1e12
    assert structural_gray(shifted).dtype == np.float64
    expected = display(image, window(image))
    actual = display(shifted, window(shifted))
    assert np.max(np.abs(actual.astype(int) - expected.astype(int))) <= 1
    path = tmp_path / "he-preview.png"
    save_preview(path, shifted)
    with Image.open(path) as preview:
        np.testing.assert_array_equal(np.asarray(preview), actual)


def test_tiny_float_display_ranges_remain_visible():
    reference = np.linspace(0, 1, 10000, dtype=np.float32).reshape(100, 100)
    tiny = reference * 1e-12
    actual = display(tiny, window(tiny))
    expected = display(reference, window(reference))
    assert np.max(np.abs(actual.astype(int) - expected.astype(int))) <= 1
    assert actual.max() == 255


def test_matching_views_preserve_large_offset_and_small_scale():
    image = textured_counts()
    expected = match_image(image)
    for encoded in [image.astype(np.float64) + 1e12, image.astype(np.float32) * 1e-12]:
        actual = match_image(encoded)
        assert np.max(np.abs(actual.astype(int) - expected.astype(int))) <= 1


def test_legacy_sift_recovers_high_offset_supported_float64_grayscale():
    image = textured_counts()
    matrix, diagnostics = estimate_affine(
        image,
        image.astype(np.float64) + 1e12,
        {"max_dimension": 256, "min_matches": 12, "ransac_pixels": 3.0},
    )
    np.testing.assert_allclose(matrix, np.eye(3), atol=0.05)
    assert diagnostics["fit_inliers"] >= 12
