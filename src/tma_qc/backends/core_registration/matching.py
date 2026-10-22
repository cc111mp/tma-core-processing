# Adapted from the local tma-core-registration research workflow (tmareg 0.1.0).
"""Interchangeable correspondence backends. RoMa never silently falls back to SIFT."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
from PIL import Image


class BackendUnavailable(RuntimeError):
    pass


def cached_weights():
    """Identify existing RoMa weights without downloading or executing a model."""
    try:
        import torch
        from romatch.models.model_zoo import weight_urls
    except (ImportError, RuntimeError) as error:
        raise BackendUnavailable(
            "RoMa requires the [registration-roma] extra and pre-cached model weights"
        ) from error
    records = {}

    def identity(stat):
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]

    for key, url in (
        ("roma", weight_urls["romatch"]["outdoor"]),
        ("dinov2", weight_urls["dinov2"]),
    ):
        path = Path(torch.hub.get_dir()) / "checkpoints" / Path(urlparse(url).path).name
        if not path.is_file():
            raise BackendUnavailable(
                f"Missing cached {key} weights: {path}. Prepare model weights explicitly; "
                "TMA QC never downloads registration weights. TORCH_HOME selects the cache."
            )
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            if identity(os.fstat(stream.fileno())) != identity(before):
                raise BackendUnavailable(f"Model weights changed before hashing: {path}")
            for block in iter(lambda: stream.read(8 * 1024**2), b""):
                digest.update(block)
            if identity(os.fstat(stream.fileno())) != identity(before):
                raise BackendUnavailable(f"Model weights changed while hashing: {path}")
        after = path.stat()
        if identity(before) != identity(after):
            raise BackendUnavailable(f"Model weights changed while hashing: {path}")
        records[key] = {
            "url": url,
            "path": str(path),
            "sha256": digest.hexdigest(),
            "identity": identity(after),
        }
    return records


@dataclass
class Matches:
    moving: np.ndarray
    fixed: np.ndarray
    confidence: np.ndarray

    def subset(self, keep):
        return Matches(self.moving[keep], self.fixed[keep], self.confidence[keep])


def sparse_matches(moving, fixed, backend, settings):
    detector = (
        cv2.SIFT_create(nfeatures=6000) if backend == "sift" else cv2.ORB_create(nfeatures=6000)
    )
    km, dm = detector.detectAndCompute(moving, None)
    kf, df = detector.detectAndCompute(fixed, None)
    if dm is None or df is None or min(len(dm), len(df)) < 2:
        return Matches(np.empty((0, 2)), np.empty((0, 2)), np.empty(0))
    matcher = cv2.BFMatcher(cv2.NORM_L2 if backend == "sift" else cv2.NORM_HAMMING)
    forward = matcher.knnMatch(dm, df, k=2)
    reverse = matcher.knnMatch(df, dm, k=2)
    reverse_ok = {
        p.queryIdx: p.trainIdx for p, q in reverse if p.distance < settings.ratio * q.distance
    }
    good = [
        (p, q)
        for p, q in forward
        if p.distance < settings.ratio * q.distance and reverse_ok.get(p.trainIdx) == p.queryIdx
    ]
    return Matches(
        np.float64([km[p.queryIdx].pt for p, _ in good]).reshape(-1, 2),
        np.float64([kf[p.trainIdx].pt for p, _ in good]).reshape(-1, 2),
        np.float64([1 - p.distance / max(q.distance, 1e-8) for p, q in good]),
    )


class RomaMatcher:
    """One pretrained model per experiment, with original-image pixel-centre coordinates."""

    def __init__(self, settings, seed):
        self.settings, self.seed = settings, seed
        self.model = None
        self.metadata = None

    def load(self):
        if self.model is not None:
            return
        try:
            import torch
            from romatch import roma_outdoor
        except (ImportError, RuntimeError) as error:
            raise BackendUnavailable(
                "RoMa is unavailable. Install the [roma] extra in a separate environment. "
                f"Import error: {error}"
            ) from error
        device = self.settings.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise BackendUnavailable("RoMa requested CUDA, but CUDA is unavailable")
        torch.manual_seed(self.seed)
        try:
            weights = cached_weights()
            from collections.abc import Mapping

            states = {
                key: torch.load(record["path"], map_location="cpu", weights_only=True)
                for key, record in weights.items()
            }
            if any(
                not isinstance(state, Mapping)
                or not state
                or any(
                    not isinstance(key, str) or not isinstance(value, torch.Tensor)
                    for key, value in state.items()
                )
                for state in states.values()
            ):
                raise BackendUnavailable(
                    "Cached RoMa/DINOv2 checkpoints must be nonempty tensor state dictionaries; no model was initialized"
                )
            self.model = roma_outdoor(
                device=device,
                weights=states["roma"],
                dinov2_weights=states["dinov2"],
                coarse_res=self.settings.coarse_resolution,
                upsample_res=self.settings.upsample_resolution,
                use_custom_corr=False,
            )
            self.model.eval()
        except Exception as error:
            raise BackendUnavailable(f"Could not initialize pretrained RoMa: {error}") from error
        self.device = device
        self.metadata = {
            "model": "roma_outdoor",
            "backbone": "DINOv2",
            "device": device,
            "custom_cuda_kernel": False,
            "certainty_is_calibrated_for_AF_HE": False,
        }
        self.metadata["weights"] = weights

    def match(self, moving, fixed, number):
        self.load()
        import torch

        # The two images have already received the SAME requested structural representation.
        im = Image.fromarray(moving).convert("RGB")
        fi = Image.fromarray(fixed).convert("RGB")
        torch.manual_seed(self.seed)
        with torch.inference_mode():
            dense, certainty = self.model.match(im, fi, device=self.device)
            matches, confidence = self.model.sample(dense, certainty, num=number)
        pairs = matches.detach().float().cpu().numpy()
        # RoMa/grid_sample normalized coordinates use align_corners=False. The upstream
        # to_pixel_coordinates helper maps to image edges; subtract .5 for pixel centres.
        moving_xy = (pairs[:, :2] + 1) * np.array([im.width, im.height]) / 2 - 0.5
        fixed_xy = (pairs[:, 2:] + 1) * np.array([fi.width, fi.height]) / 2 - 0.5
        return Matches(moving_xy, fixed_xy, confidence.detach().float().cpu().numpy())


def foreground_matches(matches, moving_mask, fixed_mask, number):
    keep = np.isfinite(matches.moving).all(axis=1) & np.isfinite(matches.fixed).all(axis=1)
    keep &= np.isfinite(matches.confidence)
    for points, mask in ((matches.moving, moving_mask), (matches.fixed, fixed_mask)):
        inside = (
            (points >= 0).all(axis=1)
            & (points[:, 0] <= mask.shape[1] - 1)
            & (points[:, 1] <= mask.shape[0] - 1)
        )
        nearest = np.rint(np.nan_to_num(points, nan=0, posinf=0, neginf=0)).astype(np.int64)
        nearest[:, 0] = np.clip(nearest[:, 0], 0, mask.shape[1] - 1)
        nearest[:, 1] = np.clip(nearest[:, 1], 0, mask.shape[0] - 1)
        keep &= inside & mask[nearest[:, 1], nearest[:, 0]].astype(bool)
    matches = matches.subset(keep)
    # Retain distributed support, rather than allowing one high-contrast gland to dominate.
    order = np.argsort(-matches.confidence, kind="stable")
    counts, selected = {}, []
    per_cell = max(8, int(np.ceil(number / 64)))
    height, width = fixed_mask.shape
    for index in order:
        x, y = matches.fixed[index]
        cell = min(7, int(y * 8 / height)), min(7, int(x * 8 / width))
        if counts.get(cell, 0) < per_cell:
            selected.append(index)
            counts[cell] = counts.get(cell, 0) + 1
        if len(selected) >= number:
            break
    return matches.subset(np.asarray(selected, dtype=int))
