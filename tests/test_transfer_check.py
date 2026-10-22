"""Tests for the transfer check, using small fake slides built on the fly.

Run from the repository folder:
    python -m unittest -v
"""

import csv
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tma_qc.raw_layout import Layout, analyse_grid, find_slides, read_slide
from tma_qc.report import ALERT, CHECK, OK, load_settings
from tma_qc.transfer_check import UNFINISHED, check_slide, run_compare, run_scan

SETTINGS = load_settings(Path(__file__).resolve().parent.parent / "settings.toml")
STEP_X = SETTINGS["grid"]["step_x_mm"]
STEP_Y = SETTINGS["grid"]["step_y_mm"]
LAMP = "middle lamp 275"


def grid_names(x0, y0, cols, rows):
    """FOV folder names for a full cols x rows grid starting at (x0, y0)."""
    return [
        f"{x0 + c * STEP_X:.6f}_{y0 + r * STEP_Y:.6f}" for c in range(cols) for r in range(rows)
    ]


def make_core(slide_dir, core, fov_names, stitch=True, images_per_fov=1, mtime=None):
    """Create <slide>/<core>/<lamp>/scan/<X_Y>/<image>.tiff like the real data."""
    lamp = Path(slide_dir, core, LAMP)
    for n, name in enumerate(fov_names):
        fov = lamp / "scan" / name
        fov.mkdir(parents=True)
        for k in range(images_per_fov):
            img = fov / f"0.6.{110 + k}.tiff"
            img.write_bytes(b"x" * 100)
            if mtime is not None:
                os.utime(img, (mtime + n, mtime + n))
        (fov / "Thumbs.db").write_bytes(b"ignored")
    if stitch:
        (lamp / "stitch").mkdir(parents=True)
        for n in range(len(fov_names)):
            (lamp / "stitch" / f"{n + 1:04d}_0.6.113.tiff").write_bytes(b"x")
    return lamp


class FakeData(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.layout = Layout()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def slide(self, name="D001Ab01_01_CQ_UV"):
        # Real data repeats the slide name one level down.
        path = self.tmp / "data" / name / name
        path.mkdir(parents=True)
        return path

    def rows_by_core(self, slide_dir, reference=None):
        level, problems, rows = check_slide(
            read_slide(slide_dir, self.layout), SETTINGS, reference or {}
        )
        return level, problems, {r["core"]: r for r in rows}


class GridTests(unittest.TestCase):
    def test_full_grid(self):
        g = analyse_grid(grid_names(43.591236, 42.357218, 7, 6), STEP_X, STEP_Y, 0.1)
        self.assertEqual((g.columns, g.rows, g.off_grid, g.missing), (7, 6, 0, 0))

    def test_real_positions_from_d049_a1(self):
        xs = [43.591236, 44.019186, 44.447136, 44.875086, 45.303036, 45.730986, 46.158936]
        ys = [42.357218, 42.788281, 43.219344, 43.650407, 44.08147, 44.512533, 44.943596]
        names = [f"{x}_{y}" for x in xs for y in ys]
        g = analyse_grid(names, STEP_X, STEP_Y, 0.1)
        self.assertEqual((g.columns, g.rows, g.off_grid, g.missing), (7, 7, 0, 0))

    def test_two_real_cores_mixed(self):
        # Real A1 and A2 of D049Ga01 sit on shifted grids.
        a1 = grid_names(43.591236, 42.357218, 7, 7)
        a2 = grid_names(43.315077, 40.311410, 7, 6)
        g = analyse_grid(a1 + a2, STEP_X, STEP_Y, 0.1)
        self.assertEqual(g.off_grid, 42)

    def test_bad_names(self):
        g = analyse_grid(["1.0_2.0", "FOV_001"], STEP_X, STEP_Y, 0.1)
        self.assertEqual(g.bad_names, 1)


class ScanRuleTests(FakeData):
    def test_normal_slide_is_ok(self):
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 7, 7))
        make_core(s, "A2", grid_names(43.31, 40.31, 6, 5))
        level, problems, rows = self.rows_by_core(s)
        self.assertEqual(level, OK, problems)
        self.assertEqual(rows["A1"]["fovs"], 49)
        self.assertEqual(rows["A1"]["grid"], "7x7")
        self.assertEqual(rows["A2"]["grid"], "6x5")

    def test_two_cores_in_one_folder(self):
        s = self.slide()
        make_core(s, "A3", grid_names(43.59, 42.35, 6, 6) + grid_names(43.31, 40.31, 6, 5))
        _, _, rows = self.rows_by_core(s)
        self.assertEqual(rows["A3"]["status"], ALERT)
        self.assertIn("more than one core", rows["A3"]["problems"])

    def test_two_cores_on_same_grid_caught_by_size(self):
        s = self.slide()
        # Second core lands exactly on the same grid but far away -> grid too large.
        make_core(
            s, "A3", grid_names(10.0, 10.0, 6, 6) + grid_names(10.0, 10.0 + 10 * STEP_Y, 6, 6)
        )
        _, _, rows = self.rows_by_core(s)
        self.assertEqual(rows["A3"]["status"], ALERT)
        self.assertIn("larger than", rows["A3"]["problems"])

    def test_core_without_scan_folder(self):
        s = self.slide()
        (s / "A4").mkdir()
        _, _, rows = self.rows_by_core(s)
        self.assertEqual(rows["A4"]["status"], ALERT)
        self.assertIn("no scan folder", rows["A4"]["problems"])

    def test_empty_scan_folder(self):
        s = self.slide()
        (s / "A4" / LAMP / "scan").mkdir(parents=True)
        _, _, rows = self.rows_by_core(s)
        self.assertEqual(rows["A4"]["status"], ALERT)

    def test_fov_without_image_and_fov_with_two_images(self):
        s = self.slide()
        lamp = make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        first = sorted((lamp / "scan").iterdir())[0]
        for f in first.glob("*.tiff"):
            f.unlink()
        make_core(s, "A2", grid_names(43.31, 40.31, 3, 3), images_per_fov=2)
        _, _, rows = self.rows_by_core(s)
        self.assertIn("have no image", rows["A1"]["problems"])
        self.assertIn("more than one image", rows["A2"]["problems"])

    def test_missing_position_is_check(self):
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 7, 7)[:-1])
        _, _, rows = self.rows_by_core(s)
        self.assertEqual(rows["A1"]["status"], CHECK)
        self.assertIn("1 positions missing", rows["A1"]["problems"])

    def test_stitch_count_mismatch_is_check(self):
        s = self.slide()
        lamp = make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        next((lamp / "stitch").iterdir()).unlink()
        _, _, rows = self.rows_by_core(s)
        self.assertEqual(rows["A1"]["status"], CHECK)
        self.assertIn("stitch folder has 8 tiles", rows["A1"]["problems"])

    def test_time_gap_is_check(self):
        s = self.slide()
        lamp = make_core(s, "A1", grid_names(43.59, 42.35, 3, 3), mtime=1_750_000_000)
        last = sorted((lamp / "scan").iterdir())[-1] / "0.6.110.tiff"
        os.utime(last, (1_750_000_000 + 3 * 3600,) * 2)
        _, _, rows = self.rows_by_core(s)
        self.assertIn("min apart", rows["A1"]["problems"])

    def test_reference_mismatch(self):
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 7, 7))
        ref = {"D001Ab01_01_CQ_UV": {"A1": 42, "A2": None}}
        level, problems, rows = self.rows_by_core(s, ref)
        self.assertEqual(level, ALERT)
        self.assertIn("reference says 42", rows["A1"]["problems"])
        self.assertTrue(any("A2" in m for _, m in problems))

    def test_odd_slide_name_is_check(self):
        s = self.slide("R165Lu01_03_CQ_Fail")
        make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        level, problems, _ = self.rows_by_core(s)
        self.assertEqual(level, CHECK)
        self.assertIn("naming pattern", problems[0][1])


class FindSlidesTests(FakeData):
    def test_finds_nested_slide_folders_only(self):
        a = self.slide("D001Ab01_01_CQ_UV")
        b = self.slide("D002Ab01_01_CQ_UV")
        make_core(a, "A1", grid_names(1, 1, 2, 2))
        make_core(b, "A1", grid_names(1, 1, 2, 2))
        (self.tmp / "data" / "D001Ab01_01_CQ_UV" / "D001Ab01_01_CQ_UV-stitch (Wuhan)").mkdir()
        slides, unreadable = find_slides(self.tmp / "data", self.layout)
        self.assertEqual(sorted(p.name for p in slides), ["D001Ab01_01_CQ_UV", "D002Ab01_01_CQ_UV"])
        self.assertEqual(unreadable, [])


class ReportAndCompareTests(FakeData):
    def read_csv(self, path):
        with open(path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def test_scan_writes_report(self):
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        out = run_scan(self.tmp / "data", self.tmp / "report", SETTINGS)
        self.assertEqual(self.read_csv(out / "slides.csv")[0]["status"], OK)
        self.assertTrue((out / "report.txt").exists())
        self.assertFalse((out / UNFINISHED).exists())

    def test_finished_run_is_never_reused(self):
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        out = self.tmp / "report"
        run_scan(self.tmp / "data", out, SETTINGS)
        shutil.rmtree(next((s / "A1" / LAMP / "scan").iterdir()))
        run_scan(self.tmp / "data", out, SETTINGS)
        self.assertEqual(self.read_csv(out / "cores.csv")[0]["fovs"], "8")

    def test_unfinished_run_continues(self):
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        out = self.tmp / "report"
        run_scan(self.tmp / "data", out, SETTINGS)
        # Pretend the run stopped before the end (NAS down, PC restarted).
        (out / UNFINISHED).write_text(str((self.tmp / "data").resolve()))
        shutil.rmtree(next((s / "A1" / LAMP / "scan").iterdir()))
        run_scan(self.tmp / "data", out, SETTINGS)
        self.assertEqual(self.read_csv(out / "cores.csv")[0]["fovs"], "9")  # reused
        # ...unless rescan is asked for.
        (out / UNFINISHED).write_text(str((self.tmp / "data").resolve()))
        run_scan(self.tmp / "data", out, SETTINGS, rescan=True)
        self.assertEqual(self.read_csv(out / "cores.csv")[0]["fovs"], "8")

    def test_same_report_folder_for_another_site_reads_fresh(self):
        # Bug found 2026-10-13: the HK scan silently reused the Wuhan manifests.
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        shutil.copytree(self.tmp / "data", self.tmp / "hk")
        hk = self.tmp / "hk" / "D001Ab01_01_CQ_UV" / "D001Ab01_01_CQ_UV"
        shutil.rmtree(next((hk / "A1" / LAMP / "scan").iterdir()))
        out = self.tmp / "report"
        run_scan(self.tmp / "data", out, SETTINGS)
        (out / UNFINISHED).write_text(str((self.tmp / "data").resolve()))  # even if unfinished
        run_scan(self.tmp / "hk", out, SETTINGS)
        self.assertEqual(self.read_csv(out / "cores.csv")[0]["fovs"], "8")

    def test_read_errors_are_read_again_when_continuing(self):
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        out = self.tmp / "report"
        run_scan(self.tmp / "data", out, SETTINGS)
        manifest = next((out / "manifests").glob("*.json"))
        saved = json.loads(manifest.read_text())
        saved["slide"]["scans"][0]["error"] = "cannot read scan folder: NAS down"
        manifest.write_text(json.dumps(saved))
        (out / UNFINISHED).write_text(str((self.tmp / "data").resolve()))
        run_scan(self.tmp / "data", out, SETTINGS)
        self.assertEqual(self.read_csv(out / "cores.csv")[0]["status"], OK)

    def test_single_slide_folder_gets_its_own_manifest_name(self):
        # Bug: scanning one slide folder wrote "..json" for every slide.
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        out = run_scan(s, self.tmp / "report", SETTINGS)
        self.assertEqual(
            [p.name for p in (out / "manifests").iterdir()], ["D001Ab01_01_CQ_UV.json"]
        )

    def test_time_gap_rule_can_be_switched_off(self):
        s = self.slide()
        lamp = make_core(s, "A1", grid_names(43.59, 42.35, 3, 3), mtime=1_750_000_000)
        last = sorted((lamp / "scan").iterdir())[-1] / "0.6.110.tiff"
        os.utime(last, (1_750_000_000 + 3 * 3600,) * 2)
        settings = dict(SETTINGS, checks=dict(SETTINGS["checks"], max_time_gap_minutes=0))
        _, _, rows = check_slide(read_slide(s, self.layout), settings, {})
        self.assertEqual(rows[0]["status"], OK, rows[0]["problems"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read anything")
    def test_unreadable_lamp_folder_is_a_read_error_not_missing_data(self):
        s = self.slide()
        lamp = make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        lamp.chmod(0)
        try:
            scans = read_slide(s, self.layout).scans
        finally:
            lamp.chmod(0o755)
        self.assertTrue(scans[0].error.startswith("cannot read"), scans[0].error)

    def test_compare_finds_lost_and_changed_files(self):
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        make_core(s, "A2", grid_names(43.31, 40.31, 3, 3))
        run_scan(self.tmp / "data", self.tmp / "site1", SETTINGS)

        shutil.copytree(self.tmp / "data", self.tmp / "copy")
        copied = self.tmp / "copy" / "D001Ab01_01_CQ_UV" / "D001Ab01_01_CQ_UV"
        fovs = sorted((copied / "A1" / LAMP / "scan").iterdir())
        shutil.rmtree(fovs[0])  # lost FOV
        (fovs[1] / "0.6.110.tiff").write_bytes(b"short")  # damaged image
        shutil.rmtree(copied / "A2")  # lost core
        run_scan(self.tmp / "copy", self.tmp / "site2", SETTINGS)

        run_compare(self.tmp / "site1", self.tmp / "site2", self.tmp / "cmp")
        messages = [r["message"] for r in self.read_csv(self.tmp / "cmp" / "compare.csv")]
        self.assertTrue(any("1 FOVs missing at second site" in m for m in messages))
        self.assertTrue(any("different-size" in m for m in messages))
        self.assertTrue(any("missing at second site" in m and "core" in m for m in messages))

    def test_compare_finds_lost_stitch_tiles(self):
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        run_scan(self.tmp / "data", self.tmp / "site1", SETTINGS)
        shutil.copytree(self.tmp / "data", self.tmp / "copy")
        copied = self.tmp / "copy" / "D001Ab01_01_CQ_UV" / "D001Ab01_01_CQ_UV"
        shutil.rmtree(copied / "A1" / LAMP / "stitch")
        run_scan(self.tmp / "copy", self.tmp / "site2", SETTINGS)
        run_compare(self.tmp / "site1", self.tmp / "site2", self.tmp / "cmp")
        messages = [r["message"] for r in self.read_csv(self.tmp / "cmp" / "compare.csv")]
        self.assertIn("9 stitch tiles at first site, no stitch folder at second site", messages)

    def test_compare_warns_about_duplicate_slide_names(self):
        for project in ("Project_1", "Project_2"):
            s = self.tmp / "data" / project / "D001Ab01_01_CQ_UV"
            s.mkdir(parents=True)
            make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        run_scan(self.tmp / "data", self.tmp / "site1", SETTINGS)
        run_scan(self.tmp / "data", self.tmp / "site2", SETTINGS)
        run_compare(self.tmp / "site1", self.tmp / "site2", self.tmp / "cmp")
        rows = self.read_csv(self.tmp / "cmp" / "compare.csv")
        self.assertTrue(any("more than once" in r["message"] for r in rows))
        self.assertIn("identical: 0", (self.tmp / "cmp" / "report.txt").read_text())

    def test_compare_identical_copy_has_no_problems(self):
        s = self.slide()
        make_core(s, "A1", grid_names(43.59, 42.35, 3, 3))
        run_scan(self.tmp / "data", self.tmp / "site1", SETTINGS)
        shutil.copytree(self.tmp / "data", self.tmp / "copy")
        run_scan(self.tmp / "copy", self.tmp / "site2", SETTINGS)
        run_compare(self.tmp / "site1", self.tmp / "site2", self.tmp / "cmp")
        self.assertEqual(self.read_csv(self.tmp / "cmp" / "compare.csv"), [])


if __name__ == "__main__":
    unittest.main()
