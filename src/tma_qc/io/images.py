from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np
import tifffile
from PIL import Image

from .tiff_validation import validate_tiff_plane


def native_array(image, *, contiguous=False):
    """Preserve numeric values while normalizing byte order for image libraries."""
    return np.asarray(image, dtype=image.dtype.newbyteorder("="), order="C" if contiguous else None)


def is_uint16(dtype):
    return np.dtype(dtype).kind == "u" and np.dtype(dtype).itemsize == 2


def _plane(image, axes, rgb):
    if rgb and axes in {"SYX", "CYX"}:
        image = np.moveaxis(image, 0, -1)
    if image.ndim == 2:
        return image
    if rgb and image.ndim == 3 and image.shape[-1] in {3, 4}:
        return image[..., :3]
    raise ValueError(f"Unsupported image shape {image.shape}")


def read_image(path: str | Path, *, rgb: bool = False) -> np.ndarray:
    with tifffile.TiffFile(path, _multifile=False) as tif:
        axes = validate_tiff_plane(tif, label=f"Image {path}", allow_pyramid=True).axes
        if axes not in ({"YX", "YXS", "YXC", "SYX", "CYX"} if rgb else {"YX"}):
            raise ValueError(f"Explicit plane selection required for axes {axes}: {path}")
    try:
        image = tifffile.memmap(path, mode="r", _multifile=False)
    except (ValueError, OSError):
        with tifffile.TiffFile(path, _multifile=False) as tif:
            series = tif.series[0]
            image = series.asarray()
    return native_array(_plane(image, axes, rgb))


@contextmanager
def mapped_image(path, scratch, *, rgb=False, max_segment_bytes=64 * 1024 * 1024):
    """Open a native image with file-backed storage, including compressed TIFFs.

    Compressed sources decode to temporary local SSD storage, with two decoder
    workers and bounded TIFF segments, instead of an image-sized RAM array.
    Callers must copy any previews/patches needed after this context closes.
    """
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    mapped = None
    with (
        tifffile.TiffFile(path, _multifile=False) as tif,
        tempfile.TemporaryDirectory(prefix="he-decode-", dir=scratch) as temp,
    ):
        series = validate_tiff_plane(tif, label=f"Image {path}", allow_pyramid=True)
        axes = series.axes
        if (
            axes not in ({"YX", "YXS", "YXC", "SYX", "CYX"} if rgb else {"YX"})
            or len(series.pages) != 1
        ):
            raise ValueError(f"Explicit single-plane image selection required: {axes}: {path}")
        try:
            try:
                mapped = tifffile.memmap(path, mode="r", _multifile=False)
            except (ValueError, OSError):
                page = series.pages[0]
                channels = (
                    1
                    if page.planarconfig == tifffile.PLANARCONFIG.SEPARATE
                    else page.samplesperpixel
                )
                height = (
                    page.tilelength if page.is_tiled else min(page.rowsperstrip, page.imagelength)
                )
                width = page.tilewidth if page.is_tiled else page.imagewidth
                segment_bytes = height * width * channels * np.dtype(series.dtype).itemsize
                if segment_bytes > max_segment_bytes:
                    raise ValueError(
                        f"TIFF decoding segment exceeds the {max_segment_bytes}-byte working limit; retile or re-strip the H&E source"
                    )
                required = int(np.prod(series.shape)) * np.dtype(series.dtype).itemsize
                if shutil.disk_usage(scratch).free < required + max_segment_bytes:
                    raise ValueError(
                        f"Insufficient local scratch for native H&E decoding: need {required} bytes plus decoder space"
                    )
                mapped = series.asarray(
                    out=str(Path(temp) / "native.bin"), maxworkers=2, buffersize=8 * 1024 * 1024
                )
                mapped.flush()
            yield _plane(mapped, axes, rgb)
        finally:
            if mapped is not None and getattr(mapped, "_mmap", None) is not None:
                mapped._mmap.close()


def write_tiff(path: str | Path, image: np.ndarray, **kwargs) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + uuid.uuid4().hex + ".tif")
    try:
        tifffile.imwrite(tmp, image, bigtiff=True, **kwargs)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def reduced(image: np.ndarray, size: int) -> np.ndarray:
    step = max(1, int(np.ceil(max(image.shape[:2]) / size)))
    return np.asarray(image[::step, ::step])


def covered_sample(image, coverage, size=1024):
    """Sample finite acquired counts; a missed grid is not empty native coverage.

    The fallback scans native 512-pixel patches and keeps at most size² values.
    It supplies advisory reference statistics, never a dense support mask.
    """
    sample = reduced(image, size)
    selected = sample[reduced(coverage, size).astype(bool)]
    selected = selected[np.isfinite(selected)]
    if selected.size:
        return selected, "strided_grid"
    values, retained, limit = [], 0, size * size
    for y in range(0, image.shape[0], 512):
        for x in range(0, image.shape[1], 512):
            region = np.s_[y : y + 512, x : x + 512]
            block = np.asarray(image[region])
            selected = block[np.asarray(coverage[region], bool)].ravel()
            counts = selected[np.isfinite(selected)][: limit - retained]
            if counts.size:
                values.append(counts)
                retained += counts.size
            if retained == limit:
                return np.concatenate(values), "native_coverage_fallback"
    return (
        np.concatenate(values) if values else np.empty(0, dtype=sample.dtype),
        "native_coverage_fallback",
    )


def window(image: np.ndarray, valid: np.ndarray | None = None) -> tuple[float, float]:
    if valid is not None:
        sample, _ = covered_sample(image, valid, 512)
    else:
        sample = reduced(image, 512)
        sample = sample[np.isfinite(sample)]
    if not sample.size:
        return 0.0, 1.0
    lo, hi = np.percentile(sample, [1, 99.5])
    if hi <= lo:
        hi = max(lo + 1, np.nextafter(float(lo), np.inf))
    return float(lo), float(hi)


def display(image: np.ndarray, limits: tuple[float, float]) -> np.ndarray:
    dtype = np.float64 if image.dtype.kind == "f" and image.dtype.itemsize == 8 else np.float32
    span = limits[1] - limits[0]
    return np.uint8(
        np.clip((np.asarray(image, dtype) - limits[0]) / (span if span > 0 else 1.0), 0, 1) * 255
    )


def save_preview(path: str | Path, image: np.ndarray, limits=None, size=1024) -> Path:
    path = Path(path)
    small = reduced(image, size)
    if small.ndim == 2:
        small = display(small, limits or window(small))
    elif small.dtype != np.uint8:
        if not np.issubdtype(small.dtype, np.unsignedinteger):
            raise ValueError("RGB previews require explicit uint8 or uint16 intensity encoding")
        maximum = np.iinfo(small.dtype).max
        small = np.rint(np.asarray(small, np.float64) * (255.0 / maximum)).astype(np.uint8)
    Image.fromarray(small.astype(np.uint8)).save(path)
    return path


def structural_gray(image: np.ndarray) -> np.ndarray:
    dtype = np.float64 if image.dtype.kind == "f" and image.dtype.itemsize == 8 else np.float32
    a = np.asarray(image, dtype)
    if a.ndim == 3:
        # Structural proxy for matching/display, never a corrected scientific image.
        maximum = 255.0 if a.max() <= 255 else 65535.0
        return -np.log(np.clip((a[..., :3].mean(axis=2) + 1) / (maximum + 1), 1e-6, 1))
    return a


def match_image(image: np.ndarray) -> np.ndarray:
    gray = structural_gray(image)
    # Centre and scale before the float32 OpenCV operations. This preserves
    # meaningful float64 contrast on a large offset, as well as tiny float ranges.
    lo, hi = window(gray)
    gray = np.asarray((gray - lo) / (hi - lo), np.float32)
    gx = cv2.Sobel(cv2.GaussianBlur(gray, (0, 0), 1), cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(cv2.GaussianBlur(gray, (0, 0), 1), cv2.CV_32F, 0, 1)
    magnitude = np.sqrt(gx * gx + gy * gy)
    return display(magnitude, (0.0, max(float(np.percentile(magnitude, 99.5)), 1e-6)))
