import numpy as np
import pytest

from tma_qc.backends.blend import blend_pair
from tma_qc.io.images import read_image, write_tiff


@pytest.mark.parametrize("band_rows", [1, 7, 32, 200])
def test_paired_bands_match_independent_native_formula(tmp_path, band_rows):
    rng = np.random.default_rng(19)
    positions = [
        {"id": "a", "x": 0, "y": 0},
        {"id": "b", "x": 19, "y": 9},
        {"id": "c", "x": 6, "y": 18},
    ]
    raw, selected = {}, {}
    frames = {}
    for p in positions:
        a = rng.integers(0, 65536, (32, 46), dtype=np.uint16)
        b = a.astype(np.float32) * 2 - 3000
        frames[p["id"]] = a, b
        raw[p["id"]] = write_tiff(tmp_path / (p["id"] + ".tif"), a)
        selected[p["id"]] = write_tiff(tmp_path / (p["id"] + "-selected.tif"), b)
    # Full-canvas, independent reference with the original summation order.
    height = max(p["y"] for p in positions) + 32
    width = max(p["x"] for p in positions) + 46
    yy, xx = np.indices((32, 46))
    weight = (
        np.minimum(yy + 1, 32 - yy).astype(np.float64)
        * np.minimum(xx + 1, 46 - xx).astype(np.float64)
    ) ** 1.5
    sums = [np.zeros((height, width), np.float64) for _ in range(2)]
    denominator = np.zeros((height, width), np.float64)
    for p in positions:
        region = np.s_[p["y"] : p["y"] + 32, p["x"] : p["x"] + 46]
        denominator[region] += weight
        for n, image in enumerate(frames[p["id"]]):
            sums[n][region] += image.astype(np.float64) * weight
    present = denominator > 0
    expected = [
        np.divide(total, denominator, out=np.zeros_like(total), where=present).astype(np.float32)
        for total in sums
    ]
    a, b, ac, bc = [
        tmp_path / name for name in ("raw.tif", "selected.tif", "raw-cover.tif", "cover.tif")
    ]
    actual_shape = blend_pair(raw, selected, positions, a, b, ac, bc, chunk_rows=band_rows)
    assert actual_shape == (height, width)
    for output, reference in zip((a, b), expected):
        np.testing.assert_array_equal(read_image(output), reference)
    np.testing.assert_array_equal(read_image(ac), present.astype(np.uint8))
    np.testing.assert_array_equal(read_image(bc), present.astype(np.uint8))
    assert read_image(b).min() < 0 and read_image(b).max() > 65535


def test_paired_blend_rejects_hidden_nonfinite_source(tmp_path):
    raw = write_tiff(tmp_path / "input.tif", np.ones((32, 32), np.uint16))
    bad = np.ones((32, 32), np.float32)
    bad[-1, -1] = np.nan
    selected = write_tiff(tmp_path / "bad.tif", bad)
    with pytest.raises(ValueError, match="nonfinite FOV"):
        blend_pair(
            {"a": raw},
            {"a": selected},
            [{"id": "a", "x": 0, "y": 0}],
            *(tmp_path / name for name in ("out-a.tif", "out-b.tif", "cover-a.tif", "cover-b.tif")),
            chunk_rows=3,
        )
