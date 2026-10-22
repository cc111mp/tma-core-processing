from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path

from ..provenance import read_json, write_new_json
from ..schema import FOV, Core, Group, SectionPair, safe_id


class Manifest:
    def __init__(self, path: str | Path, *, data=None):
        self.path = Path(path).resolve()
        self.data = read_json(self.path) if data is None else data
        allowed = {
            "schema_version",
            "dataset_id",
            "raw_root",
            "groups",
            "cores",
            "pairs",
            "metadata",
        }
        if set(self.data) - allowed or self.data.get("schema_version") != 1:
            raise ValueError("Unsupported manifest schema or unknown top-level fields")
        self.dataset_id = safe_id(self.data["dataset_id"])
        self.raw_root = self.resolve(self.data.get("raw_root", "."))
        self.groups = [Group(**g) for g in self.data.get("groups", [])]
        self.cores = [
            Core(**{**c, "fovs": [FOV(**v) for v in c["fovs"]]}) for c in self.data.get("cores", [])
        ]
        self.pairs = [SectionPair(**p) for p in self.data.get("pairs", [])]
        # Normalize asset paths so a saved snapshot replays from any directory.
        for group in self.groups:
            for name in ["flatfield", "darkfield", "model_metadata"]:
                value = getattr(group, name)
                if value:
                    setattr(group, name, str(self.resolve(value)))
        for core in self.cores:
            if core.positions:
                core.positions = str(self.resolve(core.positions))
        for pair in self.pairs:
            for name in ["he_path", "transform", "landmarks"]:
                value = getattr(pair, name)
                if value:
                    setattr(pair, name, str(self.resolve(value)))
        for values, label in [(self.groups, "group"), (self.cores, "core"), (self.pairs, "pair")]:
            ids = [getattr(v, "uid", getattr(v, "id", None)) for v in values]
            if len(set(ids)) != len(ids):
                raise ValueError(f"Duplicate {label} identifiers")
        if not self.cores:
            raise ValueError("Manifest has no raw cores")
        groups = {g.id for g in self.groups}
        cores = {c.uid for c in self.cores}
        if cores & {p.uid for p in self.pairs}:
            raise ValueError("Core and section-pair identifiers must be globally distinct")
        for c in self.cores:
            if c.group not in groups:
                raise ValueError(f"Unknown illumination group {c.group}")
            sources = [self.raw_path(fov) for fov in c.fovs]
            if len(set(sources)) != len(sources):
                raise ValueError(f"{c.uid}: one resolved source path assigned to multiple FOV ids")
        for p in self.pairs:
            if p.af_core not in cores:
                raise ValueError(f"Pair {p.uid} refers to unknown AF core")

    def resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        return (p if p.is_absolute() else self.path.parent / p).resolve()

    def raw_path(self, fov: FOV) -> Path:
        p = Path(fov.path)
        return (p if p.is_absolute() else self.raw_root / p).resolve()

    def group(self, ident: str) -> Group:
        return next(g for g in self.groups if g.id == ident)

    def snapshot(self):
        return {
            "schema_version": 1,
            "dataset_id": self.dataset_id,
            "raw_root": str(self.raw_root),
            "groups": [asdict(g) for g in self.groups],
            "cores": [asdict(c) for c in self.cores],
            "pairs": [asdict(p) for p in self.pairs],
            "metadata": self.data.get("metadata", {}),
        }


def _ident(label: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("._-")[:100] or "acquisition"
    return clean + "_" + hashlib.sha256(label.encode()).hexdigest()[:10]


def _inventory_location(path, entry_kind):
    """Return (slide path, lamp, core, tail) for known acquisition folders.

    Directory entries also keep empty core folders visible. A lamp-less core
    directory is a placeholder until its actual lamp acquisitions are discovered.
    """
    parts = path.parts
    lamps = [i for i, p in enumerate(parts) if p.lower().startswith("middle lamp")]
    if len(lamps) == 1:
        i = lamps[0]
        if i >= 2 and re.fullmatch(r"[A-Za-z]+\d+", parts[i - 1]):
            return Path(*parts[: i - 1]).as_posix(), parts[i], parts[i - 1], parts[i + 1 :]
    for i in range(1, len(parts)):
        if not re.fullmatch(r"[A-Za-z]{1,3}\d{1,3}", parts[i]):
            continue
        tail = parts[i + 1 :]
        if (not tail and entry_kind in {"dir", "directory", "folder"}) or (
            tail and tail[0].lower() in {"scan", "stitch", "stitch_folder", "stitch_folder_bg"}
        ):
            return Path(*parts[:i]).as_posix(), "", parts[i], tail
    return None


def _ordered_fovs(kind, files):
    """Validate a candidate set without selecting an ambiguous image plane."""
    if kind == "scan":
        positions = [str(Path(r["path"]).parent) for r in files]
        if len(set(positions)) != len(positions):
            return [], "Multiple images per XY position; explicit Z/exposure selection required"
        return sorted(files, key=lambda r: r["path"]), None
    indices = [re.match(r"(?:FOV_)?(\d+)", Path(r["path"]).stem, re.IGNORECASE) for r in files]
    if any(m is None for m in indices):
        return [], "Unrecognized numbered FOV filename"
    ids = [int(m.group(1)) for m in indices]
    if len(set(ids)) != len(ids):
        return [], "Duplicate FOV indices"
    if sorted(ids) not in [list(range(len(ids))), list(range(1, len(ids) + 1))]:
        return [], "Missing FOV indices; a contiguous sequence starting at 0 or 1 is required"
    return [r for _, r in sorted(zip(ids, files), key=lambda pair: pair[0])], None


def import_copy_inventory(
    database: str | Path, raw_root: str | Path, output: str | Path, models: str | Path | None = None
) -> dict:
    """Read the completed copy database; never write to the transfer controller.

    This adapter recognizes the two observed AF acquisition layouts. It does
    not invent native tile geometry or infer physical coordinate units.
    """
    output = Path(output).expanduser().absolute()
    root = Path(raw_root).expanduser().resolve()
    if output.resolve().is_relative_to(root):
        raise ValueError("Manifest output must be outside the raw archive")
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    db = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        done = db.execute("SELECT value FROM meta WHERE key='inventory_complete'").fetchone()
        if not done or json.loads(done["value"]) is not True:
            raise ValueError(
                "Source inventory is incomplete; freeze it before post-transfer indexing"
            )
        bad = db.execute(
            "SELECT path FROM entries WHERE "
            "(kind='file' AND (COALESCE(copied,0)=0 OR COALESCE(copy_error,'')<>'')) OR "
            "COALESCE(inventory_error,'')<>'' LIMIT 1"
        ).fetchone()
        if bad:
            raise ValueError("Transfer is incomplete or contains unresolved errors")
        data = _manifest_from_entries(
            db.execute("SELECT path,kind,source_sha256,destination_verified FROM entries"),
            raw_root,
            read_json(models) if models else {},
        )
    finally:
        db.close()
    data["metadata"]["inventory_database"] = str(Path(database).resolve())
    Manifest(output, data=data)
    write_new_json(output, data)
    return data


def _manifest_from_entries(entries, raw_root, model_map, *, hash_source=None):
    """Share NAS layout recognition and FOV selection across inventory adapters.

    Folder imports hash only the selected original FOVs. Transfer imports reuse
    the source hashes already recorded by the copy controller.
    """
    folders, discovered = {}, {}
    inventory_files = unmapped_files = 0
    for entry in entries:
        r = dict(entry)
        is_file = r["kind"] == "file"
        inventory_files += is_file
        path = Path(r["path"])
        location = _inventory_location(path, r["kind"])
        if location is None:
            unmapped_files += is_file
            continue
        parent, lamp, core, tail = location
        key = parent, lamp, core
        item = discovered.setdefault(key, {"files": 0, "source_sets": []})
        item["files"] += is_file
        if not is_file or path.suffix.lower() not in {".tif", ".tiff"} or not tail:
            continue
        kind = tail[0].lower()
        if kind not in {"stitch", "stitch_folder", "scan"}:
            continue
        if len(tail) != (3 if kind == "scan" else 2):
            continue
        folders.setdefault((*key, kind), []).append(r)
    # Do not count the parent core directory again for each real lamp folder.
    lamp_cores = {(parent, core) for parent, lamp, core in discovered if lamp}
    for key in list(discovered):
        parent, lamp, core = key
        if not lamp and (parent, core) in lamp_cores and not discovered[key]["files"]:
            del discovered[key]
    selected = {}
    for (parent, lamp, core, kind), files in sorted(folders.items()):
        key = (parent, lamp, core)
        ordered, reason = _ordered_fovs(kind, files)
        discovered[key]["source_sets"].append(
            {"kind": kind, "files": len(files), "eligible": reason is None, "reason": reason}
        )
        if reason:
            continue
        rank = {"stitch": 0, "stitch_folder": 1, "scan": 2}[kind]
        if key not in selected or rank < selected[key][0]:
            selected[key] = (rank, kind, ordered)
    cores, groups = [], {}
    for (parent, lamp, core), inventory in sorted(discovered.items()):
        choice = selected.get((parent, lamp, core))
        _, kind, files = choice if choice else (None, None, [])
        reasons = (
            []
            if choice
            else [f"{s['kind']}: {s['reason']}" for s in inventory["source_sets"] if s["reason"]]
        )
        if not choice and not reasons:
            reasons = [
                "No supported uncorrected AF FOV set; review source layout and missing/empty data"
            ]
        signature = parent + "/" + lamp
        group_id = (
            re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(parent).name + "_" + lamp).strip("._-")[:140]
            or "group"
        )
        group_id += "_" + hashlib.sha256(signature.encode()).hexdigest()[:10]
        groups[group_id] = {
            "id": group_id,
            "metadata": {
                "source_parent": parent,
                "lamp": lamp,
                "compatibility": "Camera/exposure compatibility requires review",
            },
            **model_map.get(group_id, {}),
        }
        fovs = []
        for n, r in enumerate(files, 1):
            ident = (
                n
                if kind == "scan"
                else int(re.match(r"(?:FOV_)?(\d+)", Path(r["path"]).stem, re.IGNORECASE).group(1))
            )
            fovs.append(
                asdict(
                    FOV(
                        str(ident),
                        r["path"],
                        hash_source(r) if hash_source else r["source_sha256"],
                        {
                            "source_role": kind,
                            **(
                                {"destination_verified_at_import": bool(r["destination_verified"])}
                                if "destination_verified" in r
                                else {}
                            ),
                            **(
                                {"stage_xy_folder": Path(r["path"]).parent.name}
                                if kind == "scan"
                                else {}
                            ),
                        },
                    )
                )
            )
        acquisition = _ident(parent)
        cores.append(
            dict(
                uid=_ident(signature + "/" + core),
                acquisition=acquisition,
                slide=_ident(Path(parent).name),
                core=core,
                channel="AF",
                group=group_id,
                fovs=fovs,
                role="raw_camera",
                geometry_reviewed=False,
                blocked_reasons=reasons,
                metadata={
                    "source_folder": parent + "/" + core + "/" + lamp,
                    "coordinate_units": "unconfirmed",
                    "inventory_files": inventory["files"],
                    "source_sets": inventory["source_sets"],
                    "selected_source_set": kind,
                    "sequence_reviewed": kind not in {None, "scan"},
                    "sequence_source": "numeric_fov_index"
                    if kind in {"stitch", "stitch_folder"}
                    else "unconfirmed",
                },
            )
        )
    blocked = sum(bool(c["blocked_reasons"]) for c in cores)
    data = dict(
        schema_version=1,
        dataset_id="TMA-2026",
        raw_root=str(Path(raw_root).resolve()),
        groups=list(groups.values()),
        cores=cores,
        pairs=[],
        metadata={
            "import_coverage": {
                "discovered_core_acquisitions": len(cores),
                "eligible": len(cores) - blocked,
                "blocked": blocked,
                "inventory_files": inventory_files,
                "unmapped_files": unmapped_files,
            },
            "note": "Add reviewed geometry and explicit AF-HE section pairs before those stages.",
        },
    )
    if not cores:
        raise ValueError("No recognizable core acquisition folders found; review the source layout")
    return data
