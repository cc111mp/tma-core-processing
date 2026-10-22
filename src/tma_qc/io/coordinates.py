from __future__ import annotations

import math

from ..provenance import read_json

FRAME = {"coordinate_space": "native_pixels", "axes": "XY", "origin": "upper_left"}


def position_document(rows, **metadata):
    return {**metadata, **FRAME, "positions": rows}


def load_positions(path, fov_ids):
    value = read_json(path)
    if not isinstance(value, dict) or any(value.get(k) != v for k, v in FRAME.items()):
        raise ValueError(
            "Positions must declare coordinate_space=native_pixels, axes=XY and origin=upper_left; convert reviewed legacy arrays explicitly"
        )
    rows = value["positions"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("Positions must contain a nonempty list")
    result = []
    for row in rows:
        if not isinstance(row, dict) or not {"x", "y"} <= row.keys():
            raise ValueError("Each position must be an object with x and y coordinates")
        ident = str(row.get("id", row.get("index", "")))
        x, y = row["x"], row["y"]
        if any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or v < 0
            or int(v) != v
            for v in [x, y]
        ):
            raise ValueError(
                "Current native blender requires explicit nonnegative integer pixel positions"
            )
        result.append({"id": ident, "x": int(x), "y": int(y)})
    if len({r["id"] for r in result}) != len(result) or {r["id"] for r in result} != set(fov_ids):
        raise ValueError("Position ids must match all FOV ids exactly once")
    if len({(r["x"], r["y"]) for r in result}) != len(result):
        raise ValueError("Duplicate tile positions require review")
    return result


def overlap_pairs(positions, shape):
    h, w = shape
    for i, a in enumerate(positions):
        for b in positions[i + 1 :]:
            x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
            x1, y1 = min(a["x"] + w, b["x"] + w), min(a["y"] + h, b["y"] + h)
            if x1 > x0 and y1 > y0:
                yield a, b, (x0, y0, x1, y1)
