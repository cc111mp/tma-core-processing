"""Direct-folder discovery must retain source identities and unresolved acquisitions."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from tma_qc.io import folder
from tma_qc.io.manifest import Manifest, import_copy_inventory
from tma_qc.provenance import read_json, write_json


def raw_file(root, relative, content=b"synthetic source bytes"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_nas_wrappers_original_labels_and_numeric_order(tmp_path):
    root = tmp_path / "raw"
    base = "Project 1/Raw/D049Ga01_01_CQ_UV/D049Ga01_01_CQ_UV/A5/middle lamp 275"
    originals = []
    for index in range(12, 0, -1):
        originals.append(raw_file(root, f"{base}/stitch/{index}_0.6.099.tiff", str(index).encode()))
    raw_file(root, f"{base}/auto_focus/1_2/100.tif")
    raw_file(root, "@eaDir/unrelated.tif")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in originals}
    output = tmp_path / "manifest.json"
    data = folder.import_raw_folder(root, output, dataset_id="study-01")
    core = data["cores"][0]
    assert data["dataset_id"] == "study-01" and data["pairs"] == []
    assert core["core"] == "A5" and core["channel"] == "AF"
    assert core["metadata"]["source_slide_label"] == "D049Ga01_01_CQ_UV"
    assert core["metadata"]["source_lamp_label"] == "middle lamp 275"
    assert core["metadata"]["source_FOV_set_verified"] is False
    assert [f["id"] for f in core["fovs"]] == [str(i) for i in range(1, 13)]
    for fov in core["fovs"]:
        assert fov["sha256"] == hashlib.sha256((root / fov["path"]).read_bytes()).hexdigest()
        assert "destination_verified_at_import" not in fov["metadata"]
    assert data["metadata"]["folder_import"]["hashed_files"] == 12
    assert data["metadata"]["folder_import"]["hashed_bytes"] == sum(
        len(str(i)) for i in range(1, 13)
    )
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in originals}
    assert Manifest(output).cores[0].uid == core["uid"]


def test_scan_identity_is_preserved_without_inventing_native_geometry(tmp_path):
    root = tmp_path / "raw"
    for position in ["9_2", "10_2"]:
        raw_file(root, f"slide/A1/scan/{position}/100.tif")
    data = folder.import_raw_folder(root, tmp_path / "manifest.json")
    core = data["cores"][0]
    assert not core["blocked_reasons"]
    assert core["metadata"]["sequence_reviewed"] is False
    assert core.get("positions") is None and core["geometry_reviewed"] is False
    assert {f["metadata"]["stage_xy_folder"] for f in core["fovs"]} == {"9_2", "10_2"}


def test_empty_ambiguous_and_unsupported_cores_remain_blocked(tmp_path):
    root = tmp_path / "raw"
    for name in ["100.tif", "101.tif"]:
        raw_file(root, f"slide/A1/scan/1_2/{name}")
    for index in [1, 3]:
        raw_file(root, f"slide/A2/stitch/FOV_{index}.tif")
    raw_file(root, "slide/A3/stitch_folder_bg/FOV_1.tif")
    (root / "slide/A4").mkdir()
    raw_file(root, "slide/A5/core.zip")
    data = folder.import_raw_folder(root, tmp_path / "manifest.json")
    cores = {c["core"]: c for c in data["cores"]}
    assert set(cores) == {"A1", "A2", "A3", "A4", "A5"}
    assert all(c["blocked_reasons"] and c["fovs"] == [] for c in cores.values())
    assert "Z/exposure" in cores["A1"]["blocked_reasons"][0]
    assert "Missing FOV" in cores["A2"]["blocked_reasons"][0]
    assert data["metadata"]["folder_import"]["hashed_files"] == 0
    assert data["metadata"]["import_coverage"]["blocked"] == 5


def test_identity_matches_transfer_import_and_hashes_only_selected_tiles(tmp_path, monkeypatch):
    root = tmp_path / "raw"
    base = "project/slide/slide/A1/middle lamp 275"
    selected = raw_file(root, f"{base}/stitch/FOV_1.tif")
    raw_file(root, f"{base}/scan/1_2/100.tif")
    raw_file(root, f"{base}/stitch_folder_bg/FOV_1.tif")
    hashed = []
    original = folder._source_hash

    def record_hash(root, entry):
        hashed.append(entry["path"])
        return original(root, entry)

    monkeypatch.setattr(folder, "_source_hash", record_hash)
    direct = folder.import_raw_folder(root, tmp_path / "folder.json")
    assert hashed == [selected.relative_to(root).as_posix()]
    database = tmp_path / "inventory.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE meta (key TEXT,value TEXT)")
        db.execute("INSERT INTO meta VALUES ('inventory_complete','true')")
        db.execute(
            "CREATE TABLE entries (path TEXT,kind TEXT,source_sha256 TEXT,"
            "destination_verified INTEGER,copied INTEGER,copy_error TEXT,inventory_error TEXT)"
        )
        for path in sorted(root.rglob("*")):
            if path.is_file():
                db.execute(
                    "INSERT INTO entries VALUES (?,'file',?,1,1,NULL,NULL)",
                    (
                        path.relative_to(root).as_posix(),
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                    ),
                )
    copied = import_copy_inventory(database, root, tmp_path / "copy.json")
    for field in ["uid", "slide", "core", "acquisition", "channel", "group"]:
        assert direct["cores"][0][field] == copied["cores"][0][field]
    assert direct["cores"][0]["fovs"][0]["sha256"] == copied["cores"][0]["fovs"][0]["sha256"]


def test_repeated_acquisitions_and_lamps_have_distinct_core_uids(tmp_path):
    root = tmp_path / "raw"
    for acquisition, lamp in [("original", "275"), ("repeat", "275"), ("original", "365")]:
        raw_file(root, f"{acquisition}/slide/A1/middle lamp {lamp}/stitch/FOV_1.tif")
    data = folder.import_raw_folder(root, tmp_path / "manifest.json")
    assert len(data["cores"]) == len({c["uid"] for c in data["cores"]}) == 3


def test_model_map_assignments_are_reused(tmp_path):
    root = tmp_path / "raw"
    raw_file(root, "slide/A1/stitch_folder/FOV_1.tif")
    data = folder.import_raw_folder(root, tmp_path / "first.json")
    group = data["groups"][0]["id"]
    models = tmp_path / "models.json"
    assets = {"flatfield": "models/flat.tif", "darkfield": "models/dark.tif"}
    write_json(models, {group: assets})
    result = folder.import_raw_folder(root, tmp_path / "second.json", models)
    assert all(result["groups"][0][key] == value for key, value in assets.items())


def test_source_mutation_during_hashing_publishes_no_manifest(tmp_path, monkeypatch):
    root = tmp_path / "raw"
    raw_file(root, "slide/A1/stitch/FOV_1.tif")
    original = folder.os.read
    changed = False

    def mutate(descriptor, size):
        nonlocal changed
        value = original(descriptor, size)
        if value and not changed:
            changed = True
            (root / "slide/A1/stitch/FOV_1.tif").write_bytes(b"changed source")
        return value

    monkeypatch.setattr(folder.os, "read", mutate)
    output = tmp_path / "manifest.json"
    with pytest.raises(ValueError, match="changed during hashing"):
        folder.import_raw_folder(root, output)
    assert not output.exists()


def test_new_tile_during_hashing_publishes_no_manifest(tmp_path, monkeypatch):
    root = tmp_path / "raw"
    raw_file(root, "slide/A1/stitch/FOV_1.tif")
    original = folder._source_hash

    def add_tile(root, entry):
        digest = original(root, entry)
        raw_file(root, "slide/A1/stitch/FOV_2.tif")
        return digest

    monkeypatch.setattr(folder, "_source_hash", add_tile)
    output = tmp_path / "manifest.json"
    with pytest.raises(ValueError, match="folder changed"):
        folder.import_raw_folder(root, output)
    assert not output.exists()


def test_raw_archive_and_existing_manifest_are_preserved(tmp_path):
    root = tmp_path / "raw"
    raw_file(root, "slide/A1/stitch/FOV_1.tif")
    with pytest.raises(ValueError, match="outside the raw"):
        folder.import_raw_folder(root, root / "manifest.json")
    output = tmp_path / "manifest.json"
    output.write_text("existing manifest")
    with pytest.raises(FileExistsError):
        folder.import_raw_folder(root, output)
    assert output.read_text() == "existing manifest"


def test_links_are_not_followed_and_no_manifest_is_written(tmp_path):
    root = tmp_path / "raw"
    path = raw_file(root, "slide/A1/stitch/FOV_1.tif")
    raw_file(tmp_path, "external.tif")
    path.unlink()
    path.symlink_to(tmp_path / "external.tif")
    output = tmp_path / "manifest.json"
    with pytest.raises(ValueError, match="Symbolic links"):
        folder.import_raw_folder(root, output)
    assert not output.exists()


def test_unreadable_directory_does_not_become_a_partial_manifest(tmp_path, monkeypatch):
    root = tmp_path / "raw"
    raw_file(root, "slide/A1/stitch/FOV_1.tif")
    original = folder.os.scandir

    def fail(path):
        if Path(path).name == "stitch":
            raise PermissionError("unreadable acquisition")
        return original(path)

    monkeypatch.setattr(folder.os, "scandir", fail)
    output = tmp_path / "manifest.json"
    with pytest.raises(PermissionError):
        folder.import_raw_folder(root, output)
    assert not output.exists()


def test_invalid_generated_identity_does_not_publish_invalid_json(tmp_path):
    root = tmp_path / "raw"
    raw_file(root, "slide..invalid/A1/stitch/FOV_1.tif")
    output = tmp_path / "manifest.json"
    with pytest.raises(ValueError, match="identifier"):
        folder.import_raw_folder(root, output)
    assert not output.exists()


def test_manifest_can_be_validated_before_publication(tmp_path):
    root = tmp_path / "raw"
    raw_file(root, "slide/A1/stitch/FOV_1.tif")
    data = folder.import_raw_folder(root, tmp_path / "manifest.json")
    target = tmp_path / "not-written.json"
    assert (
        Manifest(target, data=read_json(tmp_path / "manifest.json")).dataset_id
        == data["dataset_id"]
    )
    assert not target.exists()
