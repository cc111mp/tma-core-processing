from __future__ import annotations

from PIL import Image, ImageDraw

from ..io.images import display, read_image
from ..montage import grid_place


def _row_index(label):
    result = 0
    for char in label:
        result = 26 * result + ord(char) - ord("A") + 1
    return result - 1


def _row_label(index):
    result = ""
    index += 1
    while index:
        index, v = divmod(index - 1, 26)
        result = chr(65 + v) + result
    return result


def make_grid(root, units, all_rows, path, tile=160, observations=None):
    placements = {c.uid: grid_place(c.core) for c in units}
    if any(p is None for p in placements.values()):
        raise ValueError("Slide grid requires explicit TMA row/column core labels")
    columns = max(p[1] for p in placements.values())
    rows = max(_row_index(p[0]) for p in placements.values()) + 1
    if rows > 64 or columns > 128:
        raise ValueError("Unreasonable TMA grid extent; inspect identity mapping")
    cellh = tile + 30
    sheet = Image.new("RGB", (columns * (tile + 8) + 8, rows * cellh + 56), "#171b22")
    draw = ImageDraw.Draw(sheet)
    draw.text(
        (8, 6),
        "Shared raw count window per illumination group. Blank = no manifest entry; acquisition plan unconfirmed.",
        fill="white",
    )
    windows = {}
    for c in units:
        score = next((r for r in all_rows.get(c.uid, []) if r["stage"] == "tile_qc"), {})
        limits = score.get("metrics", {}).get("display_window", [0.0, 1.0])
        old = windows.get(c.group, limits)
        windows[c.group] = [min(old[0], limits[0]), max(old[1], limits[1])]
    for y in range(rows):
        for x in range(columns):
            draw.text(
                (8 + x * (tile + 8), 48 + y * cellh), f"{_row_label(y)}{x + 1}", fill="#7f8b9f"
            )
    occupied = set()
    for c in units:
        label, col = placements[c.uid]
        row = _row_index(label)
        if (row, col) in occupied:
            raise ValueError("Multiple core acquisitions map to the same slide-grid position")
        occupied.add((row, col))
        x, y = 8 + (col - 1) * (tile + 8), 48 + row * cellh
        records = all_rows.get(c.uid, [])
        bad = any(r["execution"] == "error" for r in records)
        color = "#cf6a65" if bad else "#d4a44c"
        status = (
            "ERROR"
            if bad
            else "BLOCKED"
            if c.blocked_reasons or any(r["execution"] == "blocked" for r in records)
            else "NOT RUN"
            if not records
            else "REVIEW"
        )
        info = (observations or {}).get(c.uid, {})
        if info.get("observation") == "stale":
            status = "STALE"
        elif info.get("processing_finished") is False:
            status = "PARTIAL"
        elif info.get("observation") == "historical_not_reverified":
            status = "PRIOR " + status
        draw.rectangle((x, y, x + tile + 2, y + tile + 24), fill=color)
        draw.text((x + 3, y + 3), c.core + " " + status, fill="#10151d")
        image_record = next((r for r in records if r["stage"] == "mosaic_qc"), {})
        counts = image_record.get("outputs", {}).get("preview_counts")
        if counts:
            image = Image.fromarray(
                display(read_image(root / counts["path"]), windows[c.group])
            ).convert("RGB")
            image.thumbnail((tile - 4, tile - 4))
            sheet.paste(image, (x + 3, y + 23))
        else:
            draw.text((x + 5, y + 50), "No reconstruction", fill="#10151d")
    sheet.save(path)
    return {"windows": windows, "rows": rows, "columns": columns}
