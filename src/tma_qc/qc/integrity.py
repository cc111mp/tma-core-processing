from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import numpy as np
import tifffile

from ..io.images import read_image
from ..io.tiff_validation import validate_tiff_plane
from ..io.tile_cache import file_identity, stat_identity


def verify_file(path, expected=None, *, identities=None):
    path = Path(os.path.abspath(path))
    before = file_identity(path)
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or stat_identity(opened) != before:
            raise ValueError(f"Source changed before SHA256 verification: {path}")
        while block := os.read(descriptor, 8 * 1024 * 1024):
            digest.update(block)
        if stat_identity(os.fstat(descriptor)) != before or file_identity(path) != before:
            raise ValueError(f"Source changed while SHA256 was being verified: {path}")
    finally:
        os.close(descriptor)
    actual = digest.hexdigest()
    if expected and actual != expected:
        raise ValueError(f"Source SHA256 mismatch: {path}")
    if identities is not None:
        previous = identities.get(path)
        if previous is not None and previous != before:
            raise ValueError(f"Verified source changed after SHA256 verification: {path}")
        identities[path] = before
    return actual


def verify_core(core, manifest, profile, *, decode=True, reader=None, identities=None, paths=None):
    identities = identities if identities is not None else {}
    if not core.fovs:
        raise ValueError(f"{core.uid}: no raw FOVs to verify")
    sources = {}
    shape = None
    for fov in core.fovs:
        path = paths[fov.id] if paths is not None else manifest.raw_path(fov)
        sources[str(path)] = verify_file(path, fov.sha256, identities=identities)
        with tifffile.TiffFile(path, _multifile=False) as tif:
            series = validate_tiff_plane(tif, label=f"FOV {fov.id}")
            dtype = np.dtype(series.dtype).newbyteorder("=")
            if (
                series.axes != "YX"
                or dtype != np.dtype(profile["dtype"])
                or list(series.shape) != profile["shape"]
            ):
                raise ValueError(
                    f"FOV {fov.id}: expected {profile}, found {series.shape}/{series.dtype}/{series.axes}"
                )
            shape = series.shape
        if decode:
            a = (reader or read_image)(path)
            if a.dtype != np.dtype(profile["dtype"]) or list(a.shape) != profile["shape"]:
                raise ValueError(f"FOV {fov.id}: expected {profile}, found {a.shape}/{a.dtype}")
    for path, identity in identities.items():
        file_identity(path, identity)
    return {
        "verified_files": len(sources),
        "shape": list(shape),
        "dtype": profile["dtype"],
        "decoded_files": len(sources) if decode else 0,
        "validation_scope": "manifest_listed_files_and_native_pixels"
        if decode
        else "manifest_listed_file_hashes_headers_and_segment_bounds",
        "physical_acquisition_completeness": "not_assessed",
    }, sources
