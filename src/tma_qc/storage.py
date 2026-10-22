"""Conservative AF capacity estimates and guards before large allocations."""

from __future__ import annotations

import math
import os
import shutil
from pathlib import Path

from .io.coordinates import load_positions
from .schema import NeedsInput

DEFAULT_STORAGE = {"reserve_bytes": 256 * 1024 * 1024, "safety_factor": 1.2}


def validate_writable_tree(path, raw_root=None):
    """Reject pre-existing aliases without traversing their targets.

    Output and cache trees are application-owned. They must have no symbolic
    links and no other process may replace their directories during execution.
    Inspection is limited to existing entries inside the chosen writable tree.
    """
    root = Path(path).expanduser().resolve()
    if raw_root is not None:
        raw_root = Path(raw_root).resolve()
        if root.is_relative_to(raw_root) or raw_root.is_relative_to(root):
            raise ValueError("Writable processing and raw archive directory trees must be disjoint")
    if not root.exists():
        return root
    if not root.is_dir():
        raise ValueError(f"Writable processing target is not a directory: {root}")
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            raise ValueError(
                                f"Writable processing tree contains a symbolic link: {entry.path}"
                            )
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                    except FileNotFoundError:
                        continue  # A cooperating run may remove its temporary scratch entry.
        except FileNotFoundError:
            continue
    return root


def existing_parent(path):
    path = Path(path).expanduser().resolve()
    while not path.exists():
        path = path.parent
    if not path.is_dir():
        raise ValueError(f"Storage target is not a directory: {path}")
    return path


def volume_info(path):
    existing = existing_parent(path)
    return {
        "device": existing.stat().st_dev,
        "free_bytes": shutil.disk_usage(existing).free,
        "existing_path": str(existing),
    }


def space_budget(requests, settings=None):
    """Combine simultaneous requests sharing a filesystem; count reserve once."""
    settings = settings or DEFAULT_STORAGE
    volumes = {}
    for path, size, label in requests:
        info = volume_info(path)
        volume = volumes.setdefault(info["device"], {**info, "requested_bytes": 0, "requests": []})
        volume["free_bytes"] = min(volume["free_bytes"], info["free_bytes"])
        volume["requested_bytes"] += int(size)
        volume["requests"].append({"path": str(path), "bytes": int(size), "purpose": label})
    for volume in volumes.values():
        volume["required_bytes"] = (
            math.ceil(volume["requested_bytes"] * settings["safety_factor"])
            + settings["reserve_bytes"]
        )
        volume["sufficient"] = volume["free_bytes"] >= volume["required_bytes"]
    return list(volumes.values())


def require_space(requests, settings=None):
    volumes = space_budget(requests, settings)
    for volume in volumes:
        if not volume["sufficient"]:
            raise NeedsInput(
                f"Insufficient storage at {volume['existing_path']}: need {volume['required_bytes']} bytes "
                f"including margin/reserve, available {volume['free_bytes']} bytes"
            )
    return volumes


def canvas_shape(positions, tile_shape):
    h, w = tile_shape
    return max(p["y"] for p in positions) + h, max(p["x"] for p in positions) + w


def estimate_core(core, config, pixels):
    h, w = config["profile"]["shape"]
    tile_pixels = h * w
    n = len(core.fovs)
    correction = (
        (n * tile_pixels * 4 + tile_pixels * 8) if config["illumination"]["mode"] == "cidre" else 0
    )
    mist = 0
    if not core.positions and config["geometry"]["backend"] == "mist":
        factor = config["geometry"]["downsample"]
        mist = n * (h // factor) * (w // factor) * 2
    # Two float32 mosaics + two uint8 coverage maps + two uint8 support maps.
    images = pixels * 12
    previews = 16 * config["report"]["preview_size"] ** 2 + 16 * 1024 * 1024
    return {
        "uid": core.uid,
        "canvas_pixels": pixels,
        "retained_bytes_estimate": correction + mist + images + previews + (n + 12) * 4096,
        "peak_blend_scratch_bytes": 0,
    }


def estimate_batch(manifest, config, output, selected=None):
    estimates, unknown = [], []
    for core in manifest.cores:
        if selected and core.uid not in selected or core.blocked_reasons:
            continue
        try:
            if core.positions:
                positions = load_positions(core.positions, [f.id for f in core.fovs])
                h, w = canvas_shape(positions, config["profile"]["shape"])
                estimated_geometry = False
            elif config["geometry"]["backend"] == "mist":
                from .backends.mist import validate_layout

                layout, _ = validate_layout(core, config["geometry"], config["profile"]["shape"])
                h = layout["rows"] * config["profile"]["shape"][0]
                w = layout["columns"] * config["profile"]["shape"][1]
                estimated_geometry = True
            else:
                raise NeedsInput("Native geometry unavailable")
            if h * w > config["mosaic"]["max_pixels"]:
                raise ValueError("Canvas exceeds mosaic.max_pixels")
            estimates.append(
                {**estimate_core(core, config, h * w), "geometry_is_estimate": estimated_geometry}
            )
        except (OSError, ValueError, KeyError, TypeError, NeedsInput) as exc:
            unknown.append({"uid": core.uid, "reason": str(exc)})
    retained = sum(row["retained_bytes_estimate"] for row in estimates)
    scratch = max((row["peak_blend_scratch_bytes"] for row in estimates), default=0)
    requests = [
        (output, retained, "AF outputs for one full rebuild"),
        (config["runtime"]["cache_root"], scratch, "Peak serial AF blending scratch"),
    ]
    volumes = space_budget(requests, config["storage"]) if output else []
    return {
        "cores": estimates,
        "unknown_cores": unknown,
        "retained_bytes_estimate": retained,
        "peak_blend_scratch_bytes": scratch,
        "volumes": volumes,
        "complete": not unknown and bool(output),
        "sufficient": bool(output) and not unknown and all(v["sufficient"] for v in volumes),
        "scope": "Additional space for one AF rebuild; existing artifacts are retained. Serial execution. H&E exports/decoding, other processes and future revisions are excluded. MIST canvases are estimates; native stages recheck actual allocations.",
    }
