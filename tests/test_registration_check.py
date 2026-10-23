"""Tests for the registration check, using fake registered cores.

The text files below are copied from real registration output (October 2026).
"""

import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from tma_qc.registration_check import check_core, run_registration_check
from tma_qc.report import ALERT, CHECK, OK, load_settings

SETTINGS = load_settings(Path(__file__).resolve().parent.parent / "settings.toml")

GOOD_MATRIX = "0.84732073 0.02912310 -74.46218748\n-0.02912310 0.84732073 1670.25576179\n"
LOG_CSV = (
    "detector,ratio_thresh,ransac_thresh,equalize_hist_fixed,equalize_hist_moving,"
    "dice_coefficient,good_matches\norb,0.75,3.0,True,True,0.45865,{matches}\n"
)
OLD_EVAL = """Dice score: {dice}
Fixed tissue pixels: 31822662
Intersection over fixed: 78.26%
Intersection over moving: 97.30%
IoU / Jaccard score: 0.765999
Mean residual magnitude: {residual} px
Median residual magnitude: 34.2024 px
"""
NEW_EVAL = """status: DONE
boundary_review_status: {review}
postcrop_dice: {dice}
intersection_over_fixed_pct: 92.98968326593034
intersection_over_moving_pct: 99.81981687554276
local_phase_mean_magnitude: {residual}
"""


def similarity(scale, degrees):
    import math

    a = scale * math.cos(math.radians(degrees))
    b = scale * math.sin(math.radians(degrees))
    return f"{a} {b} 0\n{-b} {a} 0\n"


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.slide = self.tmp / "Project_Reg" / "D001Ab01_01_CQ_UV_Reg"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def core(
        self, name, matrix=GOOD_MATRIX, eval_text=None, matches=89, images=True, registration=True
    ):
        core = self.slide / name
        core.mkdir(parents=True)
        (core / f"{name}.tiff").write_bytes(b"he")
        (core / f"D001Ab01_01_CQ_UV_{name}_AF.tif").write_bytes(b"af")
        if not registration:
            return core
        reg = core / "Registration"
        reg.mkdir()
        if images:
            (reg / f"{name}_Stain_Reg.tif").write_bytes(b"he")
            (reg / f"D001Ab01_01_CQ_UV_{name}_AF_Reg.tif").write_bytes(b"af")
        if matrix:
            (reg / "matrix.txt").write_text(matrix)
        (reg / "registration_log.csv").write_text(LOG_CSV.format(matches=matches))
        if eval_text is None:
            eval_text = OLD_EVAL.format(dice=0.867496, residual=35.6)
        if eval_text:
            (reg / "postcrop_eval_result.txt").write_text(eval_text)
        return core

    def test_good_core_old_format(self):
        row = check_core(self.core("A1"), SETTINGS)
        self.assertEqual(row["status"], OK, row["problems"])
        self.assertAlmostEqual(row["dice"], 0.867, places=3)
        self.assertAlmostEqual(row["scale"], 0.8478, places=3)

    def test_good_core_new_format(self):
        text = NEW_EVAL.format(review="normal", dice=0.9628, residual=34.6)
        row = check_core(self.core("A1", eval_text=text), SETTINGS)
        self.assertEqual(row["status"], OK, row["problems"])
        self.assertAlmostEqual(row["over_af_pct"], 92.99, places=2)

    def test_wrong_scale_and_rotation_are_alerts(self):
        # Real failures: G8 scale 0.32, C5 rotated 139 degrees.
        row = check_core(self.core("G8", matrix=similarity(0.3214, -52.4)), SETTINGS)
        self.assertEqual(row["status"], ALERT)
        self.assertIn("scale 0.32", row["problems"])
        self.assertIn("rotated -52 degrees", row["problems"])

    def test_dice_levels(self):
        low = check_core(
            self.core("A1", eval_text=OLD_EVAL.format(dice=0.70, residual=30)), SETTINGS
        )
        very_low = check_core(
            self.core("A2", eval_text=OLD_EVAL.format(dice=0.24, residual=30)), SETTINGS
        )
        self.assertEqual(low["status"], CHECK)
        self.assertEqual(very_low["status"], ALERT)

    def test_large_residual_and_few_matches_are_checks(self):
        row = check_core(
            self.core("A1", eval_text=OLD_EVAL.format(dice=0.9, residual=80), matches=3), SETTINGS
        )
        self.assertEqual(row["status"], CHECK)
        self.assertIn("local shift", row["problems"])
        self.assertIn("only 3 matched", row["problems"])

    def test_flagged_boundary_is_check(self):
        text = NEW_EVAL.format(review="flagged", dice=0.95, residual=20)
        row = check_core(self.core("A1", eval_text=text), SETTINGS)
        self.assertEqual(row["status"], CHECK)

    def test_missing_pieces(self):
        no_reg = check_core(self.core("A1", registration=False), SETTINGS)
        no_images = check_core(self.core("A2", images=False), SETTINGS)
        no_eval = check_core(self.core("A3", eval_text=""), SETTINGS)
        self.assertIn("not registered", no_reg["problems"])
        self.assertEqual(no_images["status"], ALERT)
        self.assertEqual(no_eval["status"], CHECK)

    def test_missing_feature_log_requires_review(self):
        core = self.core("A1")
        (core / "Registration/registration_log.csv").unlink()

        row = check_core(core, SETTINGS)

        self.assertEqual(row["status"], CHECK)
        self.assertEqual(row["matches"], "")
        self.assertIn("missing registration_log.csv", row["problems"])

    def test_empty_feature_log_requires_review(self):
        core = self.core("A1")
        (core / "Registration/registration_log.csv").write_text("detector,good_matches\n")

        row = check_core(core, SETTINGS)

        self.assertEqual(row["status"], CHECK)
        self.assertEqual(row["matches"], "")
        self.assertIn("missing or invalid matched-feature count", row["problems"])

    def test_residual_with_px_unit_is_read(self):
        # Bug found 2026-10-13: "35.65 px" was not read, so the shift rule never ran.
        row = check_core(
            self.core("A1", eval_text=OLD_EVAL.format(dice=0.9, residual=35.65)), SETTINGS
        )
        self.assertEqual(row["residual_px"], 35.65)
        self.assertEqual(row["over_af_pct"], 78.26)

    def test_newest_registration_folder_is_used(self):
        core = self.core("A1")
        for n in (2, 10):
            reg = core / f"Registration {n}"
            reg.mkdir()
        dice = {"Registration 2": 0.70, "Registration 10": 0.95}
        for name, value in dice.items():
            reg = core / name
            (reg / "A1_Stain_Reg.tif").write_bytes(b"he")
            (reg / "D001_A1_AF_Reg.tif").write_bytes(b"af")
            (reg / "matrix.txt").write_text(GOOD_MATRIX)
            (reg / "postcrop_eval_result.txt").write_text(OLD_EVAL.format(dice=value, residual=30))
        row = check_core(core, SETTINGS)
        self.assertEqual(row["dice"], 0.95)  # from "Registration 10", not "Registration 2"
        self.assertIn("3 Registration folders; using Registration 10", row["problems"])

    def test_broken_file_becomes_alert_and_run_continues(self):
        # Bug found 2026-10-13: one bad matrix.txt crashed the whole run.
        self.core("A1")
        self.core("A2", matrix="garbage\n")
        out = run_registration_check(self.tmp / "Project_Reg", self.tmp / "report", SETTINGS)
        with open(out / "registration.csv", encoding="utf-8-sig") as f:
            rows = {r["core"]: r for r in csv.DictReader(f)}
        self.assertEqual(rows["A1"]["status"], OK)
        self.assertEqual(rows["A2"]["status"], ALERT)
        self.assertIn("cannot read registration files", rows["A2"]["problems"])

    def test_report(self):
        self.core("A1")
        self.core("A2", matrix=similarity(1.70, -2))
        out = run_registration_check(self.tmp / "Project_Reg", self.tmp / "report", SETTINGS)
        with open(out / "registration.csv", encoding="utf-8-sig") as f:
            rows = {r["core"]: r for r in csv.DictReader(f)}
        self.assertEqual(rows["A1"]["status"], OK)
        self.assertEqual(rows["A2"]["status"], ALERT)
        self.assertIn("ALERT (1)", (out / "report.txt").read_text())


if __name__ == "__main__":
    unittest.main()
