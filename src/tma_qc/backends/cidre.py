from __future__ import annotations

import os
import subprocess
from pathlib import Path

import cv2
import numpy as np
import tifffile

from ..io.images import is_uint16, read_image, write_tiff
from ..provenance import read_json, sha256, write_json

UPSTREAM_COMMIT = "10e1d8e8f80635349d65ead6b19cf3a85f8a225a"


def validate_fields(flatfield, darkfield):
    s, d = np.asarray(flatfield, np.float32), np.asarray(darkfield, np.float32)
    if s.ndim != 2 or d.ndim != 2 or s.shape != d.shape:
        raise ValueError("Illumination fields must be matching 2D arrays")
    if not np.isfinite(s).all() or not np.isfinite(d).all() or np.any(s <= 0):
        raise ValueError("Illumination fields must be finite; flatfield must be strictly positive")
    return s, d


def apply_field(image, flatfield, darkfield):
    """Original dynamic-range-corrected formula; preserve all finite float counts."""
    a = np.asarray(image)
    if a.ndim != 2 or not is_uint16(a.dtype):
        raise ValueError("CIDRE AF profile requires 2D uint16 camera data")
    s, d = validate_fields(flatfield, darkfield)
    h, w = a.shape
    s = cv2.resize(s, (w, h), interpolation=cv2.INTER_LINEAR)
    d = cv2.resize(d, (w, h), interpolation=cv2.INTER_LINEAR)
    out = (a.astype(np.float32) - d) / s
    if not np.isfinite(out).all():
        raise ValueError("Nonfinite corrected counts")
    return out


def fit_model(
    inputs: list[Path], output: Path, runtime: Path, downsample: int = 4, timeout: int = 1800
):
    """Run the pinned, previously built Java adapter on an explicit input pool.

    Caller owns group compatibility and source verification. No hidden sampling,
    numerical-source modifications or claim of method validation.
    """
    build = read_json(runtime / "build.json")
    if build.get("upstream_commit") != UPSTREAM_COMMIT:
        raise ValueError("CIDRE runtime does not match the pinned upstream commit")
    for rel, digest in build["classes"].items():
        if sha256(runtime / rel) != digest:
            raise ValueError(f"CIDRE runtime class hash mismatch: {rel}")
    if sha256(runtime / "lib" / "ij.jar") != build["imagej_sha256"]:
        raise ValueError("ImageJ dependency hash mismatch")
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    training = output / "training"
    training.mkdir()
    sources = []
    for n, path in enumerate(inputs):
        a = read_image(path)
        h, w = a.shape
        if not is_uint16(a.dtype) or h % downsample or w % downsample:
            raise ValueError("Fitting requires uint16 images divisible by downsample")
        q = a.reshape(h // downsample, downsample, w // downsample, downsample).mean(
            (1, 3), dtype=np.float32
        )
        # Classic TIFF is required by the pinned ImageJ reader, even for float inputs.
        tifffile.imwrite(training / f"{n:05d}.tif", q, bigtiff=False)
        sources.append({"path": str(path), "sha256": sha256(path)})
    command = [
        "xvfb-run",
        "-a",
        "java",
        "-Xmx6g",
        "-cp",
        os.pathsep.join([str(runtime / "classes"), str(runtime / "lib" / "ij.jar")]),
        "CidreBatch",
        str(training),
        str(output),
    ]
    write_json(output / "command.json", command)
    with (output / "fit.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=True)
    metadata = read_json(output / "adapter-summary.json")

    def binary(name):
        return (
            np.fromfile(output / name, dtype=">f8").reshape(metadata["width"], metadata["height"]).T
        )

    v, d = binary("v-double.bin"), binary("z-double.bin")
    s, d = validate_fields(v / metadata["mean_v"], d)
    write_tiff(output / "flatfield.tif", s)
    write_tiff(output / "darkfield.tif", d)
    record = {
        "upstream_commit": UPSTREAM_COMMIT,
        "mode": "dynamic_range_corrected",
        "sources": sources,
        "quality_controls": "not_passed",
        "flatfield_sha256": sha256(output / "flatfield.tif"),
        "darkfield_sha256": sha256(output / "darkfield.tif"),
    }
    write_json(output / "model.json", record)
    return record
