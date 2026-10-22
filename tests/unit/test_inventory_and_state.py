import sqlite3

import pytest

from tma_qc.io.manifest import Manifest, import_copy_inventory
from tma_qc.provenance import sha256
from tma_qc.state import State


def test_inventory_import_is_read_only_and_requires_complete_transfer(tmp_path):
    path = tmp_path / "copy.sqlite3"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)")
    c.execute(
        "CREATE TABLE entries (path TEXT,kind TEXT,copied INTEGER,copy_error TEXT,inventory_error TEXT,source_sha256 TEXT,destination_verified INTEGER)"
    )
    c.execute("INSERT INTO meta VALUES ('inventory_complete','false')")
    entries = [
        "project/slide/slide/A1/middle lamp 275/stitch/FOV_00001.tif",
        "project/slide/slide/A1/middle lamp 275/stitch_folder_bg/FOV_00001.tif",
        "project/slide/slide/A1/middle lamp 275/scan/1_2/100.5.tif",
        "project/slide/slide/A1/middle lamp 275/scan/1_2/101.5.tif",
    ]
    for entry in entries:
        c.execute("INSERT INTO entries VALUES (?,'file',1,NULL,NULL,?,1)", (entry, "a" * 64))
    c.commit()
    with pytest.raises(ValueError, match="incomplete"):
        import_copy_inventory(path, tmp_path / "raw", tmp_path / "manifest.json")
    c.execute("UPDATE meta SET value='true'")
    c.commit()
    c.close()
    before = sha256(path)
    data = import_copy_inventory(path, tmp_path / "raw", tmp_path / "manifest.json")
    assert before == sha256(path)
    assert len(data["cores"]) == 1
    assert len(data["cores"][0]["fovs"]) == 1
    assert "/stitch/" in data["cores"][0]["fovs"][0]["path"]
    assert Manifest(tmp_path / "manifest.json").pairs == []


def test_state_refuses_network_sqlite_and_locks_independently_of_cache_directory(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("tma_qc.state.filesystem_type", lambda path: "cifs")
    with pytest.raises(ValueError, match="local storage"):
        State(tmp_path / "network")
    assert not (tmp_path / "network").exists()
    monkeypatch.setattr("tma_qc.state.filesystem_type", lambda path: "ext4")
    a = State(tmp_path / "a", lock_identity="same-output")
    b = State(tmp_path / "b", lock_identity="same-output")
    try:
        with a.lock():
            with pytest.raises(RuntimeError, match="Another process"):
                with b.lock():
                    pass
    finally:
        a.close()
        b.close()
