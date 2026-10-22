"""Bounded, labelled evidence views; no changes to scientific image pixels."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from ..io.images import display, reduced


def overlap_sheet(raw, positions, edges, output, *, reader, corrected=None, count=4, crop=256):
    """Show native overlap centre crops with explicit coordinates and count scale."""
    poses = {p["id"]: p for p in positions}
    edges = edges[:count]
    columns = 4 if corrected is not None else 3
    cell = min(crop, 256)
    canvas = Image.new("RGB", (columns * cell, max(1, len(edges)) * (cell + 62)), "#171b22")
    draw = ImageDraw.Draw(canvas)
    records = []
    if not edges:
        draw.text((8, 8), "No assessable overlaps; this is not a quality pass.", fill="white")
    for n, edge in enumerate(edges):
        x0, y0, x1, y1 = edge["overlap_bbox_xyxy"]
        left, top = max(x0, (x0 + x1 - crop) // 2), max(y0, (y0 + y1 - crop) // 2)
        right, bottom = min(x1, left + crop), min(y1, top + crop)
        box = [left, top, right, bottom]

        def patch(frames, ident):
            p = poses[ident]
            return np.asarray(
                reader(frames[ident])[
                    top - p["y"] : bottom - p["y"], left - p["x"] : right - p["x"]
                ]
            )

        a, b = patch(raw, edge["a"]), patch(raw, edge["b"])
        lo, hi = np.percentile(np.concatenate([a.ravel(), b.ravel()]), [1, 99.5])
        limits = float(lo), float(max(hi, lo + 1))
        images = [display(a, limits), display(b, limits)]
        labels = ["Raw " + edge["a"], "Raw " + edge["b"]]
        if corrected is None:
            aa, bb = images
            images.append(np.stack([aa, bb, bb], axis=-1))
            labels.append("A red / B cyan")
        else:
            images.extend(
                [
                    display(patch(corrected, edge["a"]), limits),
                    display(patch(corrected, edge["b"]), limits),
                ]
            )
            labels.extend(["Corrected " + edge["a"], "Corrected " + edge["b"]])
        y = n * (cell + 62)
        draw.text((6, y + 2), f"{edge['a']} / {edge['b']}  native XY box {box}", fill="white")
        if corrected is None:
            detail = (
                "Residual XY px: "
                + str(edge.get("phase_residual_native_pixels"))
                + "; phase response: "
                + str(edge.get("phase_response"))
            )
        else:
            detail = "Matched-pixel absolute difference p95, raw / corrected counts: " + " / ".join(
                f"{edge[key]:.4g}"
                for key in (
                    "raw_absolute_difference_p95_counts",
                    "corrected_absolute_difference_p95_counts",
                )
            )
        draw.text((6, y + 18), detail, fill="white")
        for i, (image, label) in enumerate(zip(images, labels)):
            draw.text((i * cell + 6, y + 36), label, fill="white")
            tile = Image.fromarray(image).convert("RGB")
            tile.thumbnail((cell, cell))
            canvas.paste(tile, (i * cell, y + 60))
        records.append(
            {
                "a": edge["a"],
                "b": edge["b"],
                "crop_bbox_xyxy": box,
                "display_window_counts": list(limits),
            }
        )
    canvas.save(output)
    return {
        "coordinate_space": "native_af_pixels",
        "crop_selection": "Overlap centre; not a semantic artifact mask",
        "pairs": records,
    }


def boundary_view(raw, corrected, coverage, report, limits, output, size=1024):
    """Layout midlines on equal-scale raw/corrected previews, not defect masks."""
    step = max(1, int(np.ceil(max(raw.shape) / size)))
    valid = reduced(coverage, size).astype(bool)
    panels = []
    for source in (raw, corrected):
        image = display(reduced(source, size), limits)
        image[~valid] = 0
        panels.append(Image.fromarray(image).convert("RGB"))
    for panel in panels:
        draw = ImageDraw.Draw(panel)
        for seam in report["seams"]:
            x0, y0, x1, y1 = seam["overlap_bbox_xyxy"]
            cut = seam["midline"]["cut_native_pixel"]
            points = (
                [(cut / step, y0 / step), (cut / step, (y1 - 1) / step)]
                if seam["axis"] == "x"
                else [(x0 / step, cut / step), ((x1 - 1) / step, cut / step)]
            )
            draw.line(points, fill="#ffba63", width=1)
    w, h = panels[0].size
    canvas = Image.new("RGB", (2 * w, h + 48), "#171b22")
    draw = ImageDraw.Draw(canvas)
    draw.text((6, 4), "Known overlap midlines - layout only, not detected artifacts", fill="white")
    draw.text((6, 24), "Raw reconstruction", fill="white")
    draw.text((w + 6, 24), "Corrected; same raw count window", fill="white")
    for i, panel in enumerate(panels):
        canvas.paste(panel, (i * w, 48))
    canvas.save(output)


def projection_view(report, output, *, title=None):
    """Compare raw/corrected profiles while retaining unsupported gaps."""
    width, height = 512, 230
    canvas = Image.new("RGB", (2 * width, 2 * height + 30), "#171b22")
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (8, 6),
        title or "Mask-normalized projections: raw orange / corrected cyan; not GRID probabilities",
        fill="white",
    )
    for n, row in enumerate(report["measurements"]):
        x0, y0 = (n % 2) * width, (n // 2) * height + 30
        scope = f" / strip {row['strip_index']}" if row.get("scope") == "strip" else ""
        draw.text(
            (x0 + 8, y0 + 5),
            f"{row['axis'].upper()} / {row['stratum']}{scope} / {row['assessment']}",
            fill="white",
        )
        pitch = row["tile_pitch_native_pixels"]
        draw.text(
            (x0 + 8, y0 + 21),
            f"Tile pitch: {pitch} native px; bins {row['supported_bins']}/{row['total_bins']}",
            fill="white",
        )
        profiles = [row["raw_profile_counts"], row["corrected_profile_counts"]]
        values = [v for profile in profiles for v in profile if v is not None]
        if not values:
            draw.text((x0 + 8, y0 + 60), "No supported profile", fill="white")
            continue
        low, high = min(values), max(values)
        draw.text((x0 + 8, y0 + 40), f"Counts: {low:.3g} to {high:.3g}", fill="white")
        for profile, color in zip(profiles, ("#ffba63", "#83d4e3")):
            segment = []
            for i, value in enumerate(profile):
                if value is not None:
                    segment.append(
                        (
                            x0 + 16 + i * (width - 32) / max(1, len(profile) - 1),
                            y0 + 190 - (value - low) * 120 / max(high - low, 1.0),
                        )
                    )
                else:
                    if len(segment) > 1:
                        draw.line(segment, fill=color)
                    segment = []
            if len(segment) > 1:
                draw.line(segment, fill=color)
        draw.text(
            (x0 + 16, y0 + 202),
            f"XY region: {row['region_bbox_xyxy']}"
            if row.get("scope") == "strip"
            else f"0 to {row['coordinate_native_pixels'][-1]} native pixels",
            fill="white",
        )
    canvas.save(output)
