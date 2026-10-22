"""One big picture per slide, every core in its TMA position.

For each registered slide three PNGs are written:
  <slide>_AF.png       registered AF, same brightness window for all cores (0 to 99.5th pct)
                       (so brightness differences between cores stay visible)
  <slide>_HE.png       registered HE
  <slide>_overlay.png  AF in green, HE tissue in magenta. Where they line up the
                       tissue looks white/grey; green or magenta fringes mean
                       misalignment.

Each tile is labelled with its core name. If a registration report
(registration.csv) is given, the tile border shows its status:
green OK, orange CHECK, red ALERT.
"""

from __future__ import annotations

import csv
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .preview import cached_preview
from .raw_layout import find_slides, layout_from
from .registration_check import registration_dirs

CORE = re.compile(r"^([A-Z]+)(\d+)$")
BORDER = {"OK": (46, 125, 50), "CHECK": (239, 143, 0), "ALERT": (198, 40, 40)}
NO_STATUS = (90, 90, 90)
LABEL_PX = 16
EDGE_PX = 3


def grid_place(core: str) -> tuple[str, int] | None:
    """("B", 12) for core "B12"; None if the name is not letters + a column number >= 1."""
    m = CORE.match(core)
    return (m.group(1), int(m.group(2))) if m and int(m.group(2)) >= 1 else None


def load_statuses(report_csv) -> dict:
    """{(slide, core): status} from a registration.csv."""
    if not report_csv:
        return {}
    with open(report_csv, encoding="utf-8-sig") as f:
        return {(r["slide"], r["core"]): r["status"] for r in csv.DictReader(f)}


def _load_core(core_dir: Path, size: int, cache: Path):
    """Return (af preview or None, he preview or None, note)."""
    try:  # a broken file or a NAS error must not stop the whole slide
        regs = registration_dirs(core_dir)
        if not regs:
            return None, None, "not registered"
        af = sorted(regs[-1].glob("*_AF_Reg.tif"))
        he = sorted(regs[-1].glob("*_Stain_Reg.tif"))
        return (
            cached_preview(af[0], size, cache) if af else None,
            cached_preview(he[0], size, cache) if he else None,
            "" if af and he else "image missing",
        )
    except Exception as exc:
        return None, None, f"cannot read: {type(exc).__name__}"


def _af_window(previews) -> tuple[float, float]:
    """Brightness window shared by all cores of a slide: 0 to the 99.5th percentile.

    Starting at 0 keeps dim tissue and any background offset visible.
    """
    values = [p[p > 0] for p in previews if p is not None]
    values = np.concatenate(values) if values else np.array([])
    hi = float(np.percentile(values, 99.5)) if values.size else 1.0
    return 0.0, max(hi, 1.0)


def _af_display(af, window) -> np.ndarray:
    lo, hi = window
    return (np.clip((af.astype(np.float32) - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)


def _he_tissue(he) -> np.ndarray:
    """0 = background (white glass), 255 = dark tissue."""
    gray = he[..., :3].astype(np.float32).mean(axis=2)
    return (np.clip((235 - gray) / 155, 0, 1) * 255).astype(np.uint8)


def _fit(image: np.ndarray, tile: int) -> Image.Image:
    """Centre the image on a black tile x tile square."""
    img = Image.fromarray(image).convert("RGB")
    img.thumbnail((tile, tile))
    canvas = Image.new("RGB", (tile, tile))
    canvas.paste(img, ((tile - img.width) // 2, (tile - img.height) // 2))
    return canvas


def _sheet(slide_name, tiles: dict, statuses: dict, tile: int, title: str) -> Image.Image:
    """Arrange {core: tile image or note} on the TMA grid (rows A, B..., columns 1, 2...)."""
    places = {c: grid_place(c) for c in tiles}
    rows = sorted({row for row, _ in places.values()}, key=lambda r: (len(r), r))
    cols = max(col for _, col in places.values())
    cell_w, cell_h = tile + 2 * EDGE_PX, tile + 2 * EDGE_PX + LABEL_PX
    top = 28
    sheet = Image.new("RGB", (cols * cell_w + 8, len(rows) * cell_h + top + 4), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    draw.text((6, 6), f"{slide_name} - {title}", fill=(240, 240, 240))
    for core, content in tiles.items():
        row, col = places[core]
        x = 4 + (col - 1) * cell_w
        y = top + rows.index(row) * cell_h
        status = statuses.get((slide_name, core), "")
        draw.rectangle(
            [x, y + LABEL_PX, x + cell_w - 1, y + cell_h - 1], fill=BORDER.get(status, NO_STATUS)
        )
        draw.text((x + 2, y + 2), f"{core} {status}".strip(), fill=(240, 240, 240))
        if isinstance(content, Image.Image):
            sheet.paste(content, (x + EDGE_PX, y + LABEL_PX + EDGE_PX))
        else:
            draw.rectangle(
                [
                    x + EDGE_PX,
                    y + LABEL_PX + EDGE_PX,
                    x + EDGE_PX + tile - 1,
                    y + LABEL_PX + EDGE_PX + tile - 1,
                ],
                fill=(45, 45, 45),
            )
            draw.text((x + 8, y + LABEL_PX + tile // 2), content, fill=(230, 230, 230))
    return sheet


def make_slide_montages(slide_dir: Path, out: Path, settings: dict, statuses: dict) -> list[Path]:
    m = settings["montage"]
    tile, cache = m["tile_px"], Path(m["cache_dir"] or out / "preview_cache")
    layout = layout_from(settings)
    # Core folders as defined in settings.toml; they also need a row letter and a
    # column number (A1, B12...) to be placed on the grid.
    cores = sorted(p for p in slide_dir.iterdir() if p.is_dir() and layout.is_core(p.name))
    skipped = [p.name for p in cores if grid_place(p.name) is None]
    cores = [p for p in cores if p.name not in skipped]
    if skipped:
        print(f"    not placed on the grid (name is not letter + number): {', '.join(skipped)}")
    if not cores:
        print("    no cores to draw")
        return []
    with ThreadPoolExecutor(max_workers=m["read_threads"]) as pool:
        loaded = dict(
            zip((c.name for c in cores), pool.map(lambda c: _load_core(c, tile, cache), cores))
        )

    window = _af_window(af for af, _, _ in loaded.values())
    af_tiles, he_tiles, overlay_tiles = {}, {}, {}
    for core, (af, he, note) in loaded.items():
        af_tiles[core] = _fit(_af_display(af, window), tile) if af is not None else note
        he_tiles[core] = _fit(he[..., :3], tile) if he is not None else note
        if af is not None and he is not None:
            tissue = np.array(Image.fromarray(_he_tissue(he)).resize(af.shape[::-1]))
            green = _af_display(af, window)
            overlay_tiles[core] = _fit(np.dstack([tissue, green, tissue]), tile)
        else:
            overlay_tiles[core] = note

    name = slide_dir.name
    written = []
    for suffix, tiles, title in (
        ("AF", af_tiles, "registered AF (shared brightness window)"),
        ("HE", he_tiles, "registered HE"),
        ("overlay", overlay_tiles, "overlay: AF green, HE magenta, aligned = white"),
    ):
        path = out / f"{name}_{suffix}.png"
        _sheet(name, tiles, statuses, tile, title).save(path)
        written.append(path)
    return written


def run_montage(data_folder, out_folder, settings, registration_csv=None) -> Path:
    out = Path(out_folder)
    out.mkdir(parents=True, exist_ok=True)
    statuses = load_statuses(registration_csv)
    slides, unreadable = find_slides(Path(data_folder), layout_from(settings))
    for problem in unreadable:
        print("WARNING:", problem)
    print(f"Found {len(slides)} slide folders under {data_folder}")
    for n, slide in enumerate(slides, 1):
        print(f"[{n}/{len(slides)}] {slide.name} ...", flush=True)
        try:
            written = make_slide_montages(slide, out, settings, statuses)
        except OSError as exc:  # e.g. the NAS dropped; carry on with the next slide
            print(f"    WARNING: cannot read {slide}: {exc}")
            continue
        for path in written:
            print(f"    {path.name}")
    print(f"Montages written to {out}")
    return out
