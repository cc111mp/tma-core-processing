"""Read raw TMA scan folders without opening any image.

Raw layout seen on the company NAS (October 2026):

    <slide>/<slide>/<core>/<lamp>/scan/<X_Y>/<image>.tiff
                              <lamp>/stitch/<NNNN_...>.tiff

- <core> is a TMA position such as A1 or F9.
- <lamp> is the lamp setting folder, e.g. "middle lamp 275".
- Every folder inside scan/ is one field of view (FOV). Its name is the
  microscope stage position in mm, e.g. "43.591236_42.357218".
  It should hold exactly one image.

This module only lists folder names, file sizes and file times.
It never opens, moves, renames or deletes anything.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

# "43.591236_42.357218" -> x = 43.591236 mm, y = 42.357218 mm
POSITION = re.compile(r"^(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)$")


@dataclass
class Layout:
    """How the raw folders are named. Filled from settings.toml [layout]."""

    core_pattern: str = r"^[A-Z][0-9]{1,2}$"
    scan_folder: str = "scan"
    stitch_folder: str = "stitch"
    image_extensions: tuple[str, ...] = (".tif", ".tiff")
    ignore_files: tuple[str, ...] = ("Thumbs.db", "desktop.ini", ".DS_Store")
    search_depth: int = 3
    read_threads: int = 8  # FOV folders listed at the same time

    def is_core(self, name: str) -> bool:
        return re.match(self.core_pattern, name) is not None

    def is_image(self, name: str) -> bool:
        return name.lower().endswith(tuple(e.lower() for e in self.image_extensions))


def layout_from(settings: dict) -> Layout:
    """Layout from the [layout] section of settings.toml (missing keys keep their default)."""
    s = settings.get("layout", {})
    default = Layout()
    return Layout(
        core_pattern=s.get("core_pattern", default.core_pattern),
        scan_folder=s.get("scan_folder", default.scan_folder),
        stitch_folder=s.get("stitch_folder", default.stitch_folder),
        image_extensions=tuple(s.get("image_extensions", default.image_extensions)),
        ignore_files=tuple(s.get("ignore_files", default.ignore_files)),
        search_depth=s.get("search_depth", default.search_depth),
        read_threads=s.get("read_threads", default.read_threads),
    )


@dataclass
class Image:
    name: str
    size: int  # bytes
    mtime: float  # last-modified time, seconds since 1970


@dataclass
class Fov:
    name: str
    images: list[Image] = field(default_factory=list)


@dataclass
class Scan:
    """One core scanned with one lamp setting."""

    core: str
    lamp: str
    fovs: list[Fov] = field(default_factory=list)
    stitch_tiles: int | None = None  # None means there is no stitch folder
    unexpected: list[str] = field(default_factory=list)  # stray files/folders
    error: str = ""


@dataclass
class Slide:
    name: str
    path: str
    scans: list[Scan] = field(default_factory=list)
    error: str = ""


@dataclass
class Grid:
    """Shape of the FOV positions of one scan."""

    columns: int = 0
    rows: int = 0
    off_grid: int = 0  # positions that do not sit on the main scan grid
    missing: int = 0  # empty places inside the columns x rows rectangle
    duplicates: int = 0  # two FOVs at the same grid place
    bad_names: int = 0  # FOV folder names that are not "X_Y"


# ---------------------------------------------------------------- reading


def parse_position(name: str) -> tuple[float, float] | None:
    m = POSITION.match(name)
    return (float(m.group(1)), float(m.group(2))) if m else None


def _list(folder: Path, layout: Layout):
    """Return (sub-folders, files) of a folder, sorted, without ignored files."""
    folders, files = [], []
    with os.scandir(folder) as entries:
        for e in entries:
            if e.name in layout.ignore_files:
                continue
            (folders if e.is_dir() else files).append(e)
    by_name = lambda e: e.name  # noqa: E731
    return sorted(folders, key=by_name), sorted(files, key=by_name)


def find_slides(root: str | Path, layout: Layout) -> tuple[list[Path], list[str]]:
    """Find slide folders under root.

    A slide folder is a folder that directly contains core folders (A1, B2...).
    Returns (slide folders, problems for folders that could not be read).
    """
    slides: list[Path] = []
    unreadable: list[str] = []

    def visit(folder: Path, depth: int) -> None:
        try:
            subfolders, _ = _list(folder, layout)
        except OSError as exc:
            unreadable.append(f"cannot read {folder}: {exc}")
            return
        if any(layout.is_core(f.name) for f in subfolders):
            slides.append(folder)
            return
        if depth < layout.search_depth:
            for f in subfolders:
                visit(Path(f.path), depth + 1)

    visit(Path(root), 0)
    return slides, unreadable


def read_slide(folder: str | Path, layout: Layout, on_core=None) -> Slide:
    """Read one slide. on_core(name) is called before each core (for progress)."""
    folder = Path(folder)
    slide = Slide(name=folder.name, path=str(folder))
    try:
        subfolders, _ = _list(folder, layout)
    except OSError as exc:
        slide.error = f"cannot read slide folder: {exc}"
        return slide
    for f in subfolders:
        if layout.is_core(f.name):
            if on_core:
                on_core(f.name)
            slide.scans.extend(read_core(Path(f.path), layout))
    return slide


def read_core(core_dir: Path, layout: Layout) -> list[Scan]:
    """Read one core folder. Returns one Scan per lamp folder."""
    core = core_dir.name
    try:
        subfolders, _ = _list(core_dir, layout)
        if any(f.name == layout.scan_folder for f in subfolders):
            lamps = [(core_dir, "")]  # scan/ directly inside the core folder
        else:
            # List each sub-folder (instead of Path.is_dir, which hides NAS errors
            # and would report them as "no scan folder").
            lamps = [
                (Path(f.path), f.name)
                for f in subfolders
                if any(g.name == layout.scan_folder for g in _list(Path(f.path), layout)[0])
            ]
    except OSError as exc:
        return [Scan(core=core, lamp="", error=f"cannot read core folder: {exc}")]
    if not lamps:
        return [Scan(core=core, lamp="", error="no scan folder")]
    return [read_scan(path, core, lamp, layout) for path, lamp in lamps]


def _read_fov(fov_dir, layout: Layout) -> tuple[Fov, list[str]]:
    """List one FOV folder. Returns the FOV and any unexpected items in it."""
    inner_folders, files = _list(Path(fov_dir.path), layout)
    fov = Fov(name=fov_dir.name)
    unexpected = [f"{fov_dir.name}/{g.name}/" for g in inner_folders]
    for f in files:
        if layout.is_image(f.name):
            st = f.stat()
            fov.images.append(Image(f.name, st.st_size, st.st_mtime))
        else:
            unexpected.append(f"{fov_dir.name}/{f.name}")
    return fov, unexpected


def read_scan(lamp_dir: Path, core: str, lamp: str, layout: Layout) -> Scan:
    scan = Scan(core=core, lamp=lamp)
    try:
        fov_dirs, stray = _list(lamp_dir / layout.scan_folder, layout)
        scan.unexpected = [f.name for f in stray]
        # Each FOV folder costs a network round-trip on the NAS, so several
        # are listed at the same time. Results keep the folder order.
        with ThreadPoolExecutor(max_workers=layout.read_threads) as pool:
            for fov, unexpected in pool.map(lambda d: _read_fov(d, layout), fov_dirs):
                scan.fovs.append(fov)
                scan.unexpected += unexpected

        stitch = lamp_dir / layout.stitch_folder
        if stitch.is_dir():
            _, files = _list(stitch, layout)
            scan.stitch_tiles = sum(1 for f in files if layout.is_image(f.name))
    except OSError as exc:
        scan.error = f"cannot read scan folder: {exc}"
    return scan


# ------------------------------------------------------- saving / loading


def slide_to_dict(slide: Slide) -> dict:
    return asdict(slide)


def slide_from_dict(data: dict) -> Slide:
    scans = []
    for s in data["scans"]:
        fovs = [Fov(f["name"], [Image(**i) for i in f["images"]]) for f in s["fovs"]]
        scans.append(
            Scan(
                core=s["core"],
                lamp=s["lamp"],
                fovs=fovs,
                stitch_tiles=s["stitch_tiles"],
                unexpected=list(s["unexpected"]),
                error=s["error"],
            )
        )
    return Slide(name=data["name"], path=data["path"], scans=scans, error=data["error"])


# ------------------------------------------------------------ grid shape


def _main_offset(values: list[float], step: float, tolerance: float) -> float:
    """The grid offset (value modulo step) shared by the most positions."""

    def distance(a: float, b: float) -> float:
        d = abs(a - b) % step
        return min(d, step - d) / step

    residues = [v % step for v in values]
    return max(
        residues,
        key=lambda r: sum(1 for other in residues if distance(r, other) <= tolerance),
    )


def analyse_grid(fov_names: list[str], step_x: float, step_y: float, tolerance: float) -> Grid:
    """Work out the scan grid from FOV folder names.

    One core = one full rectangle of positions, `step_x` / `step_y` mm apart.
    Positions from a second core usually sit on a shifted grid ("off_grid")
    or make the rectangle too big.
    `tolerance` is a fraction of one step (0.1 = 10%).
    """
    points = [p for p in map(parse_position, fov_names) if p]
    grid = Grid(bad_names=len(fov_names) - len(points))
    if not points:
        return grid

    off_x = _main_offset([x for x, _ in points], step_x, tolerance)
    off_y = _main_offset([y for _, y in points], step_y, tolerance)

    cells: list[tuple[int, int]] = []
    for x, y in points:
        kx = (x - off_x) / step_x
        ky = (y - off_y) / step_y
        if abs(kx - round(kx)) > tolerance or abs(ky - round(ky)) > tolerance:
            grid.off_grid += 1
        else:
            cells.append((round(kx), round(ky)))

    if cells:
        cols = {c for c, _ in cells}
        rows = {r for _, r in cells}
        grid.columns = max(cols) - min(cols) + 1
        grid.rows = max(rows) - min(rows) + 1
        grid.duplicates = len(cells) - len(set(cells))
        grid.missing = grid.columns * grid.rows - len(set(cells))
    return grid
