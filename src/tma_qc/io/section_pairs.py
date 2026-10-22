"""Add explicitly reviewed AF–H&E pairs without guessing slide correspondence."""

from __future__ import annotations

import csv
import math
from dataclasses import asdict
from pathlib import Path

from ..provenance import utc, write_new_json
from ..schema import SectionPair
from .folder import _identity, _source_hash
from .manifest import Manifest

REQUIRED_COLUMNS = {"uid", "af_core", "he_path", "relationship"}
OPTIONAL_COLUMNS = {"he_slide_label", "transform", "landmarks", "af_mpp", "he_mpp"}


def _regular_path(path):
    path = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"Pair inputs must not use symbolic links: {path}")
    if not path.is_file():
        raise ValueError(f"Pair input is not a regular file: {path}")
    return path.resolve()


def import_section_pairs(manifest_path, pairs_csv, output):
    """Append reviewed pair rows and return a new, relocatable manifest.

    CSV asset paths resolve beside the CSV, independently of the output folder.
    H&E files are hashed without decoding. The input manifest and source images
    are preserved. Pair IDs must be new and AF core UIDs must already exist.
    """
    table = _regular_path(pairs_csv)
    output = Path(output).expanduser().absolute()
    sources = {}

    def pin(path):
        source = _regular_path(path)
        if source not in sources:
            info = _identity(source.stat())
            digest = _source_hash(source.parent, {"path": source.name, "identity": info})
            sources[source] = info, digest
        return source, sources[source][1]

    source_manifest, _ = pin(manifest_path)
    manifest = Manifest(source_manifest)
    if output.resolve().is_relative_to(manifest.raw_root):
        raise ValueError("Manifest output must be outside the raw archive")
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    _, table_hash = pin(table)
    data = manifest.snapshot()
    known = {core.uid for core in manifest.cores}
    pair_ids = {pair.uid for pair in manifest.pairs}
    added = []
    with table.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)):
            raise ValueError("Pair CSV contains duplicate column names")
        if not REQUIRED_COLUMNS <= set(columns):
            raise ValueError("Pair CSV requires uid,af_core,he_path,relationship")
        unknown = set(columns) - REQUIRED_COLUMNS - OPTIONAL_COLUMNS
        if unknown:
            raise ValueError(f"Unknown pair CSV columns: {sorted(unknown)}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed pair CSV row at line {reader.line_num}")
            row = {key: value.strip() for key, value in row.items()}
            if not any(row.values()):
                continue
            if any(not row[key] for key in REQUIRED_COLUMNS):
                raise ValueError(f"Missing required pair value at line {reader.line_num}")
            if row["af_core"] not in known:
                raise ValueError(f"Unknown AF core UID: {row['af_core']}")
            if row["uid"] in pair_ids:
                raise ValueError(f"Duplicate section-pair UID: {row['uid']}")
            he_path, he_hash = pin(table.parent / row["he_path"])
            assets = {}
            for key in ("transform", "landmarks"):
                if row.get(key):
                    asset, _ = pin(table.parent / row[key])
                    assets[key] = str(asset)
            metadata = {"pairing_source": str(table), "pairing_row": reader.line_num}
            if row.get("he_slide_label"):
                metadata["he_slide_label"] = row["he_slide_label"]
            for key in ("af_mpp", "he_mpp"):
                if row.get(key):
                    value = float(row[key])
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError(f"{key} must be a finite positive calibrated pixel size")
                    metadata[key] = value
            pair = SectionPair(
                uid=row["uid"],
                af_core=row["af_core"],
                he_path=str(he_path),
                he_sha256=he_hash,
                relationship=row["relationship"],
                metadata=metadata,
                **assets,
            )
            pair_ids.add(pair.uid)
            added.append(asdict(pair))
    if not added:
        raise ValueError("Pair CSV contains no section pairs")
    data["pairs"].extend(added)
    data["metadata"] = dict(data["metadata"])
    data["metadata"].setdefault("section_pair_imports", [])
    data["metadata"]["section_pair_imports"] = [
        *data["metadata"]["section_pair_imports"],
        {"source": str(table), "sha256": table_hash, "pairs": len(added), "completed_utc": utc()},
    ]
    Manifest(output, data=data)
    for source, (expected, _) in sources.items():
        if source.is_symlink() or _identity(source.stat()) != expected:
            raise ValueError(f"Pair input changed during import: {source}")
    write_new_json(output, data)
    return data
