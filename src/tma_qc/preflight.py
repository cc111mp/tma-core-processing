"""Read-only operational checks; no image-quality acceptance or processing."""

from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path

import numpy as np
import tifffile

from .backends.cidre import validate_fields
from .backends.mist import validate_layout
from .io.coordinates import load_positions
from .io.images import read_image
from .io.tile_cache import file_identity
from .provenance import read_json, sha256, utc
from .qc.integrity import validate_tiff_plane, verify_core, verify_file
from .qc.registration import evaluate_landmarks, load_registration_transform
from .schema import NeedsInput
from .state import filesystem_type
from .storage import estimate_batch, existing_parent, validate_writable_tree


def check(manifest, config, output=None, selected=None):
    known = {c.uid for c in manifest.cores}
    if selected and not set(selected) <= known:
        raise ValueError("Unknown selected core identifiers")
    cores = [c for c in manifest.cores if not selected or c.uid in selected]
    pairs = [p for p in manifest.pairs if p.af_core in {c.uid for c in cores}]
    issues, warnings = [], []
    checked_sources = 0

    def attempt(scope, label, operation):
        try:
            return operation()
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            tifffile.TiffFileError,
        ) as exc:
            issues.append({"scope": scope, "check": label, "reason": str(exc)})

    cache = Path(config["runtime"]["cache_root"]).expanduser().resolve()
    for label, value in [("output", output), ("cache", cache)]:
        if value is None:
            issues.append(
                {
                    "scope": label,
                    "check": "location",
                    "reason": "Pass --out for an operational storage preflight",
                }
            )
            continue
        target = Path(value).resolve()
        overlapping = target.is_relative_to(manifest.raw_root) or manifest.raw_root.is_relative_to(
            target
        )
        if overlapping:
            issues.append(
                {
                    "scope": label,
                    "check": "location",
                    "reason": "Writable processing locations and the raw archive require disjoint directory trees",
                }
            )
        else:
            attempt(label, "writable tree aliases", lambda: validate_writable_tree(target))

        def writable():
            parent = existing_parent(target)
            if not os.access(parent, os.W_OK | os.X_OK):
                raise ValueError(f"Location is not writable: {parent}")

        attempt(label, "writability", writable)
    if filesystem_type(cache) in {"cifs", "smbfs", "nfs", "nfs4", "fuse.sshfs"}:
        issues.append(
            {
                "scope": "cache",
                "check": "filesystem",
                "reason": "SQLite checkpoints require local storage",
            }
        )

    groups = {c.group for c in cores if not c.blocked_reasons}
    if config["illumination"]["mode"] == "cidre":
        for group in (g for g in manifest.groups if g.id in groups):

            def fields():
                if not group.flatfield or not group.darkfield:
                    raise NeedsInput("Explicit flatfield and darkfield assets are required")
                validate_fields(read_image(group.flatfield), read_image(group.darkfield))
                metadata = read_json(group.model_metadata) if group.model_metadata else {}
                for name in ("flatfield", "darkfield"):
                    actual = sha256(getattr(group, name))
                    if metadata.get(name + "_sha256", actual) != actual:
                        raise ValueError(f"{name} differs from the frozen model receipt")

            attempt(group.id, "illumination fields", fields)
            warnings.append(
                {
                    "scope": group.id,
                    "reason": "Acquisition compatibility and CIDRE signal preservation require scientific review",
                }
            )

    for core in cores:
        if core.blocked_reasons:
            issues.append(
                {
                    "scope": core.uid,
                    "check": "eligibility",
                    "reason": "; ".join(core.blocked_reasons),
                }
            )
            continue
        verified = attempt(
            core.uid,
            "raw hashes and TIFF headers",
            lambda: verify_core(core, manifest, config["profile"], decode=False),
        )
        if verified:
            checked_sources += verified[0]["verified_files"]
        if core.positions:
            attempt(
                core.uid,
                "native geometry",
                lambda: load_positions(core.positions, [f.id for f in core.fovs]),
            )
            if not core.geometry_reviewed:
                warnings.append(
                    {"scope": core.uid, "reason": "Provided geometry has not been marked reviewed"}
                )
        elif config["geometry"]["backend"] == "mist":

            def mist_runtime():
                validate_layout(core, config["geometry"], config["profile"]["shape"])
                if not shutil.which("java"):
                    raise NeedsInput("Java is unavailable on PATH")
                for part in config["geometry"]["mist_classpath"].split(os.pathsep):
                    if not part or not any(Path(path).exists() for path in glob.glob(part)):
                        raise NeedsInput(f"Missing MIST classpath entry: {part}")

            attempt(core.uid, "MIST prerequisites", mist_runtime)
        else:
            issues.append(
                {
                    "scope": core.uid,
                    "check": "geometry",
                    "reason": "Provide native positions or configure reviewed MIST geometry",
                }
            )

    for pair in pairs:

        def he_source():
            identities = {}
            verify_file(pair.he_path, pair.he_sha256, identities=identities)
            with tifffile.TiffFile(pair.he_path, _multifile=False) as tif:
                series = validate_tiff_plane(tif, label="H&E source", allow_pyramid=True)
                dtype = np.dtype(series.dtype)
                unsigned = dtype.kind == "u" and dtype.itemsize in {1, 2}
                gray_float = series.axes == "YX" and dtype.kind == "f" and dtype.itemsize in {4, 8}
                if (
                    series.axes not in {"YX", "YXS", "YXC", "SYX", "CYX"}
                    or len(series.pages) != 1
                    or not (unsigned or gray_float)
                ):
                    raise ValueError("Unsupported H&E plane, axes or numeric encoding")
                if series.axes != "YX":
                    channels = (
                        series.shape[-1] if series.axes in {"YXS", "YXC"} else series.shape[0]
                    )
                    if len(series.shape) != 3 or channels not in {3, 4}:
                        raise ValueError("H&E color images require exactly three or four channels")
            for path, identity in identities.items():
                file_identity(path, identity)

        attempt(pair.uid, "H&E source hash and header", he_source)
        if pair.transform:
            attempt(
                pair.uid,
                "native HE-to-AF transform and residual map",
                lambda: load_registration_transform(pair.transform),
            )
        elif config["registration"]["backend"] == "provided":
            issues.append(
                {
                    "scope": pair.uid,
                    "check": "registration",
                    "reason": "A provided HE-to-AF transform is required",
                }
            )
        elif config["registration"]["backend"] == "core":
            from .backends.core_registration import runtime_identity

            attempt(
                pair.uid,
                "core registration configuration and optional runtimes",
                lambda: runtime_identity(config["registration"]),
            )
        if pair.landmarks:
            attempt(
                pair.uid,
                "evaluation landmark format",
                lambda: evaluate_landmarks(np.eye(3), pair.landmarks),
            )
        else:
            warnings.append(
                {"scope": pair.uid, "reason": "No independent evaluation landmarks supplied"}
            )
    budget = attempt(
        "storage", "AF batch budget", lambda: estimate_batch(manifest, config, output, selected)
    )
    if budget:
        for item in budget["unknown_cores"]:
            issues.append({"scope": item["uid"], "check": "AF capacity", "reason": item["reason"]})
        for volume in budget["volumes"]:
            if not volume["sufficient"]:
                issues.append(
                    {
                        "scope": "storage",
                        "check": "AF capacity",
                        "reason": f"Need {volume['required_bytes']} bytes at {volume['existing_path']}; available {volume['free_bytes']}",
                    }
                )
    if pairs:
        warnings.append(
            {
                "scope": "storage",
                "reason": "AF budget excludes H&E exports and decompression; H&E stages retain their separate native-decode guard",
            }
        )
    return {
        "updated_utc": utc(),
        "dataset": manifest.dataset_id,
        "cores": len(cores),
        "pairs": len(pairs),
        "ready_for_execution": not issues,
        "issues": issues,
        "warnings": warnings,
        "raw_files_hash_and_header_verified": checked_sources,
        "physical_acquisition_completeness": "not_assessed",
        "AF_storage": budget,
        "scope": "Operational preflight checks manifest-listed file hashes, TIFF headers and stored segment bounds. Does not decode raw FOVs, validate floating H&E pixels, establish physical acquisition completeness, fit models, or certify image quality. Inputs and available space may change; runtime stages recheck.",
        "automatic_image_quality_release": False,
    }
