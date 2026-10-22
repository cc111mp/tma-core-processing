"""A verified source identity survives disabled/evicted caches and decoding races."""

import numpy as np
import pytest

from tma_qc.io.images import read_image, write_tiff
from tma_qc.io.tile_cache import TileCache
from tma_qc.provenance import sha256
from tma_qc.qc.integrity import verify_file


@pytest.mark.parametrize("budget", [0, 1, 8192, 16384])
def test_verified_source_cannot_be_reloaded_after_replacement_or_eviction(tmp_path, budget):
    paths = [tmp_path / (name + ".tif") for name in ("a", "b")]
    identities = {}
    for path in paths:
        write_tiff(path, np.full((64, 64), 100, np.uint16))
        verify_file(path, sha256(path), identities=identities)
    cache = TileCache(paths, budget, identities=identities)
    cache.read(paths[0])
    cache.read(paths[1])
    write_tiff(paths[0], np.full((64, 64), 999, np.uint16))
    with pytest.raises(ValueError, match="Verified source changed"):
        cache.read(paths[0])


@pytest.mark.parametrize("budget", [0, 8192])
def test_tracked_raw_decode_checks_identity_even_without_retention(tmp_path, monkeypatch, budget):
    from tma_qc.io import tile_cache

    path = write_tiff(tmp_path / "raw.tif", np.full((64, 64), 100, np.uint16))
    identities = {}
    verify_file(path, sha256(path), identities=identities)
    original = tile_cache.read_image

    def changed(source):
        image = original(source)
        write_tiff(path, np.full((64, 64), 999, np.uint16))
        return image

    monkeypatch.setattr(tile_cache, "read_image", changed)
    with pytest.raises(ValueError, match="changed while being decoded"):
        TileCache([path], budget, identities=identities).read(path)


def test_parent_symlink_swap_does_not_escape_the_verified_path_set(tmp_path):
    source, replacement = tmp_path / "source", tmp_path / "replacement"
    source.mkdir()
    replacement.mkdir()
    path = write_tiff(source / "raw.tif", np.full((64, 64), 100, np.uint16))
    write_tiff(replacement / "raw.tif", np.full((64, 64), 999, np.uint16))
    identities = {}
    verify_file(path, sha256(path), identities=identities)
    cache = TileCache([path], 0, identities=identities)
    source.rename(tmp_path / "original")
    source.symlink_to(replacement, target_is_directory=True)
    with pytest.raises(ValueError, match="Verified source changed"):
        cache.read(path)


def test_change_during_sha_verification_does_not_pin_old_hash_to_new_identity(
    tmp_path, monkeypatch
):
    from tma_qc.qc import integrity

    path = write_tiff(tmp_path / "raw.tif", np.full((64, 64), 100, np.uint16))
    expected = sha256(path)
    original = integrity.os.read

    def changed(descriptor, size):
        block = original(descriptor, size)
        if block:
            write_tiff(path, np.full((64, 64), 999, np.uint16))
        return block

    monkeypatch.setattr(integrity.os, "read", changed)
    identities = {}
    with pytest.raises(ValueError, match="while SHA256 was being verified"):
        integrity.verify_file(path, expected, identities=identities)
    assert identities == {}


@pytest.mark.parametrize("require_digest", [False, True])
def test_transient_path_swap_cannot_hash_one_inode_then_pin_another(
    tmp_path, monkeypatch, require_digest
):
    from tma_qc.qc import integrity

    source, alternate = tmp_path / "source", tmp_path / "alternate"
    source.mkdir()
    alternate.mkdir()
    path = write_tiff(source / "raw.tif", np.full((64, 64), 100, np.uint16))
    write_tiff(alternate / "raw.tif", np.full((64, 64), 999, np.uint16))
    expected = sha256(path) if require_digest else None
    original = integrity.os.open

    def swapped(name, flags, *args, **kwargs):
        parked = tmp_path / "parked"
        source.rename(parked)
        alternate.rename(source)
        try:
            descriptor = original(name, flags, *args, **kwargs)
        finally:
            source.rename(alternate)
            parked.rename(source)
        return descriptor

    monkeypatch.setattr(integrity.os, "open", swapped)
    identities = {}
    with pytest.raises(ValueError, match="before SHA256 verification"):
        integrity.verify_file(path, expected, identities=identities)
    assert identities == {}


def test_absolute_asset_path_is_not_resolved_to_a_different_source(tmp_path):
    from tma_qc.workflows import _asset_hashes

    class FrozenManifest:
        def resolve(self, path):
            pytest.fail("An absolute normalized asset was resolved again")

    path = tmp_path / "positions.json"
    path.write_text("{}")
    identities = {}
    assert _asset_hashes(FrozenManifest(), [str(path)], identities=identities) == {
        str(path): sha256(path)
    }
    assert set(identities) == {path}


def test_verification_pins_before_first_decode(tmp_path):
    from tma_qc.io.manifest import Manifest
    from tma_qc.provenance import write_json
    from tma_qc.qc.integrity import verify_core

    path = write_tiff(tmp_path / "raw/FOV_1.tif", np.full((8, 8), 100, np.uint16))
    manifest_path = tmp_path / "manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": 1,
            "dataset_id": "study",
            "raw_root": "raw",
            "groups": [{"id": "group"}],
            "cores": [
                {
                    "uid": "core",
                    "acquisition": "a",
                    "slide": "s",
                    "core": "A1",
                    "channel": "AF",
                    "group": "group",
                    "fovs": [{"id": "1", "path": "FOV_1.tif", "sha256": sha256(path)}],
                }
            ],
        },
    )
    manifest = Manifest(manifest_path)
    identities = {}

    def changed(source):
        assert source in identities
        write_tiff(source, np.full((8, 8), 999, np.uint16))
        return read_image(source)

    with pytest.raises(ValueError, match="Verified source changed"):
        verify_core(
            manifest.cores[0],
            manifest,
            {"shape": [8, 8], "dtype": "uint16"},
            reader=changed,
            identities=identities,
        )
