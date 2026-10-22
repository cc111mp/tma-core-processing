"""A per-core, byte-limited cache of immutable raw FOV arrays."""

import os
from collections import OrderedDict
from pathlib import Path

from .images import read_image


def stat_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def file_identity(path, expected=None):
    identity = stat_identity(path.stat())
    if expected is not None and identity != expected:
        raise ValueError(f"Verified source changed after SHA256 verification: {path}")
    return identity


class TileCache:
    """Retain only explicitly allowed raw tiles, never a mosaic or H&E WSI.

    The budget covers retained array bytes. Active callers, decoder buffers and
    the rest of the pipeline have separate working memory. SHA256 verification
    remains the caller's responsibility. Verified filesystem identities freeze
    that hash check across cache hits, eviction and a disabled retention budget.
    """

    def __init__(self, paths, max_bytes, *, identities=None):
        if type(max_bytes) is not int or max_bytes < 0:
            raise ValueError("Tile cache budget must be a nonnegative integer")
        self.paths = {Path(os.path.abspath(path)) for path in paths}
        self.identities = identities if identities is not None else {}
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.bytes = self.peak_bytes = self.hits = self.misses = self.evictions = 0

    def read(self, path):
        # Preserve the frozen pathname: resolving a swapped parent symlink here
        # would move it outside the set of verified sources and bypass the pin.
        path = Path(os.path.abspath(path))
        if path not in self.paths:
            return read_image(path)
        identity = file_identity(path, self.identities.get(path))
        entry = self.entries.pop(path, None)
        if entry is not None:
            previous, image = entry
            if previous == identity:
                self.entries[path] = entry
                self.hits += 1
                return image
            self.bytes -= image.nbytes
        self.misses += 1
        image = read_image(path)
        if file_identity(path) != identity:
            raise ValueError(f"Raw FOV changed while being decoded: {path}")
        image.flags.writeable = False
        if self.max_bytes and image.nbytes <= self.max_bytes:
            while self.entries and self.bytes + image.nbytes > self.max_bytes:
                _, (_, removed) = self.entries.popitem(last=False)
                self.bytes -= removed.nbytes
                self.evictions += 1
            self.entries[path] = (identity, image)
            self.bytes += image.nbytes
            self.peak_bytes = max(self.peak_bytes, self.bytes)
        return image

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    def statistics(self):
        return {
            "budget_bytes": self.max_bytes,
            "retained_bytes": self.bytes,
            "peak_retained_bytes": self.peak_bytes,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }
