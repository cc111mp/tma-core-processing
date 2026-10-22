"""Importers preserve concurrent outputs and reject ambiguous source identities."""

import csv
import sqlite3
from pathlib import Path

import pytest

from tma_qc import provenance
from tma_qc.config import load_config
from tma_qc.demo import create_demo
from tma_qc.io.folder import import_raw_folder
from tma_qc.io.manifest import Manifest, import_copy_inventory
from tma_qc.io.section_pairs import import_section_pairs


def inventory(path, *, empty=False):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE meta (key TEXT,value TEXT)")
        db.execute("INSERT INTO meta VALUES ('inventory_complete','true')")
        db.execute(
            "CREATE TABLE entries (path TEXT,kind TEXT,source_sha256 TEXT,"
            "destination_verified INTEGER,copied INTEGER,copy_error TEXT,inventory_error TEXT)"
        )
        if not empty:
            db.execute(
                "INSERT INTO entries VALUES (?,'file',?,1,1,NULL,NULL)",
                ("slide/A1/stitch/FOV_1.tif", "a" * 64),
            )
    return path


@pytest.mark.parametrize("importer", ["folder", "transfer", "pairs"])
def test_importer_cannot_overwrite_concurrent_manifest(tmp_path, monkeypatch, importer):
    output = tmp_path / "manifest.json"
    if importer == "folder":
        raw = tmp_path / "raw"
        tile = raw / "slide/A1/stitch/FOV_1.tif"
        tile.parent.mkdir(parents=True)
        tile.write_bytes(b"synthetic source")

        def publish():
            return import_raw_folder(raw, output)
    elif importer == "transfer":
        database = inventory(tmp_path / "inventory.sqlite3")

        def publish():
            return import_copy_inventory(database, tmp_path / "raw", output)
    else:
        manifest, _ = create_demo(tmp_path / "inputs")
        source = Manifest(manifest)
        table = tmp_path / "pairs.csv"
        with table.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["uid", "af_core", "he_path", "relationship"])
            writer.writerow(["new-pair", "demo-A1", source.pairs[0].he_path, "same_section"])

        def publish():
            return import_section_pairs(manifest, table, output)

    original = provenance.os.link

    def concurrent_output(source, destination):
        Path(destination).write_text("concurrent user manifest")
        return original(source, destination)

    monkeypatch.setattr(provenance.os, "link", concurrent_output)
    with pytest.raises(FileExistsError):
        publish()
    assert output.read_text() == "concurrent user manifest"
    assert not list(tmp_path.glob(".manifest-*"))


def test_transfer_import_rejects_raw_output_and_invalid_manifest_before_writing(tmp_path):
    database = inventory(tmp_path / "inventory.sqlite3")
    raw = tmp_path / "raw"
    with pytest.raises(ValueError, match="outside the raw archive"):
        import_copy_inventory(database, raw, raw / "manifest.json")
    assert not raw.exists()
    with sqlite3.connect(database) as db:
        db.execute("UPDATE entries SET path='slide..invalid/A1/stitch/FOV_1.tif'")
    output = tmp_path / "invalid.json"
    with pytest.raises(ValueError, match="identifier"):
        import_copy_inventory(database, raw, output)
    assert not output.exists()


@pytest.mark.parametrize("alias", ["relative", "absolute", "symlink"])
def test_manifest_rejects_one_source_assigned_through_path_aliases(tmp_path, alias):
    path, _ = create_demo(tmp_path / "inputs")
    data = provenance.read_json(path)
    fovs = data["cores"][0]["fovs"]
    original = path.parent / data["raw_root"] / fovs[0]["path"]
    if alias == "relative":
        fovs[1]["path"] = "A1/unused/../FOV_1.tif"
    elif alias == "absolute":
        fovs[1]["path"] = str(original)
    else:
        link = original.parent / "alias.tif"
        link.symlink_to(original)
        fovs[1]["path"] = str(link)
    with pytest.raises(ValueError, match="one resolved source path"):
        Manifest(path, data=data)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "5", float("nan")])
def test_landmark_minimum_requires_positive_integer(value):
    with pytest.raises(ValueError, match="qc.minimum_landmarks"):
        load_config(base={"qc": {"minimum_landmarks": value}})


def test_explicit_positive_landmark_minimum_is_preserved():
    assert load_config(base={"qc": {"minimum_landmarks": 2}})["qc"]["minimum_landmarks"] == 2
