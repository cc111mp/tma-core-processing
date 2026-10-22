"""Runtime source changes cannot publish a completed reconstruction or reuse cache."""

import shutil

import numpy as np
import pytest

from tests.integration.test_pipeline import fixture, rows
from tma_qc.config import load_config
from tma_qc.io.images import write_tiff
from tma_qc.io.manifest import Manifest
from tma_qc.pipeline import Runner, run_pipeline
from tma_qc.preflight import check
from tma_qc.provenance import read_json, write_json
from tma_qc.storage import validate_writable_tree


@pytest.mark.parametrize(
    "tree,relative",
    [
        ("output", "acquisitions"),
        ("output", "acquisitions/nested"),
        ("output", "reports"),
        ("cache", "state"),
        ("cache", "scratch"),
    ],
)
def test_preexisting_writable_tree_alias_is_rejected_before_writes(tmp_path, tree, relative):
    manifest_path, config_path, output = fixture(tmp_path)
    manifest = Manifest(manifest_path)
    config = load_config(config_path)
    location = output if tree == "output" else tmp_path / "cache"
    config["runtime"]["cache_root"] = str(tmp_path / "cache")
    alias = location / relative
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(manifest.raw_root, target_is_directory=True)
    before = {str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")}
    plan = check(manifest, config, output)
    assert not plan["ready_for_execution"]
    assert any("symbolic link" in item["reason"] for item in plan["issues"])
    with pytest.raises(ValueError, match="symbolic link"):
        Runner(manifest, output, config)
    assert {str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")} == before


def test_alias_validation_does_not_traverse_the_target(tmp_path, monkeypatch):
    from tma_qc import storage

    output, target = tmp_path / "output", tmp_path / "raw"
    output.mkdir()
    target.mkdir()
    (output / "acquisitions").symlink_to(target, target_is_directory=True)
    original = storage.os.scandir

    def observe(path):
        if str(path) != str(output):
            pytest.fail("Writable-tree validation traversed a symlink target")
        return original(path)

    monkeypatch.setattr(storage.os, "scandir", observe)
    with pytest.raises(ValueError, match="symbolic link"):
        validate_writable_tree(output)


def test_writable_tree_scan_tolerates_departing_temporary_directories(tmp_path, monkeypatch):
    from tma_qc import storage

    temporary = tmp_path / "scratch/temporary"
    temporary.mkdir(parents=True)
    original = storage.os.scandir
    removed = []

    def departed(path):
        if path == temporary:
            temporary.rmdir()
            removed.append(path)
        return original(path)

    monkeypatch.setattr(storage.os, "scandir", departed)
    assert validate_writable_tree(tmp_path) == tmp_path
    assert removed == [temporary]


def test_writable_tree_scan_does_not_ignore_permission_errors(tmp_path, monkeypatch):
    from tma_qc import storage

    def denied(path):
        raise PermissionError("unreadable writable tree")

    monkeypatch.setattr(storage.os, "scandir", denied)
    with pytest.raises(PermissionError, match="unreadable"):
        validate_writable_tree(tmp_path)


def test_report_rebuild_rejects_alias_into_raw_archive(tmp_path):
    from tma_qc.cli import main

    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)
    raw = Manifest(manifest).raw_root
    before = {str(path.relative_to(raw)) for path in raw.rglob("*")}
    (output / "reports").rename(output / "reports.original")
    (output / "reports").symlink_to(raw, target_is_directory=True)
    assert main(["report", "--run", str(output)]) == 1
    assert {str(path.relative_to(raw)) for path in raw.rglob("*")} == before


@pytest.mark.parametrize(
    "location", ["output_parent", "output_child", "cache_parent", "cache_child"]
)
def test_raw_output_and_cache_trees_are_disjoint_before_any_writes(tmp_path, location):
    manifest_path, config_path, output = fixture(tmp_path)
    manifest = Manifest(manifest_path)
    config = load_config(config_path)
    if location == "output_parent":
        output = manifest.raw_root.parent
    elif location == "output_child":
        output = manifest.raw_root / "new-results"
    elif location == "cache_parent":
        config["runtime"]["cache_root"] = str(manifest.raw_root.parent)
    else:
        config["runtime"]["cache_root"] = str(manifest.raw_root / "new-cache")
    before = {str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")}
    assert not check(manifest, config, output)["ready_for_execution"]
    with pytest.raises(ValueError, match="disjoint"):
        Runner(manifest, output, config)
    assert {str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")} == before


def test_synthetic_acquisitions_raw_archive_is_not_modified_through_output_ancestor(tmp_path):
    manifest, config, output = fixture(tmp_path)
    output.mkdir()
    raw = output / "acquisitions"
    shutil.move(str(manifest.parent / "inputs"), raw)
    data = read_json(manifest)
    data["raw_root"] = str(raw)
    write_json(manifest, data)
    before = {str(path.relative_to(raw)) for path in raw.rglob("*")}
    with pytest.raises(ValueError, match="disjoint"):
        run_pipeline(manifest, output, config)
    assert {str(path.relative_to(raw)) for path in raw.rglob("*")} == before


@pytest.mark.parametrize("budget", [0, 512 * 1024 * 1024])
def test_af_change_during_diagnostic_is_isolated_from_other_cores(tmp_path, monkeypatch, budget):
    from tma_qc.stages import tile_qc

    manifest, config, output = fixture(tmp_path)
    config.write_text(
        config.read_text().replace("[runtime]", f"[runtime]\ndecoded_cache_bytes={budget}")
    )
    original = tile_qc.run

    def changed(ctx, out):
        payload = original(ctx, out)
        if ctx.core.uid == "demo-A1":
            path = next(iter(ctx.raw_frames().values()))
            write_tiff(path, np.full((160, 160), 999, np.uint16))
        return payload

    monkeypatch.setattr(tile_qc, "run", changed)
    result = rows(run_pipeline(manifest, output, config))
    assert result["demo-A1", "tile_qc"]["execution"] == "error"
    assert "Verified source changed" in result["demo-A1", "tile_qc"]["findings"][0]
    assert result["demo-A1", "mosaic"]["execution"] == "blocked"
    assert result["demo-B3", "mosaic"]["execution"] == "complete"


def test_he_change_between_qc_and_registration_cannot_be_consumed(tmp_path, monkeypatch):
    from tma_qc.stages import registration

    manifest, config, output = fixture(tmp_path)
    original = Runner.stage

    def after_qc(self, ctx, unit, base, name, *args, **kwargs):
        result = original(self, ctx, unit, base, name, *args, **kwargs)
        if unit == "demo-pair" and name == "he_qc":
            write_tiff(ctx.he_path, np.full((256, 256, 3), 100, np.uint8), photometric="rgb")
        return result

    def forbidden(*args):
        pytest.fail("Changed H&E source reached registration")

    monkeypatch.setattr(Runner, "stage", after_qc)
    monkeypatch.setattr(registration, "run", forbidden)
    result = rows(run_pipeline(manifest, output, config))
    assert result["demo-pair", "he_qc"]["execution"] == "complete"
    assert result["demo-pair", "registration"]["execution"] == "error"
    assert result["demo-pair", "registration_qc"]["execution"] == "blocked"
    assert "aligned_he" not in result["demo-pair", "registration"]["outputs"]
    assert result["demo-A1", "mosaic"]["execution"] == "complete"


def test_late_source_change_during_cache_validation_is_a_new_error_observation(
    tmp_path, monkeypatch
):
    manifest, config, output = fixture(tmp_path)
    first = rows(run_pipeline(manifest, output, config))
    geometry = first["demo-A1", "geometry"]
    original = Runner.cached_stage

    def changed(self, unit, name, *args):
        result = original(self, unit, name, *args)
        if unit == "demo-A1" and name == "geometry" and result is not None:
            write_tiff(manifest.parent / "inputs/A1/FOV_1.tif", np.full((160, 160), 999, np.uint16))
        return result

    monkeypatch.setattr(Runner, "cached_stage", changed)
    second = rows(run_pipeline(manifest, output, config))
    failed = second["demo-A1", "geometry"]
    assert failed["execution"] == "error" and not failed["cached"]
    assert failed["fingerprint"] != geometry["fingerprint"]
    assert all((output / artifact["path"]).is_file() for artifact in geometry["outputs"].values())
    assert second["demo-B3", "mosaic"]["execution"] == "complete"


@pytest.mark.parametrize("asset", ["positions", "transform", "landmarks"])
def test_changed_asset_cannot_publish_measurements_under_its_prior_hash(
    tmp_path, monkeypatch, asset
):
    from tma_qc.stages import geometry, registration

    manifest, config, output = fixture(tmp_path)
    module, function, unit, stage = {
        "positions": (geometry, "run", "demo-A1", "geometry"),
        "transform": (registration, "run", "demo-pair", "registration"),
        "landmarks": (registration, "evaluate", "demo-pair", "registration_qc"),
    }[asset]
    original = getattr(module, function)

    def changed(ctx, out):
        selected = ctx.core.uid if ctx.core else ctx.pair.uid
        if selected == unit:
            path = {
                "positions": manifest.parent / "assets/A1-positions.json",
                "transform": manifest.parent / "assets/transform.json",
                "landmarks": manifest.parent / "assets/landmarks.json",
            }[asset]
            # Identical numerical meaning still changes the frozen input.
            with path.open("a") as stream:
                stream.write(" ")
        return original(ctx, out)

    monkeypatch.setattr(module, function, changed)
    result = rows(run_pipeline(manifest, output, config))
    assert result[unit, stage]["execution"] == "error"
    assert "Verified source changed" in result[unit, stage]["findings"][0]
    assert result["demo-B3", "mosaic"]["execution"] == "complete"


def test_core_config_cannot_change_between_runtime_identity_and_fitting(tmp_path, monkeypatch):
    from tests.integration.test_core_registration import core_fixture
    from tma_qc.stages import registration

    manifest, config, output = core_fixture(tmp_path)
    original = registration.fit_core

    def changed(*args):
        path = config.parent / "core.toml"
        path.write_text(path.read_text().replace("export_chunk=128", "export_chunk=256"))
        return original(*args)

    monkeypatch.setattr(registration, "fit_core", changed)
    result = rows(run_pipeline(manifest, output, config))
    assert result["pair-A1", "registration"]["execution"] == "error"
    assert "Verified source changed" in result["pair-A1", "registration"]["findings"][0]
    assert result["AF-A1", "mosaic"]["execution"] == "complete"
