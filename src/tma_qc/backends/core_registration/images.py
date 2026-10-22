# Adapted from the local tma-core-registration research workflow (tmareg 0.1.0).
"""Read-only core inputs, bounded working views, and provisional foreground support."""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import tifffile
from PIL import Image
from scipy.sparse import csr_matrix


@contextmanager
def open_image(path: Path, scratch: Path):
    """Memory-map TIFFs; decode compressed single-plane TIFFs to temporary local storage."""
    path = Path(path)
    mapped = None
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="decode-", dir=scratch) as directory:
        try:
            if path.suffix.lower() in {".tif", ".tiff"}:
                with tifffile.TiffFile(path) as tif:
                    series = tif.series[0]
                    if series.axes not in {"YX", "YXS", "YXC"} or len(series.pages) != 1:
                        raise ValueError(
                            "Select an explicit grayscale/RGB core plane before running"
                        )
                    try:
                        mapped = tifffile.memmap(path, mode="r")
                    except (ValueError, OSError):
                        page = series.pages[0]
                        rows = page.tilelength if page.is_tiled else page.rowsperstrip
                        width = page.tilewidth if page.is_tiled else page.imagewidth
                        segment = rows * width * page.samplesperpixel * series.dtype.itemsize
                        if segment > 128 * 1024**2:
                            raise ValueError("TIFF strips exceed 128 MiB; retile this core first")
                        size = int(np.prod(series.shape)) * series.dtype.itemsize
                        if shutil.disk_usage(scratch).free < size + 128 * 1024**2:
                            raise ValueError("Insufficient local scratch space for TIFF decoding")
                        mapped = series.asarray(
                            out=str(Path(directory) / "pixels.bin"), maxworkers=2
                        )
                image = mapped
            else:
                with Image.open(path) as source:
                    image = np.array(source)
            if image.ndim not in {2, 3} or (image.ndim == 3 and image.shape[2] != 3):
                raise ValueError(f"Expected one grayscale or RGB core, got {image.shape}")
            if min(image.shape[:2]) < 16:
                raise ValueError("Core images must be at least 16 pixels per side")
            if image.dtype.kind not in "buf" or image.dtype.itemsize > 8:
                raise ValueError(f"Unsupported pixel encoding: {image.dtype}")
            if image.ndim == 3 and image.dtype not in {np.dtype("uint8"), np.dtype("uint16")}:
                raise ValueError("RGB H&E requires explicit uint8 or uint16 intensity encoding")
            if image.dtype.kind == "f":
                for y in range(0, image.shape[0], 512):
                    block = image[y : y + 512]
                    if not np.isfinite(block).all() or np.max(np.abs(block)) > 1e35:
                        raise ValueError("Nonfinite or unrepresentable image values")
            yield image
        finally:
            if mapped is not None and getattr(mapped, "_mmap", None) is not None:
                mapped._mmap.close()


def transform_points(matrix, points):
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    return points @ np.asarray(matrix)[:2, :2].T + np.asarray(matrix)[:2, 2]


def resize_to_native(native_shape, view_shape):
    """OpenCV resize uses pixel centres: native = scale * (view + .5) - .5."""
    sy, sx = np.asarray(native_shape[:2]) / np.asarray(view_shape[:2])
    return np.array([[sx, 0, (sx - 1) / 2], [0, sy, (sy - 1) / 2], [0, 0, 1.0]])


def normalize(image):
    dtype = np.float64 if image.dtype.kind == "f" and image.dtype.itemsize == 8 else np.float32
    image = np.asarray(image, dtype=dtype)
    lo, hi = np.percentile(image, [1, 99.5])
    if hi <= lo:
        return np.zeros_like(image)
    return np.asarray(np.clip((image - lo) / (hi - lo), 0, 1), np.float32)


def structural_gray(image, modality):
    dtype = np.float64 if image.dtype.kind == "f" and image.dtype.itemsize == 8 else np.float32
    gray = np.asarray(image, dtype)
    if modality == "he":
        if image.ndim == 3:
            maximum = np.iinfo(image.dtype).max
            gray = gray.mean(axis=2)
            gray = -np.log(np.clip((gray + 1) / (maximum + 1), 1e-6, 1))
        else:
            # A floating grayscale core has no declared white-reference units:
            # it may retain camera counts, use [0, 1], or include an offset.
            # Inverting the robust normalized grayscale intensity preserves the
            # same brightfield structure across those positive affine encodings.
            gray = -gray
    elif image.ndim != 2:
        raise ValueError("AF requires an explicitly selected grayscale channel")
    return normalize(gray)


def foreground(gray):
    """A permissive foreground estimate, retaining multiple fragments and real holes."""
    smooth = cv2.GaussianBlur(gray, (0, 0), 1)
    _, binary = cv2.threshold(np.uint8(smooth * 255), 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary)
    keep = np.zeros(count, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= max(16, binary.size * 0.0002)
    return keep[labels]


def feature_image(gray, mode):
    if mode == "intensity":
        image = gray
    else:
        smooth = cv2.GaussianBlur(gray, (0, 0), 1)
        dx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0)
        dy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1)
        image = normalize(cv2.magnitude(dx, dy))
    return np.uint8(np.clip(image, 0, 1) * 255)


def _area_weights(source, target, dtype=np.float32):
    """Sparse exact pixel-area weights for one axis, preserving the resize frame."""
    scale = source / target
    data, indices, indptr = [], [], [0]
    for output in range(target):
        start, end = output * scale, (output + 1) * scale
        first, last = int(np.floor(start)), min(source, int(np.ceil(end)))
        for pixel in range(first, last):
            weight = max(0.0, min(end, pixel + 1) - max(start, pixel)) / scale
            if weight:
                data.append(weight)
                indices.append(pixel)
        indptr.append(len(data))
    return csr_matrix((np.asarray(data, dtype), indices, indptr), shape=(target, source))


def bounded_area_resize(image, shape):
    """Area-average native pixels in bounded strips, including planar/endian inputs.

    OpenCV can copy an entire noncontiguous or nonnative-endian input before a
    resize. Streaming separable pixel-area weights avoids that native-sized RAM
    copy while retaining the exact pixel-centre scaling used by the transform.
    """
    height, width = image.shape[:2]
    out_h, out_w = shape
    dtype = np.float64 if image.dtype.kind == "f" and image.dtype.itemsize == 8 else np.float32
    wx, wy = _area_weights(width, out_w, dtype), _area_weights(height, out_h, dtype)
    channels = 1 if image.ndim == 2 else image.shape[2]
    output = np.zeros((out_h, out_w * channels), dtype)
    rows = max(1, min(64, (32 * 1024**2) // max(1, width * channels * np.dtype(dtype).itemsize)))
    for y in range(0, height, rows):
        source = np.asarray(image[y : y + rows], dtype=dtype, order="C")
        planes = source.reshape(-1, width, channels).transpose(1, 0, 2).reshape(width, -1)
        horizontal = (wx @ planes).reshape(out_w, source.shape[0], channels).transpose(1, 0, 2)
        first = max(0, int(np.floor(y * out_h / height)))
        last = min(out_h, int(np.ceil((y + source.shape[0]) * out_h / height)))
        output[first:last] += wy[first:last, y : y + source.shape[0]] @ horizontal.reshape(
            source.shape[0], -1
        )
    output = output.reshape(out_h, out_w, *image.shape[2:])
    if image.dtype.kind == "u":
        return np.rint(np.clip(output, 0, np.iinfo(image.dtype).max)).astype(
            image.dtype.newbyteorder("=")
        )
    return output


@dataclass
class View:
    gray: np.ndarray
    rgb: np.ndarray
    mask: np.ndarray
    to_native: np.ndarray
    native_shape: tuple
    mpp: float | None
    mask_source: str


def prepare_view(image, modality, max_dimension, mask_path, mpp, scratch):
    height, width = image.shape[:2]
    ratio = min(1.0, max_dimension / max(height, width))
    size = (max(16, round(width * ratio)), max(16, round(height * ratio)))
    small = bounded_area_resize(image, (size[1], size[0]))
    gray = structural_gray(small, modality)
    if modality == "he" and small.ndim == 3:
        maximum = np.iinfo(small.dtype).max
        rgb = np.uint8(np.clip(small.astype(np.float32) / maximum * 255, 0, 255))
    else:
        rgb = np.repeat(np.uint8(gray[..., None] * 255), 3, axis=2)
    if mask_path:
        with open_image(mask_path, scratch) as supplied:
            if supplied.ndim != 2 or supplied.shape != image.shape[:2]:
                raise ValueError("Supplied foreground mask must match the original image YX shape")
            # Nearest pixel-centre sampling avoids converting a native mask into a huge array.
            xs = np.minimum(((np.arange(size[0]) + 0.5) * width / size[0]).astype(int), width - 1)
            ys = np.minimum(((np.arange(size[1]) + 0.5) * height / size[1]).astype(int), height - 1)
            mask = supplied[np.ix_(ys, xs)] != 0
        mask_source = "provided_foreground_not_verified_correspondence"
    else:
        mask, mask_source = foreground(gray), "automatic_provisional_foreground"
    if mask.sum() < 64:
        raise ValueError(
            f"Insufficient {modality.upper()} foreground; provide a reviewed core mask"
        )
    return View(
        gray,
        rgb,
        mask,
        resize_to_native(image.shape, gray.shape),
        tuple(image.shape),
        mpp,
        mask_source,
    )


def warp(image, matrix, shape, *, nearest=False, fill=0):
    return cv2.warpAffine(
        np.asarray(image, dtype=np.uint8) if image.dtype == bool else image,
        matrix[:2],
        (shape[1], shape[0]),
        flags=cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=fill,
    )


def save_png(path, image):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.asarray(image)
    if image.dtype == bool:
        image = image.astype(np.uint8) * 255
    elif image.dtype != np.uint8:
        image = np.uint8(np.clip(image, 0, 1) * 255)
    Image.fromarray(image).save(path)
