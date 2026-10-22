"""Transfer check: count slides, cores and FOVs, and flag anything odd.

Two jobs:
  scan     read a data folder, apply the rules below, write a report and
           one manifest (a "packing list") per slide
  compare  compare the manifests of two sites, e.g. Wuhan NAS vs HK HDD

Every result is one of three levels:
  OK     nothing to do
  CHECK  probably fine, a person should look
  ALERT  something is wrong (missing data, or several cores in one folder)
"""

from __future__ import annotations

import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .raw_layout import (
    Scan,
    Slide,
    analyse_grid,
    find_slides,
    layout_from,
    read_slide,
    slide_from_dict,
    slide_to_dict,
)
from .report import ALERT, CHECK, OK, RANK, alert_lines, worst, write_csv


def load_reference(path: str | Path | None) -> dict:
    """Read reference.csv: slide, core, expected_fovs (expected_fovs may be blank).

    Returns {slide: {core: expected_fovs or None}}.
    """
    if not path:
        return {}
    ref: dict = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            value = (row.get("expected_fovs") or "").strip()
            ref.setdefault(row["slide"].strip(), {})[row["core"].strip()] = (
                int(value) if value else None
            )
    return ref


# ------------------------------------------------------------------ rules


def check_scan(scan: Scan, settings: dict, expected_fovs: int | None = None):
    """Apply the per-core rules. Returns (level, problems, numbers)."""
    g = settings["grid"]
    c = settings["checks"]
    problems: list[tuple[str, str]] = []  # (level, message)

    def add(level: str, message: str) -> None:
        problems.append((level, message))

    grid = analyse_grid([f.name for f in scan.fovs], g["step_x_mm"], g["step_y_mm"], g["tolerance"])
    images = [i for f in scan.fovs for i in f.images]
    numbers = {
        "fovs": len(scan.fovs),
        "grid": f"{grid.columns}x{grid.rows}" if grid.columns else "",
        "images": len(images),
        "stitch_tiles": "" if scan.stitch_tiles is None else scan.stitch_tiles,
        "size_gb": round(sum(i.size for i in images) / 1e9, 3),
        "scan_minutes": "",
        "expected_fovs": "" if expected_fovs is None else expected_fovs,
    }

    if scan.error:
        add(ALERT, scan.error)
        return ALERT, problems, numbers
    if not scan.fovs:
        add(ALERT, "no FOVs in the scan folder")
        return ALERT, problems, numbers

    # Several cores in one folder -> extra positions on a shifted grid,
    # or a grid bigger than one core can be.
    if grid.off_grid:
        add(
            ALERT,
            f"{grid.off_grid} FOVs do not fit the main scan grid "
            "- possibly more than one core in this folder",
        )
    if grid.columns > g["max_columns"] or grid.rows > g["max_rows"]:
        add(
            ALERT,
            f"scan grid is {grid.columns}x{grid.rows}, larger than the "
            f"{g['max_columns']}x{g['max_rows']} limit "
            "- possibly more than one core in this folder",
        )
    if grid.duplicates:
        add(ALERT, f"{grid.duplicates} FOVs share a grid position with another FOV")
    if grid.missing:
        add(CHECK, f"{grid.missing} positions missing inside the {grid.columns}x{grid.rows} grid")
    if grid.bad_names:
        add(CHECK, f"{grid.bad_names} FOV folder names are not stage positions (X_Y)")

    if expected_fovs is not None and len(scan.fovs) != expected_fovs:
        add(ALERT, f"{len(scan.fovs)} FOVs, reference says {expected_fovs}")

    no_image = [f.name for f in scan.fovs if not f.images]
    if no_image:
        add(ALERT, f"{len(no_image)} FOVs have no image (e.g. {no_image[0]})")
    several = [f.name for f in scan.fovs if len(f.images) > 1]
    if several:
        add(ALERT, f"{len(several)} FOVs have more than one image (e.g. {several[0]})")
    empty = [i.name for i in images if i.size == 0]
    if empty:
        add(ALERT, f"{len(empty)} images are 0 bytes (e.g. {empty[0]})")

    if scan.unexpected:
        add(
            CHECK,
            f"unexpected items in scan folder: {', '.join(scan.unexpected[:3])}"
            + (" ..." if len(scan.unexpected) > 3 else ""),
        )

    if scan.stitch_tiles is None:
        if c["expect_stitch_folder"]:
            add(CHECK, "no stitch folder")
    elif scan.stitch_tiles != len(scan.fovs):
        add(CHECK, f"stitch folder has {scan.stitch_tiles} tiles, scan has {len(scan.fovs)} FOVs")

    if images:
        times = sorted(i.mtime for i in images)
        numbers["scan_minutes"] = round((times[-1] - times[0]) / 60, 1)
        gap = max((b - a for a, b in zip(times, times[1:])), default=0) / 60
        if c["max_time_gap_minutes"] and gap > c["max_time_gap_minutes"]:
            add(
                CHECK,
                f"images were written {gap:.0f} min apart - possibly two separate scan sessions",
            )

    return worst(level for level, _ in problems), problems, numbers


def check_slide(slide: Slide, settings: dict, reference: dict):
    """Apply per-slide and per-core rules.

    Returns (slide_level, slide_problems, core_rows).
    """
    problems: list[tuple[str, str]] = []
    ref = reference.get(slide.name)
    pattern = settings["checks"].get("slide_name_pattern", "")

    if slide.error:
        problems.append((ALERT, slide.error))
    if pattern and not re.match(pattern, slide.name):
        problems.append((CHECK, "slide folder name does not follow the naming pattern"))
    if not slide.scans and not slide.error:
        problems.append((ALERT, "no core folders with data"))

    cores = [s.core for s in slide.scans]
    for core in sorted({c for c in cores if cores.count(c) > 1}):
        problems.append((CHECK, f"{core} has {cores.count(core)} lamp folders"))
    if reference and ref is None:
        problems.append((CHECK, "slide is not in the reference list"))
    if ref is not None:
        missing = sorted(set(ref) - set(cores))
        extra = sorted(set(cores) - set(ref))
        if missing:
            problems.append((ALERT, f"cores in reference but not found: {', '.join(missing)}"))
        if extra:
            problems.append((CHECK, f"cores found but not in reference: {', '.join(extra)}"))

    rows = []
    for scan in slide.scans:
        expected = ref.get(scan.core) if ref else None
        level, scan_problems, numbers = check_scan(scan, settings, expected)
        rows.append(
            {
                "status": level,
                "slide": slide.name,
                "core": scan.core,
                "lamp": scan.lamp,
                **numbers,
                "problems": "; ".join(m for _, m in scan_problems),
            }
        )

    level = worst([lvl for lvl, _ in problems] + [r["status"] for r in rows])
    return level, problems, rows


# ------------------------------------------------------------------- scan


def _manifest_name(slide_folder: Path, root: Path) -> str:
    """File name for a slide's manifest, unique within one data folder."""
    try:
        rel = slide_folder.relative_to(root).as_posix()
    except ValueError:
        rel = "."
    if rel == ".":  # the data folder is the slide itself
        rel = slide_folder.name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", rel) + ".json"


UNFINISHED = "run_unfinished.txt"  # present while a scan is running in a report folder


def _has_read_errors(slide: Slide) -> bool:
    """True if part of the slide could not be read (e.g. the NAS dropped)."""
    return bool(slide.error) or any(s.error.startswith("cannot read") for s in slide.scans)


def run_scan(data_folder, out_folder, settings, reference_csv=None, rescan=False):
    """Read every slide under data_folder and write the report into out_folder.

    If a run stops half-way (NAS down, PC restarted), the same command continues
    it: slides already read by that unfinished run are reused. Otherwise every
    slide is read fresh - after a finished run, for a different data folder, or
    with rescan=True - so an old result is never reported as new.
    """
    data_folder = Path(data_folder)
    source = str(data_folder.resolve())
    out = Path(out_folder)
    manifests = out / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    marker = out / UNFINISHED
    resume = not rescan and marker.exists() and marker.read_text("utf-8").strip() == source
    if resume:
        print("Continuing the unfinished run in this report folder")
    else:
        # Manifests of an earlier run must not be mixed into this one.
        for old in manifests.glob("*.json"):
            old.unlink()
    marker.write_text(source + "\n", "utf-8")

    layout = layout_from(settings)
    reference = load_reference(reference_csv)
    slide_folders, unreadable = find_slides(data_folder, layout)
    print(f"Found {len(slide_folders)} slide folders under {data_folder}")

    slides: list[Slide] = []
    for n, folder in enumerate(slide_folders, 1):
        manifest = manifests / _manifest_name(folder, data_folder)
        start = time.time()
        slide = None
        if resume and manifest.exists():
            saved = json.loads(manifest.read_text("utf-8"))
            slide = slide_from_dict(saved["slide"])
            if saved.get("data_folder") != source or _has_read_errors(slide):
                slide = None  # read again: other source, or the NAS failed last time
            else:
                note = "already read in this run, using saved manifest"
        if slide is None:
            print(f"[{n}/{len(slide_folders)}] reading {folder.name}:", end="", flush=True)
            slide = read_slide(
                folder, layout, on_core=lambda core: print(f" {core}", end="", flush=True)
            )
            print()
            manifest.write_text(
                json.dumps(
                    {
                        "tool": "tma_qc.transfer_check",
                        "version": __version__,
                        "data_folder": source,
                        "read_at": datetime.now().isoformat(timespec="seconds"),
                        "slide": slide_to_dict(slide),
                    }
                ),
                "utf-8",
            )
            note = f"{time.time() - start:.0f} s"
        fovs = sum(len(s.fovs) for s in slide.scans)
        print(
            f"[{n}/{len(slide_folders)}] {slide.name}: {len(slide.scans)} cores, "
            f"{fovs} FOVs ({note})"
        )
        slides.append(slide)

    _write_scan_report(out, data_folder, slides, settings, reference, unreadable)
    marker.unlink()
    return out


def _write_scan_report(out, data_folder, slides, settings, reference, unreadable):
    slide_rows, core_rows, alerts = [], [], []
    names = [s.name for s in slides]

    for problem in unreadable:
        alerts.append({"level": ALERT, "slide": "", "core": "", "message": problem})
    for slide in slides:
        level, problems, rows = check_slide(slide, settings, reference)
        if names.count(slide.name) > 1:
            problems.append((CHECK, "the same slide name appears in more than one folder"))
            level = worst([level, CHECK])
        core_rows += rows
        for lvl, msg in problems:
            alerts.append({"level": lvl, "slide": slide.name, "core": "", "message": msg})
        for r in rows:
            if r["status"] != OK:
                alerts.append(
                    {
                        "level": r["status"],
                        "slide": slide.name,
                        "core": r["core"],
                        "message": r["problems"],
                    }
                )
        statuses = [r["status"] for r in rows]
        slide_rows.append(
            {
                "status": level,
                "slide": slide.name,
                "cores": len({r["core"] for r in rows}),
                "cores_ok": statuses.count(OK),
                "cores_check": statuses.count(CHECK),
                "cores_alert": statuses.count(ALERT),
                "fovs": sum(r["fovs"] for r in rows),
                "size_gb": round(sum(r["size_gb"] for r in rows), 2),
                "slide_problems": "; ".join(m for _, m in problems),
                "folder": slide.path,
            }
        )

    if reference:
        found = set(names)
        for missing in sorted(set(reference) - found):
            alerts.append(
                {
                    "level": ALERT,
                    "slide": missing,
                    "core": "",
                    "message": "slide in reference list but not found",
                }
            )

    alerts.sort(key=lambda a: (-RANK[a["level"]], a["slide"], a["core"]))
    write_csv(out / "slides.csv", slide_rows)
    write_csv(out / "cores.csv", core_rows)
    write_csv(out / "alerts.csv", alerts, ["level", "slide", "core", "message"])

    statuses = [r["status"] for r in slide_rows]
    lines = [
        "TMA transfer check - scan",
        f"Data folder: {data_folder}",
        f"Report made: {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Slides: {len(slide_rows)}   OK {statuses.count(OK)}   "
        f"CHECK {statuses.count(CHECK)}   ALERT {statuses.count(ALERT)}",
        f"Cores:  {len({(r['slide'], r['core']) for r in core_rows})}   FOVs: {sum(r['fovs'] for r in core_rows)}   "
        f"Size: {sum(r['size_gb'] for r in core_rows):.1f} GB",
        "",
    ]
    lines += alert_lines(alerts)
    (out / "report.txt").write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))
    print(f"Report written to {out}")


# ---------------------------------------------------------------- compare


def _load_manifests(report_folder) -> tuple[dict[str, Slide], list[str]]:
    """Slides of a report folder by name, plus names that appear more than once."""
    slides, duplicates = {}, []
    for path in sorted(Path(report_folder, "manifests").glob("*.json")):
        slide = slide_from_dict(json.loads(path.read_text("utf-8"))["slide"])
        if slide.name in slides:
            duplicates.append(slide.name)
        slides[slide.name] = slide
    return slides, duplicates


def compare_slides(sent: Slide, received: Slide) -> list[dict]:
    """Compare one slide at two sites. Returns problem rows (empty = identical)."""
    rows = []

    def add(level, core, message):
        rows.append({"level": level, "slide": sent.name, "core": core, "message": message})

    def by_key(slide):
        return {(s.core, s.lamp): s for s in slide.scans}

    a, b = by_key(sent), by_key(received)
    for key in sorted(set(a) - set(b)):
        add(ALERT, key[0], f"core{' / ' + key[1] if key[1] else ''} missing at second site")
    for key in sorted(set(b) - set(a)):
        add(ALERT, key[0], f"core{' / ' + key[1] if key[1] else ''} only at second site")

    for key in sorted(set(a) & set(b)):
        fa = {f.name: f for f in a[key].fovs}
        fb = {f.name: f for f in b[key].fovs}
        missing = sorted(set(fa) - set(fb))
        extra = sorted(set(fb) - set(fa))
        if missing:
            add(ALERT, key[0], f"{len(missing)} FOVs missing at second site (e.g. {missing[0]})")
        if extra:
            add(ALERT, key[0], f"{len(extra)} extra FOVs at second site (e.g. {extra[0]})")
        changed = []
        for name in set(fa) & set(fb):
            ia = {i.name: i.size for i in fa[name].images}
            ib = {i.name: i.size for i in fb[name].images}
            if ia != ib:
                changed.append(name)
        if changed:
            changed.sort()
            add(
                ALERT,
                key[0],
                f"{len(changed)} FOVs have missing or different-size "
                f"images at second site (e.g. {changed[0]})",
            )
        if a[key].stitch_tiles != b[key].stitch_tiles:

            def tiles(n):
                return "no stitch folder" if n is None else f"{n} stitch tiles"

            add(
                ALERT,
                key[0],
                f"{tiles(a[key].stitch_tiles)} at first site, "
                f"{tiles(b[key].stitch_tiles)} at second site",
            )
        if b[key].error and not a[key].error:
            add(ALERT, key[0], f"second site: {b[key].error}")
    return rows


def run_compare(first_report, second_report, out_folder):
    """Compare the manifests of two scan reports (first = sender, second = receiver)."""
    a, dup_a = _load_manifests(first_report)
    b, dup_b = _load_manifests(second_report)
    rows = []
    for site, dups in (("first", dup_a), ("second", dup_b)):
        for name in sorted(set(dups)):
            rows.append(
                {
                    "level": ALERT,
                    "slide": name,
                    "core": "",
                    "message": f"slide name appears more than once at {site} site "
                    "- only one copy was compared",
                }
            )
    for name in sorted(set(a) - set(b)):
        rows.append(
            {"level": ALERT, "slide": name, "core": "", "message": "slide missing at second site"}
        )
    for name in sorted(set(b) - set(a)):
        rows.append(
            {"level": CHECK, "slide": name, "core": "", "message": "slide only at second site"}
        )
    same = 0
    for name in sorted(set(a) & set(b)):
        problems = compare_slides(a[name], b[name])
        rows += problems
        same += not problems and name not in dup_a + dup_b

    out = Path(out_folder)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "compare.csv", rows, ["level", "slide", "core", "message"])
    lines = [
        "TMA transfer check - compare",
        f"First site:  {first_report}",
        f"Second site: {second_report}",
        f"Report made: {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Slides at first site: {len(a)}   at second site: {len(b)}   identical: {same}",
        "",
    ]
    lines += alert_lines(rows)
    (out / "report.txt").write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))
    print(f"Report written to {out}")
    return out
