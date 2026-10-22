from pathlib import Path

import numpy as np
import pytest
import tifffile

from tma_qc.backends.warp import warp_region
from tma_qc.io.images import mapped_image, native_array


@pytest.mark.parametrize(
    "layout", ["gray", "rgb-strips", "rgb-tiles", "rgb-planar", "rgb-big-endian"]
)
def test_compressed_he_decodes_to_local_mapping_and_preserves_pixels(tmp_path, monkeypatch, layout):
    image = np.arange(32 * 48 * 3, dtype=np.uint16).reshape(32, 48, 3)
    kwargs = {"compression": "deflate", "photometric": "rgb"}
    if layout == "gray":
        image = image[..., 0]
        kwargs["photometric"] = "minisblack"
    elif layout == "rgb-tiles":
        kwargs["tile"] = 16, 16
    elif layout == "rgb-planar":
        image = np.moveaxis(image, -1, 0)
        kwargs["planarconfig"] = "separate"
    elif layout == "rgb-big-endian":
        kwargs["byteorder"] = ">"
    path = tmp_path / "he.tif"
    tifffile.imwrite(path, image, **kwargs)
    original = tifffile.TiffPageSeries.asarray
    calls = []

    def observe(series, **options):
        assert Path(options["out"]).is_relative_to(tmp_path / "scratch")
        assert options["maxworkers"] == 2
        calls.append(options)
        return original(series, **options)

    monkeypatch.setattr(tifffile.TiffPageSeries, "asarray", observe)
    expected = np.moveaxis(image, 0, -1) if layout == "rgb-planar" else image
    with mapped_image(path, tmp_path / "scratch", rgb=True) as mapped:
        assert isinstance(mapped, np.memmap)
        np.testing.assert_array_equal(native_array(mapped), expected)
    assert len(calls) == 1
    assert not list((tmp_path / "scratch").iterdir())


def test_oversized_decoding_segment_is_rejected_before_allocating(tmp_path, monkeypatch):
    path = tmp_path / "he.tif"
    tifffile.imwrite(path, np.ones((32, 48), np.uint16), compression="deflate", rowsperstrip=32)

    def should_not_decode(*args, **kwargs):
        pytest.fail("Oversized segment was decoded before validation")

    monkeypatch.setattr(tifffile.TiffPageSeries, "asarray", should_not_decode)
    with pytest.raises(ValueError, match="retile or re-strip"):
        with mapped_image(path, tmp_path / "scratch", max_segment_bytes=64):
            pass
    assert not list((tmp_path / "scratch").iterdir())


def test_native_roi_warp_respects_absolute_coordinates_and_byte_order():
    source = np.arange(70 * 90, dtype=np.uint16).reshape(70, 90).astype(">u2")
    matrix = np.array([[1.0, 0.0, 11.0], [0.0, 1.0, -9.0], [0.0, 0.0, 1.0]])
    result = warp_region(source, matrix, (30, 14, 35, 32), 65535)
    np.testing.assert_array_equal(result, source[23:55, 19:54])
    assert result.dtype.isnative


@pytest.mark.parametrize("working_pixels", [4 * 1024 * 1024, 2000])
def test_native_affine_warp_preserves_an_independent_linear_intensity_field(working_pixels):
    y, x = np.mgrid[:1200, :1200]
    source = (100 + 0.3 * x + 0.2 * y).astype(np.float32)
    matrix = np.array([[0.9, 0.15, 14.3], [-0.1, 1.1, 23.2], [0.0, 0.0, 1.0]])
    inverse = np.linalg.inv(matrix)
    for px, py in [(100, 100), (600, 600)]:
        yy, xx = np.mgrid[py : py + 112, px : px + 128]
        sx = inverse[0, 0] * xx + inverse[0, 1] * yy + inverse[0, 2]
        sy = inverse[1, 0] * xx + inverse[1, 1] * yy + inverse[1, 2]
        expected = 100 + 0.3 * sx + 0.2 * sy
        result = warp_region(
            source, matrix, (px, py, 128, 112), 1.0, max_source_pixels=working_pixels
        )
        # OpenCV linear interpolation quantizes the fractional position to 1/32 pixel.
        np.testing.assert_allclose(result, expected, rtol=0, atol=0.03)


def test_native_warp_handles_a_destination_wider_than_opencv_remap_limit():
    source = np.full((8, 16), 200, np.uint8)
    result = warp_region(source, np.eye(3), (0, 0, 32768, 8), 255)
    np.testing.assert_array_equal(result[:, :16], source)
    assert np.all(result[:, 16:] == 255)


def test_native_warp_is_identical_across_block_and_roi_partitions():
    rng = np.random.default_rng(23)
    source = rng.integers(0, 65535, (1000, 1000), dtype=np.uint16)
    matrix = np.array([[0.95, 0.07, 12.3], [-0.03, 1.07, 20.1], [0.0, 0.0, 1.0]])
    full = warp_region(source, matrix, (0, 0, 800, 800), 65535)
    tiled = np.empty_like(full)
    for y in range(0, 800, 200):
        for x in range(0, 800, 200):
            tiled[y : y + 200, x : x + 200] = warp_region(
                source, matrix, (x, y, 200, 200), 65535, max_source_pixels=12000
            )
    np.testing.assert_array_equal(full, tiled)


def test_he_finite_validation_includes_final_partial_native_chunk():
    from tma_qc.stages.registration import _validate_he

    image = np.full((1025, 1025), 100.0, np.float32)
    image[-1, -1] = np.nan
    with pytest.raises(ValueError, match="Nonfinite H&E"):
        _validate_he(image)
