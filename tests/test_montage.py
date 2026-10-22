"""Tests for previews and montages. Skipped if the image libraries are not installed."""

import shutil
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    import tifffile
    from PIL import Image

    from tma_qc.montage import run_montage
    from tma_qc.preview import cached_preview, read_preview
except ImportError:  # plain Python without numpy/tifffile/Pillow
    np = None

from tma_qc.report import load_settings

SETTINGS = load_settings(Path(__file__).resolve().parent.parent / "settings.toml")


@unittest.skipIf(np is None, "image libraries not installed (see requirements-image.txt)")
class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        rng = np.random.default_rng(0)
        self.gray = rng.integers(0, 60000, (1000, 900), dtype=np.uint16)
        self.rgb = rng.integers(0, 255, (1000, 1000, 3), dtype=np.uint8)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_uncompressed_like_stitched_af(self):
        path = self.tmp / "af.tif"
        tifffile.imwrite(path, self.gray, rowsperstrip=self.gray.shape[0])
        np.testing.assert_array_equal(read_preview(path, 100), self.gray[::10, ::10])

    def test_lzw_one_row_per_strip_like_registered(self):
        path = self.tmp / "he.tif"
        tifffile.imwrite(path, self.rgb, compression="lzw", rowsperstrip=1, photometric="rgb")
        np.testing.assert_array_equal(read_preview(path, 250), self.rgb[::4, ::4])

    def test_lzw_several_rows_per_strip(self):
        path = self.tmp / "af_lzw.tif"
        tifffile.imwrite(path, self.gray, compression="lzw", rowsperstrip=16)
        np.testing.assert_array_equal(read_preview(path, 100), self.gray[::10, ::10])

    def test_tiled_or_planar_without_pyramid_is_refused(self):
        # Bug found 2026-10-13: tiled crashed, planar gave a wrong picture.
        tiled = self.tmp / "tiled.tif"
        tifffile.imwrite(tiled, self.rgb, tile=(256, 256), photometric="rgb")
        planar = self.tmp / "planar.tif"
        tifffile.imwrite(
            planar, self.rgb.transpose(2, 0, 1), photometric="rgb", planarconfig="separate"
        )
        for path in (tiled, planar):
            with self.assertRaisesRegex(ValueError, "unsupported TIFF layout"):
                read_preview(path, 100)

    def test_pyramid_uses_a_small_level(self):
        path = self.tmp / "pyramid.tif"
        with tifffile.TiffWriter(path) as tif:
            tif.write(self.rgb, tile=(256, 256), photometric="rgb", subifds=1)
            tif.write(self.rgb[::4, ::4], tile=(128, 128), photometric="rgb", subfiletype=1)
        np.testing.assert_array_equal(read_preview(path, 200), self.rgb[::4, ::4])
        self.assertEqual(read_preview(path, 100).shape, (125, 125, 3))

    def test_cache_returns_same_preview(self):
        path = self.tmp / "af.tif"
        tifffile.imwrite(path, self.gray)
        first = cached_preview(path, 100, self.tmp / "cache")
        second = cached_preview(path, 100, self.tmp / "cache")
        np.testing.assert_array_equal(first, second)
        self.assertEqual(len(list((self.tmp / "cache").glob("*.npy"))), 1)


@unittest.skipIf(np is None, "image libraries not installed (see requirements-image.txt)")
class MontageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_montage_places_cores_on_grid(self):
        slide = self.tmp / "data" / "D001Ab01_01_CQ_UV_Reg"
        yy, xx = np.mgrid[:400, :400]
        disk = (yy - 200) ** 2 + (xx - 200) ** 2 < 150**2
        for core in ("A1", "A3", "B2"):
            reg = slide / core / "Registration"
            reg.mkdir(parents=True)
            tifffile.imwrite(
                reg / f"D001_{core}_AF_Reg.tif",
                (disk * 5000).astype(np.uint16),
                compression="lzw",
                rowsperstrip=1,
            )
            he = np.full((400, 400, 3), 245, np.uint8)
            he[disk] = (150, 60, 140)
            tifffile.imwrite(
                reg / f"{core}_Stain_Reg.tif",
                he,
                compression="lzw",
                rowsperstrip=1,
                photometric="rgb",
            )
        (slide / "B1").mkdir()  # core without registration

        settings = dict(SETTINGS, montage=dict(SETTINGS["montage"], tile_px=64))
        out = run_montage(self.tmp / "data", self.tmp / "out", settings)

        for suffix in ("AF", "HE", "overlay"):
            with Image.open(out / f"D001Ab01_01_CQ_UV_Reg_{suffix}.png") as img:
                # 3 columns x 2 rows of (64 + 2*3) wide, (64 + 2*3 + 16) high cells
                self.assertEqual(img.size, (3 * 70 + 8, 2 * 86 + 28 + 4))
        with Image.open(out / "D001Ab01_01_CQ_UV_Reg_overlay.png") as img:
            overlay = np.array(img)
        centre_a1 = overlay[28 + 16 + 3 + 32, 4 + 3 + 32]
        # Aligned tissue: AF (green) and HE (magenta = red + blue) both strong.
        red, green, blue = (int(v) for v in centre_a1)
        self.assertGreater(green, 200)
        self.assertGreater(min(red, blue), 150)
        self.assertEqual(red, blue)

    def test_cores_follow_settings_and_odd_names_are_skipped(self):
        slide = self.tmp / "data" / "S_Reg"
        for core in ("A1", "A0"):  # A0 matches core_pattern but has no column 0
            (slide / core).mkdir(parents=True)
        settings = dict(SETTINGS, montage=dict(SETTINGS["montage"], tile_px=32))
        out = run_montage(self.tmp / "data", self.tmp / "out", settings)
        with Image.open(out / "S_Reg_overlay.png") as img:
            self.assertEqual(img.size, (1 * 38 + 8, 1 * 54 + 28 + 4))  # only A1 drawn


if __name__ == "__main__":
    unittest.main()
