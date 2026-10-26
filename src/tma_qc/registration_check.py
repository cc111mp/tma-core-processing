"""Registration check: is every core registered, and does AF line up with HE?

Reads only the small text files the registration code writes; no image is opened.

Registered layout (company NAS, October 2026):

    <slide>_Reg/<core>/<core>.tiff                  HE (moving)
                      /<slide>_<core>_AF.tif        AF (fixed)
                      /Registration/<core>_Stain_Reg.tif
                                   /<slide>_<core>_AF_Reg.tif
                                   /matrix.txt                  2x3 transform HE -> AF
                                   /registration_log.csv        feature matches
                                   /postcrop_eval_result.txt    tissue overlap (Dice) and
                                                                local residual shift
"""

from __future__ import annotations

import csv
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .raw_layout import find_slides, layout_from
from .report import ALERT, CHECK, RANK, alert_lines, worst, write_csv

# The eval file exists in two formats ("Dice score: 0.88" and "postcrop_dice: 0.96").
# Each value we use, with the names it has in either format.
EVAL_FIELDS = {
    "dice": ("dice score", "postcrop_dice"),
    "over_af_pct": ("intersection over fixed", "intersection_over_fixed_pct"),
    "over_he_pct": ("intersection over moving", "intersection_over_moving_pct"),
    "residual_px": ("mean residual magnitude", "local_phase_mean_magnitude"),
    "boundary_review": ("boundary_review_status",),
}


NUMBER = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)(?:\s*(%|px))?")


def read_eval(path: Path) -> dict:
    """Values from postcrop_eval_result.txt. Numbers keep their unit off ("35.6 px" -> 35.6)."""
    values = {}
    for line in path.read_text("utf-8", errors="replace").splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        for name, aliases in EVAL_FIELDS.items():
            if key in aliases:
                number = NUMBER.fullmatch(value) if name != "boundary_review" else None
                if number and number.group(2) not in (
                    {None, "px"}
                    if name == "residual_px"
                    else {None, "%"}
                    if name.endswith("_pct")
                    else {None}
                ):
                    number = None
                values[name] = float(number.group(1)) if number else value
    return values


def read_matrix(path: Path) -> tuple[float, float]:
    """Return (scale, rotation in degrees) of the 2x3 similarity transform."""
    rows = [
        [float(v) for v in line.split()] for line in path.read_text().splitlines() if line.strip()
    ]
    if len(rows) not in {2, 3} or any(len(row) != 3 for row in rows):
        raise ValueError("Expected a 2x3 similarity matrix or its 3x3 homogeneous form")
    if not all(math.isfinite(v) for row in rows for v in row):
        raise ValueError("Nonfinite registration matrix")
    if len(rows) == 3 and any(
        not math.isclose(v, expected, abs_tol=1e-10)
        for v, expected in zip(rows[2], [0.0, 0.0, 1.0])
    ):
        raise ValueError("Invalid homogeneous row in registration matrix")
    a, b = rows[0][0], rows[0][1]
    c, d = rows[1][0], rows[1][1]
    determinant = a * d - b * c
    if not math.isfinite(determinant) or abs(determinant) < 1e-10:
        raise ValueError("Singular registration matrix")
    if not math.isclose(c, -b, rel_tol=1e-5, abs_tol=1e-8) or not math.isclose(
        d, a, rel_tol=1e-5, abs_tol=1e-8
    ):
        raise ValueError("Registration matrix is not the expected similarity transform")
    return math.hypot(a, b), math.degrees(math.atan2(b, a))


def read_matches(path: Path) -> int | None:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    try:
        value = float(rows[0]["good_matches"])
        return int(value) if math.isfinite(value) and value >= 0 and int(value) == value else None
    except (IndexError, KeyError, TypeError, ValueError):
        return None


REGISTRATION_DIR = re.compile(r"^Registration(?: (\d+))?$")


def registration_dirs(core_dir: Path) -> list[Path]:
    """Registration folders of a core ("Registration", "Registration 1", ... "Registration 10"),
    sorted by number so the last one is the newest."""
    found = []
    for p in core_dir.iterdir():
        m = REGISTRATION_DIR.match(p.name)
        if m and p.is_dir():
            found.append((int(m.group(1) or 0), p))  # "Registration" counts as 0
    return [p for _, p in sorted(found)]


def check_core(core_dir: Path, settings: dict) -> dict:
    """Check one registered core. Returns one report row."""
    r = settings["registration"]
    row = {
        "status": "",
        "slide": core_dir.parent.name,
        "core": core_dir.name,
        "dice": "",
        "over_af_pct": "",
        "over_he_pct": "",
        "residual_px": "",
        "scale": "",
        "rotation_deg": "",
        "matches": "",
        "problems": "",
    }
    problems: list[tuple[str, str]] = []

    def add(level, message):
        problems.append((level, message))

    reg_dirs = registration_dirs(core_dir)
    if not reg_dirs:
        add(ALERT, "not registered (no Registration folder)")
    else:
        if len(reg_dirs) > 1:
            add(CHECK, f"{len(reg_dirs)} Registration folders; using {reg_dirs[-1].name}")
        reg = reg_dirs[-1]
        names = {p.name for p in reg.iterdir()}

        if not any(n.endswith("_Stain_Reg.tif") for n in names):
            add(ALERT, "registered HE image missing")
        if not any(n.endswith("_AF_Reg.tif") for n in names):
            add(ALERT, "registered AF image missing")

        if "matrix.txt" in names:
            scale, rotation = read_matrix(reg / "matrix.txt")
            row["scale"], row["rotation_deg"] = round(scale, 4), round(rotation, 2)
            if abs(scale - r["expected_scale"]) > r["scale_tolerance"]:
                add(
                    ALERT,
                    f"transform scale {scale:.2f}, expected about "
                    f"{r['expected_scale']} - registration probably failed",
                )
            if abs(rotation) > r["max_rotation_deg"]:
                add(ALERT, f"HE rotated {rotation:.0f} degrees - registration probably failed")
        else:
            add(ALERT, "no matrix.txt")

        if "registration_log.csv" in names:
            matches = read_matches(reg / "registration_log.csv")
            row["matches"] = "" if matches is None else matches
            if matches is None:
                add(CHECK, "missing or invalid matched-feature count")
            elif matches < r["min_matches"]:
                add(CHECK, f"only {matches} matched features")
        else:
            add(CHECK, "missing registration_log.csv (matched-feature count unavailable)")

        if "postcrop_eval_result.txt" in names:
            ev = read_eval(reg / "postcrop_eval_result.txt")
            for key in ("dice", "residual_px"):
                if not isinstance(ev.get(key), float) or not math.isfinite(ev[key]):
                    add(CHECK, f"missing or invalid evaluation value: {key}")
            for key in ("dice", "over_af_pct", "over_he_pct", "residual_px"):
                value = ev.get(key)
                if isinstance(value, float) and math.isfinite(value):
                    maximum = 1 if key == "dice" else float("inf") if key == "residual_px" else 100
                    if not 0 <= value <= maximum:
                        add(ALERT, f"evaluation value out of range: {key}={value}")
                    else:
                        row[key] = round(value, 3)
                elif key in ev and key not in {"dice", "residual_px"}:
                    add(CHECK, f"invalid evaluation value: {key}")
            dice = ev.get("dice")
            if isinstance(dice, float) and math.isfinite(dice):
                if dice < r["dice_alert"]:
                    add(ALERT, f"tissue overlap (Dice) {dice:.2f} is very low")
                elif dice < r["dice_check"]:
                    add(CHECK, f"tissue overlap (Dice) {dice:.2f} is low")
            residual = ev.get("residual_px")
            if (
                isinstance(residual, float)
                and math.isfinite(residual)
                and residual > r["max_residual_px"]
            ):
                add(CHECK, f"local shift after registration {residual:.0f} px")
            review = ev.get("boundary_review")
            if isinstance(review, str) and review and review != "normal":
                add(CHECK, f"registration code flagged the crop boundary ({review})")
        else:
            add(CHECK, "no evaluation result (postcrop_eval_result.txt)")

    row["status"] = worst(level for level, _ in problems)
    row["problems"] = "; ".join(m for _, m in problems)
    return row


def check_core_safely(core_dir: Path, settings: dict) -> dict:
    """check_core, but a broken file or a NAS error becomes an ALERT row
    instead of stopping the whole run."""
    try:
        return check_core(core_dir, settings)
    except Exception as exc:
        return {
            "status": ALERT,
            "slide": core_dir.parent.name,
            "core": core_dir.name,
            "dice": "",
            "over_af_pct": "",
            "over_he_pct": "",
            "residual_px": "",
            "scale": "",
            "rotation_deg": "",
            "matches": "",
            "problems": f"cannot read registration files: {type(exc).__name__}: {exc}",
        }


def run_registration_check(data_folder, out_folder, settings) -> Path:
    data_folder = Path(data_folder)
    out = Path(out_folder)
    out.mkdir(parents=True, exist_ok=True)
    layout = layout_from(settings)

    slides, unreadable = find_slides(data_folder, layout)
    print(f"Found {len(slides)} registered slide folders under {data_folder}", flush=True)
    rows = []
    for n, slide in enumerate(slides, 1):
        try:
            cores = sorted(p for p in slide.iterdir() if p.is_dir() and layout.is_core(p.name))
        except OSError as exc:
            unreadable.append(f"cannot read {slide}: {exc}")
            continue
        # Small files on the NAS are slow one by one, so several cores are read at once.
        with ThreadPoolExecutor(max_workers=layout.read_threads) as pool:
            rows += list(pool.map(lambda c: check_core_safely(c, settings), cores))
        print(f"[{n}/{len(slides)}] {slide.name}: {len(cores)} cores", flush=True)

    alerts = [{"level": ALERT, "slide": "", "core": "", "message": u} for u in unreadable]
    alerts += [
        {"level": r["status"], "slide": r["slide"], "core": r["core"], "message": r["problems"]}
        for r in rows
        if r["status"] != "OK"
    ]
    alerts.sort(key=lambda a: (-RANK[a["level"]], a["slide"], a["core"]))
    write_csv(out / "registration.csv", rows)
    write_csv(out / "alerts.csv", alerts, ["level", "slide", "core", "message"])

    statuses = [r["status"] for r in rows]
    lines = [
        "TMA registration check",
        f"Data folder: {data_folder}",
        f"Report made: {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Slides: {len(slides)}   Cores: {len(rows)}   OK {statuses.count('OK')}   "
        f"CHECK {statuses.count(CHECK)}   ALERT {statuses.count(ALERT)}",
        "",
    ] + alert_lines(alerts)
    (out / "report.txt").write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))
    print(f"Report written to {out}")
    return out
