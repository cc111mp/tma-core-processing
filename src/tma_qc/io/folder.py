"""Build an AF manifest directly from the observed NAS acquisition layouts."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from ..provenance import read_json, utc, write_new_json
from ..schema import Group, safe_id
from .manifest import Manifest, _manifest_from_entries

IGNORED_NAMES = {"Thumbs.db", "desktop.ini", ".DS_Store", "@eaDir", "#recycle", ".snapshot"}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _inventory(root):
    """List originals without following links; retain empty core directories."""
    entries, directories = [], {}
    pending = [root]
    while pending:
        directory = pending.pop()
        directories[directory] = _identity(directory.stat())
        with os.scandir(directory) as stream:
            children = sorted(stream, key=lambda entry: entry.name)
        descend = []
        for child in children:
            if child.name in IGNORED_NAMES:
                continue
            path = Path(child.path)
            info = child.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"Symbolic links are not supported in raw inputs: {path}")
            if stat.S_ISDIR(info.st_mode):
                kind = "dir"
                descend.append(path)
            elif stat.S_ISREG(info.st_mode):
                kind = "file"
            else:
                raise ValueError(f"Unsupported raw filesystem entry: {path}")
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "kind": kind,
                    "identity": _identity(info),
                }
            )
        pending.extend(reversed(descend))
    return entries, directories


def _source_hash(root, entry):
    """Pin the inventoried file identity before and after reading its bytes."""
    path = root / entry["path"]
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _identity(before) != entry["identity"]:
            raise ValueError(f"Raw source changed after discovery: {path}")
        while block := os.read(descriptor, 8 * 1024 * 1024):
            digest.update(block)
        if (
            _identity(os.fstat(descriptor)) != entry["identity"]
            or _identity(path.stat(follow_symlinks=False)) != entry["identity"]
        ):
            raise ValueError(f"Raw source changed during hashing: {path}")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def import_raw_folder(raw_root, output, models=None, *, dataset_id="TMA-2026"):
    """Write a draft manifest for NAS-style AF data without a transfer database.

    Folder names identify candidate acquisitions, cores and lamp groups. They
    cannot establish physical completeness, model compatibility, native pixel
    geometry or AF/H&E correspondence. Those remain explicit review inputs.
    """
    root, output = Path(raw_root).expanduser().resolve(), Path(output).expanduser().absolute()
    safe_id(dataset_id)
    if not root.is_dir():
        raise ValueError(f"Raw input root is not a directory: {root}")
    if output.resolve().is_relative_to(root):
        raise ValueError("Manifest output must be outside the raw archive")
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    model_map = read_json(models) if models else {}
    if not isinstance(model_map, dict):
        raise ValueError("Model assignments must map group IDs to model assets")
    for group_id, assets in model_map.items():
        if not isinstance(assets, dict) or set(assets) - {
            "flatfield",
            "darkfield",
            "model_metadata",
        }:
            raise ValueError("Model assignments support flatfield, darkfield and model_metadata")
        Group(id=group_id, **assets)
    entries, directories = _inventory(root)
    hashed_files = 0
    hashed_bytes = 0

    def hash_source(entry):
        nonlocal hashed_files, hashed_bytes
        digest = _source_hash(root, entry)
        hashed_files += 1
        hashed_bytes += entry["identity"][2]
        return digest

    data = _manifest_from_entries(entries, root, model_map, hash_source=hash_source)
    for directory, expected in directories.items():
        if _identity(directory.stat()) != expected:
            raise ValueError(
                f"Raw folder changed during import; scan again when stable: {directory}"
            )
    data["dataset_id"] = dataset_id
    groups = {group["id"]: group for group in data["groups"]}
    for core in data["cores"]:
        group = groups[core["group"]]
        core["metadata"].update(
            source_slide_label=Path(group["metadata"]["source_parent"]).name,
            source_lamp_label=group["metadata"]["lamp"],
            source_FOV_set_verified=False,
        )
    data["metadata"].update(
        import_method="nas_raw_folder",
        folder_import={
            "completed_utc": utc(),
            "hashed_files": hashed_files,
            "hashed_bytes": hashed_bytes,
            "ignored_names": sorted(IGNORED_NAMES),
            "scope": "Observed AF file inventory; physical acquisition completeness requires review",
        },
    )
    Manifest(output, data=data)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    write_new_json(output, data)
    return data
