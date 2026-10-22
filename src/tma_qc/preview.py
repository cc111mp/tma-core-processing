"""Small previews of large TIFFs, read without loading the whole file.

The TMA TIFFs have no built-in low-resolution copy. For a preview of about
`size` pixels we read only every k-th row: one strip per row for the LZW files,
one row slice for uncompressed files. Previews are cached as .npy files so each
image is read from the NAS only once.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import tifffile


def read_preview(path: str | Path, size: int) -> np.ndarray:
    """Return the image shrunk so its longer side is about `size` pixels.

    Supported: files with a pyramid (e.g. .svs; the smallest big-enough level is read),
    and single-level files stored in strips with interleaved colour (the TMA TIFFs).
    Anything else raises ValueError rather than returning a wrong picture.
    """
    with tifffile.TiffFile(path) as tif:
        levels = tif.series[0].levels
        if len(levels) > 1:
            big_enough = [lv for lv in levels if max(lv.shape[:2]) >= size] or levels[:1]
            level = min(big_enough, key=lambda lv: max(lv.shape[:2]))
            image = level.asarray()
            k = max(1, max(image.shape[:2]) // size)
            return image[::k, ::k]

        page = tif.pages[0]
        if page.is_tiled or page.planarconfig == tifffile.PLANARCONFIG.SEPARATE:
            raise ValueError(f"unsupported TIFF layout (tiled or planar, no pyramid): {path}")
        height, width = page.shape[:2]
        k = max(1, max(height, width) // size)
        fh = tif.filehandle
        rows = []
        if page.compression == 1 and len(page.dataoffsets) == 1:
            # Uncompressed, one block: jump straight to each wanted row.
            row_bytes = int(np.prod(page.shape[1:])) * page.dtype.itemsize
            for r in range(0, height, k):
                fh.seek(page.dataoffsets[0] + r * row_bytes)
                row = np.frombuffer(fh.read(row_bytes), dtype=page.dtype)
                rows.append(row.reshape(page.shape[1:])[::k])
        else:
            # Strips: decode only the strips holding the wanted rows, each once.
            per_strip = page.rowsperstrip
            last_index, segment = -1, None
            for r in range(0, height, k):
                i = r // per_strip
                if i != last_index:
                    fh.seek(page.dataoffsets[i])
                    segment, _, _ = page.decode(fh.read(page.databytecounts[i]), i)
                    segment = segment.reshape(-1, *page.shape[1:])
                    last_index = i
                rows.append(segment[r - i * per_strip, ::k])
        return np.stack(rows)


def cached_preview(path: str | Path, size: int, cache_dir: str | Path) -> np.ndarray:
    """read_preview, remembered on local disk (keyed by path, size and file time)."""
    st = os.stat(path)
    key = f"{Path(path).resolve()}|{size}|{st.st_size}|{st.st_mtime_ns}"
    cache = Path(cache_dir) / (hashlib.sha1(key.encode()).hexdigest() + ".npy")
    if cache.exists():
        return np.load(cache)
    image = read_preview(path, size)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp.npy")
    np.save(tmp, image)
    tmp.replace(cache)
    return image
