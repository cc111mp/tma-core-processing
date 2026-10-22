"""Explicit pairing, path portability and non-destructive manifest publication."""

import csv
from pathlib import Path

import pytest

from tma_qc.io.manifest import Manifest
from tma_qc.io.section_pairs import import_section_pairs
from tma_qc.provenance import sha256, write_json


def inputs(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    tile = raw / "tile.tif"
    tile.write_bytes(b"original AF bytes; metadata-only pairing does not decode")
    manifest = tmp_path / "manifest.json"
    write_json(
        manifest,
        {
            "schema_version": 1,
            "dataset_id": "study",
            "raw_root": "raw",
            "groups": [{"id": "group"}],
            "cores": [
                {
                    "uid": "AF-01-A1",
                    "slide": "AF-01",
                    "core": "A1",
                    "acquisition": "original",
                    "channel": "AF",
                    "group": "group",
                    "fovs": [{"id": "1", "path": "tile.tif", "sha256": sha256(tile)}],
                }
            ],
            "pairs": [],
        },
    )
    he = tmp_path / "he" / "HE-27" / "A1.tif"
    he.parent.mkdir(parents=True)
    he.write_bytes(b"original H&E bytes")
    csv_path = tmp_path / "pairing" / "pairs.csv"
    csv_path.parent.mkdir()
    return manifest, he, csv_path


def table(path, **changes):
    row = {
        "uid": "pair-01",
        "af_core": "AF-01-A1",
        "he_path": "../he/HE-27/A1.tif",
        "relationship": "neighboring_sections",
        "he_slide_label": "HE-27",
    }
    row.update(changes)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def test_pairing_retains_distinct_slide_labels_and_portable_asset_paths(tmp_path):
    manifest, he, path = inputs(tmp_path)
    landmarks = path.parent / "points.csv"
    landmarks.write_text("id,he_x,he_y,af_x,af_y\na,1,2,1,2\n")
    table(path, landmarks="points.csv", af_mpp="0.5", he_mpp="0.25")
    original_hash = sha256(manifest)
    output = tmp_path / "new-directory" / "paired.json"
    data = import_section_pairs(manifest, path, output)
    pair = Manifest(output).pairs[0]
    assert pair.af_core == "AF-01-A1" and pair.metadata["he_slide_label"] == "HE-27"
    assert Path(pair.he_path) == he and pair.he_sha256 == sha256(he)
    assert Path(pair.landmarks) == landmarks
    assert pair.metadata["af_mpp"] == 0.5 and pair.metadata["he_mpp"] == 0.25
    assert Manifest(output).raw_path(Manifest(output).cores[0].fovs[0]) == tmp_path / "raw/tile.tif"
    assert sha256(manifest) == original_hash
    assert data["metadata"]["section_pair_imports"][0]["sha256"] == sha256(path)


@pytest.mark.parametrize(
    "changes",
    [
        {"af_core": "UNKNOWN"},
        {"uid": "AF-01-A1"},
        {"relationship": "same_filename"},
        {"af_mpp": "nan"},
        {"he_mpp": "0"},
        {"he_path": "missing.tif"},
        {"landmarks": "missing.json"},
        {"typo": "value"},
    ],
)
def test_bad_pair_rows_never_publish_partial_manifest(tmp_path, changes):
    manifest, _, path = inputs(tmp_path)
    table(path, **changes)
    output = tmp_path / "paired.json"
    with pytest.raises((ValueError, OSError)):
        import_section_pairs(manifest, path, output)
    assert not output.exists()


def test_pair_output_is_new_and_outside_original_archive(tmp_path):
    manifest, _, path = inputs(tmp_path)
    table(path)
    with pytest.raises(ValueError, match="outside"):
        import_section_pairs(manifest, path, tmp_path / "raw/paired.json")
    before = manifest.read_bytes()
    with pytest.raises(FileExistsError):
        import_section_pairs(manifest, path, manifest)
    assert manifest.read_bytes() == before


def test_duplicate_pairs_are_rejected_across_rows_and_prior_manifest(tmp_path):
    manifest, _, path = inputs(tmp_path)
    table(path)
    output = tmp_path / "paired.json"
    import_section_pairs(manifest, path, output)
    with pytest.raises(ValueError, match="Duplicate section-pair"):
        import_section_pairs(output, path, tmp_path / "again.json")
    path.write_text(path.read_text() + path.read_text().splitlines()[1] + "\n")
    with pytest.raises(ValueError, match="Duplicate section-pair"):
        import_section_pairs(manifest, path, tmp_path / "duplicate.json")


def test_changed_or_linked_he_source_prevents_publication(tmp_path, monkeypatch):
    from tma_qc.io import section_pairs

    manifest, he, path = inputs(tmp_path)
    table(path)
    original = section_pairs._source_hash

    def changing(root, entry):
        digest = original(root, entry)
        if root / entry["path"] == he:
            he.write_bytes(b"changed during import")
        return digest

    monkeypatch.setattr(section_pairs, "_source_hash", changing)
    with pytest.raises(ValueError, match="changed"):
        import_section_pairs(manifest, path, tmp_path / "changed.json")
    monkeypatch.setattr(section_pairs, "_source_hash", original)
    link = he.with_name("link.tif")
    link.symlink_to(he)
    table(path, he_path="../he/HE-27/link.tif")
    with pytest.raises(ValueError, match="symbolic"):
        import_section_pairs(manifest, path, tmp_path / "linked.json")


def test_concurrent_output_is_preserved(tmp_path, monkeypatch):
    from tma_qc import provenance

    manifest, _, path = inputs(tmp_path)
    table(path)
    output = tmp_path / "paired.json"
    original = provenance.os.link

    def competing(source, target):
        output.write_text("created by another importer")
        original(source, target)

    monkeypatch.setattr(provenance.os, "link", competing)
    with pytest.raises(FileExistsError):
        import_section_pairs(manifest, path, output)
    assert output.read_text() == "created by another importer"
    assert not list(tmp_path.glob(".manifest-*"))


@pytest.mark.parametrize(
    "content",
    [
        "uid,af_core,he_path,relationship,uid\n",
        "uid,af_core,he_path,relationship\n",
        "uid,af_core,he_path,relationship\na,AF-01-A1,missing.tif\n",
    ],
)
def test_malformed_or_empty_pair_table_is_rejected(tmp_path, content):
    manifest, _, path = inputs(tmp_path)
    path.write_text(content)
    with pytest.raises(ValueError):
        import_section_pairs(manifest, path, tmp_path / "paired.json")
