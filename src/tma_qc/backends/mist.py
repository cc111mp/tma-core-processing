from __future__ import annotations

import glob
import math
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import tifffile

from ..io.images import read_image
from ..provenance import write_json
from ..schema import NeedsInput


def classpath_files(settings, *, required=True):
    paths = []
    for part in settings["mist_classpath"].split(os.pathsep):
        found = [Path(p) for p in glob.glob(part) if Path(p).is_file()]
        if not found and required:
            raise NeedsInput(f"Missing MIST classpath entry: {part}")
        paths.extend(found)
    return sorted(set(paths))


def validate_layout(core, settings, shape=None):
    layout = core.metadata.get("grid", {})
    required = {
        "rows",
        "columns",
        "numbering_pattern",
        "origin",
        "horizontal_overlap",
        "vertical_overlap",
    }
    if settings.get("input") == "corrected":
        required.add("overlap_uncertainty")
    if (
        not isinstance(layout, dict)
        or not required <= layout.keys()
        or not settings["mist_classpath"]
    ):
        raise NeedsInput("MIST requires explicit reviewed grid layout, overlaps and mist_classpath")
    if core.metadata.get("grid_reviewed", False) is not True:
        raise NeedsInput("Review image-axis scan direction before automatic MIST estimation")
    if core.metadata.get("sequence_reviewed", True) is not True:
        raise NeedsInput(
            "Review and order the FOV acquisition sequence explicitly; XY folder sort order is unconfirmed"
        )
    rows, columns = layout["rows"], layout["columns"]
    if any(type(v) is not int or v < 1 for v in (rows, columns)):
        raise ValueError("MIST grid dimensions must be positive integers")
    if rows * columns != len(core.fovs):
        raise ValueError("MIST grid dimensions do not match FOV count")
    for name in ("horizontal_overlap", "vertical_overlap"):
        value = layout[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value < 100
        ):
            raise ValueError("MIST overlaps must be finite percentages in [0, 100)")
    if settings.get("input") == "corrected":
        uncertainty = layout["overlap_uncertainty"]
        if (
            isinstance(uncertainty, bool)
            or not isinstance(uncertainty, (int, float))
            or not math.isfinite(uncertainty)
            or not 0 <= uncertainty <= 100
        ):
            raise ValueError("MIST overlap_uncertainty must be a finite percentage in [0, 100]")
    factor = settings["downsample"]
    if type(factor) is not int or factor < 1:
        raise ValueError("geometry.downsample must be positive")
    if shape and any(v % factor for v in shape):
        raise ValueError("Geometry downsample must divide native shape")
    return layout, factor


def estimate_positions(core, manifest, output, settings, *, reader=None, frames=None, timings=None):
    reader = reader or read_image
    layout, factor = validate_layout(core, settings)
    rows, columns = layout["rows"], layout["columns"]
    inputs = output / "mist-inputs"
    inputs.mkdir()
    started = time.perf_counter()
    corrected = settings.get("input", "raw") == "corrected"
    # Sequence is explicitly the manifest order, never guessed from stage coordinates.
    for n, fov in enumerate(core.fovs, 1):
        a = reader(frames[fov.id] if corrected else manifest.raw_path(fov))
        h, w = a.shape
        if h % factor or w % factor:
            raise ValueError("Geometry downsample must divide native shape")
        if corrected:
            if factor != 1 or a.dtype != np.dtype("=f4") or not np.isfinite(a).all():
                raise ValueError("Corrected MIST requires finite native float32 tiles")
            q = a
        else:
            q = a.reshape(h // factor, factor, w // factor, factor).mean((1, 3))
        # ImageJ's native reader needs classic TIFF for these small registration inputs.
        tifffile.imwrite(
            inputs / f"FOV_{n:05d}.tif",
            q if corrected else np.rint(q).astype(np.uint16),
            bigtiff=False,
            compression=None,
        )
    if timings is not None:
        timings["corrected_staging_seconds"] = time.perf_counter() - started
    options = dict(
        headless="true",
        programType="JAVA",
        isUseBioFormats="false",
        numCPUThreads=settings["threads"] if corrected else 2,
        filenamePattern="FOV_{ppppp}.tif",
        filenamePatternType="SEQUENTIAL",
        numberingPattern=layout["numbering_pattern"],
        gridOrigin=layout["origin"],
        gridWidth=columns,
        gridHeight=rows,
        extentWidth=columns,
        extentHeight=rows,
        startTile=1,
        imageDir=inputs,
        outputPath=output,
        outputFullImage="false",
        outputMeta="true",
        displayStitching="false",
        isSuppressModelWarningDialog="true",
        horizontalOverlap=layout["horizontal_overlap"],
        verticalOverlap=layout["vertical_overlap"],
        overlapUncertainty=layout.get("overlap_uncertainty", 3),
        logLevel="MANDATORY",
        debugLevel="NONE",
    )
    command = [
        "java",
        "-Xmx8g",
        "-Djava.awt.headless=true",
        "-cp",
        settings["mist_classpath"],
        "gov.nist.isg.mist.MISTMain",
    ]
    for key, value in options.items():
        command.extend(["--" + key, str(value)])
    write_json(output / "mist-command.json", command)
    started = time.perf_counter()
    with (output / "mist.log").open("w") as log:
        subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=settings["timeout_seconds"],
            check=True,
        )
    if timings is not None:
        timings["mist_seconds"] = time.perf_counter() - started
    log_text = (output / "mist.log").read_text()
    if (
        not (output / "img-global-positions-1.txt").is_file()
        or "Error occurred in stitching worker" in log_text
    ):
        raise RuntimeError("MIST did not produce complete geometry: " + log_text[-1800:])
    pattern = re.compile(
        r"file: FOV_(\d+)\.tif; corr: ([^;]+); position: \((-?\d+), (-?\d+)\); grid: \((\d+), (\d+)\);"
    )
    positions = []
    for line in (output / "img-global-positions-1.txt").read_text().splitlines():
        if not line.strip():
            continue
        match = pattern.fullmatch(line.strip())
        if not match:
            raise ValueError(f"Unsupported MIST position record: {line}")
        n, corr, x, y, _, _ = match.groups()
        index = int(n) - 1
        if not 0 <= index < len(core.fovs):
            raise ValueError("Unexpected tile number from MIST")
        positions.append({"id": core.fovs[index].id, "x": int(x) * factor, "y": int(y) * factor})
    if len(positions) != len(core.fovs) or {r["id"] for r in positions} != {
        f.id for f in core.fovs
    }:
        raise RuntimeError("MIST did not produce complete geometry for every manifest FOV")
    if positions:
        minx, miny = min(r["x"] for r in positions), min(r["y"] for r in positions)
        for row in positions:
            row["x"] -= minx
            row["y"] -= miny
    return positions
