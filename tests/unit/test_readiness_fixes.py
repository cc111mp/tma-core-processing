import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from tma_qc.cli import main
from tma_qc.config import load_config
from tma_qc.demo import create_demo
from tma_qc.io.coordinates import load_positions, position_document
from tma_qc.io.images import write_tiff
from tma_qc.io.manifest import Manifest
from tma_qc.preflight import check
from tma_qc.provenance import read_json, write_json
from tma_qc.schema import NeedsInput
from tma_qc.state import State
from tma_qc.storage import estimate_batch, require_space, space_budget


@pytest.mark.parametrize(
    "field,value",
    [
        ("coordinate_space", "stage_microns"),
        ("coordinate_space", "preview_pixels"),
        ("axes", "YX"),
        ("origin", "lower_left"),
        ("coordinate_space", None),
        ("axes", None),
        ("origin", None),
    ],
)
def test_position_frame_must_be_explicit_native_xy_upper_left(tmp_path, field, value):
    rows = [{"id": "1", "x": 0, "y": 0}, {"id": "2", "x": 140, "y": 20}]
    path = tmp_path / "positions.json"
    data = position_document(rows)
    data[field] = value
    write_json(path, data)
    with pytest.raises(ValueError, match="declare"):
        load_positions(path, ["1", "2"])
    write_json(path, position_document(rows))
    assert load_positions(path, ["1", "2"]) == rows


def test_legacy_array_requires_deliberate_external_conversion(tmp_path):
    path = tmp_path / "positions.json"
    write_json(path, [{"id": "1", "x": 0, "y": 0}])
    with pytest.raises(ValueError, match="legacy arrays"):
        load_positions(path, ["1"])


@pytest.mark.parametrize("row", [None, {"id": "1"}])
def test_malformed_coordinate_rows_fail_with_a_validation_error(tmp_path, row):
    path = tmp_path / "positions.json"
    write_json(path, position_document([row]))
    with pytest.raises(ValueError, match="Each position"):
        load_positions(path, ["1"])


def test_checkpoint_repair_preserves_corrupt_bytes_and_holds_output_lock(tmp_path):
    broken = tmp_path / "b/state.sqlite3"
    broken.parent.mkdir()
    payload = b"corrupt checkpoint proof"
    broken.write_bytes(payload)
    a = State(tmp_path / "a", lock_identity=tmp_path / "output")
    b = State(tmp_path / "b", lock_identity=tmp_path / "output")
    try:
        with a.lock():
            with pytest.raises(RuntimeError, match="Another process"):
                with b.lock():
                    pass
            assert broken.read_bytes() == payload
            assert not (broken.parent / "quarantine").exists()
        with b.lock():
            assert b.db.execute("PRAGMA quick_check").fetchone() == ("ok",)
            preserved = Path(b.events[0]["quarantine"]) / "state.sqlite3"
            assert preserved.read_bytes() == payload
    finally:
        a.close()
        b.close()


def test_checkpoint_io_error_is_not_quarantined_as_corruption(tmp_path, monkeypatch):
    state = State(tmp_path / "cache")

    def io_failure():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(state, "_open", io_failure)
    with pytest.raises(sqlite3.OperationalError, match="I/O"):
        with state.lock():
            pass
    assert not state.events and not (state.directory / "quarantine").exists()


@pytest.mark.parametrize("same_device", [True, False])
def test_capacity_combines_output_and_scratch_on_the_same_device(monkeypatch, same_device):
    def volume(path):
        return {
            "device": 1 if same_device or str(path) == "/output" else 2,
            "free_bytes": 100,
            "existing_path": str(path),
        }

    monkeypatch.setattr("tma_qc.storage.volume_info", volume)
    requests = [("/output", 60, "outputs"), ("/scratch", 40, "scratch")]
    volumes = space_budget(requests, {"reserve_bytes": 10, "safety_factor": 1.0})
    if same_device:
        assert len(volumes) == 1 and volumes[0]["required_bytes"] == 110
        with pytest.raises(NeedsInput, match="Insufficient storage"):
            require_space(requests, {"reserve_bytes": 10, "safety_factor": 1.0})
    else:
        assert len(volumes) == 2 and all(v["sufficient"] for v in volumes)


def test_low_space_blocks_blend_before_any_large_allocation(tmp_path, monkeypatch):
    import tifffile

    from tma_qc.backends.blend import blend

    source = write_tiff(tmp_path / "source.tif", np.ones((16, 16), np.uint16))
    monkeypatch.setattr(
        "tma_qc.storage.volume_info",
        lambda path: {"device": 1, "free_bytes": 1, "existing_path": str(tmp_path)},
    )
    mapped = tifffile.memmap

    def allocation(*args, **kwargs):
        if kwargs.get("mode") == "r":
            return mapped(*args, **kwargs)
        pytest.fail("Blend must check capacity before allocating mosaic files")

    monkeypatch.setattr("tma_qc.backends.blend.tifffile.memmap", allocation)
    with pytest.raises(NeedsInput, match="Insufficient storage"):
        blend(
            {"1": source},
            [{"id": "1", "x": 0, "y": 0}],
            tmp_path / "out.tif",
            tmp_path / "coverage.tif",
            tmp_path / "scratch",
        )
    assert not (tmp_path / "out.tif").exists() and not (tmp_path / "scratch").exists()


def test_plan_checks_missing_assets_and_exits_nonzero_without_writing(tmp_path, capsys):
    manifest, config = create_demo(tmp_path / "demo")
    data = read_json(manifest)
    data["cores"][0]["positions"] = "missing-positions.json"
    data["cores"][0]["fovs"][0]["path"] = "missing-raw.tif"
    data["groups"][0]["flatfield"] = "missing-flat.tif"
    write_json(manifest, data)
    output = tmp_path / "not-created"
    status = main(
        ["plan", "--manifest", str(manifest), "--config", str(config), "--out", str(output)]
    )
    result = json.loads(capsys.readouterr().out)
    assert status == 2 and not result["ready_for_execution"]
    checks = {row["check"] for row in result["issues"]}
    assert {"raw hashes and TIFF headers", "illumination fields", "native geometry"} <= checks
    assert not output.exists() and not (manifest.parent / "cache").exists()


def test_ready_plan_verifies_hashes_and_headers_but_never_decodes_raw_images(tmp_path, monkeypatch):
    manifest, path = create_demo(tmp_path / "demo")

    def no_decode(*args, **kwargs):
        pytest.fail("Preflight must not decode raw FOVs")

    monkeypatch.setattr("tma_qc.qc.integrity.read_image", no_decode)
    result = check(Manifest(manifest), load_config(path), tmp_path / "output")
    assert result["ready_for_execution"] and result["raw_files_hash_and_header_verified"] == 8
    assert result["AF_storage"]["sufficient"] and not result["automatic_image_quality_release"]
    fov = manifest.parent / "inputs/B3/FOV_1.tif"
    image = np.ones((160, 160), np.uint16)
    write_tiff(fov, image)
    result = check(Manifest(manifest), load_config(path), tmp_path / "output")
    assert not result["ready_for_execution"]
    assert any("SHA256 mismatch" in row["reason"] for row in result["issues"])


def test_batch_budget_rejects_cohort_when_only_one_core_would_fit(tmp_path, monkeypatch):
    manifest, path = create_demo(tmp_path / "demo")
    manifest = Manifest(manifest)
    config = load_config(path)
    config["storage"] = {"reserve_bytes": 0, "safety_factor": 1.0}
    single = estimate_batch(manifest, config, tmp_path / "out", ["demo-A1"])
    free = single["retained_bytes_estimate"] + single["peak_blend_scratch_bytes"] + 1
    monkeypatch.setattr(
        "tma_qc.storage.volume_info",
        lambda path: {"device": 1, "free_bytes": free, "existing_path": str(tmp_path)},
    )
    assert estimate_batch(manifest, config, tmp_path / "out", ["demo-A1"])["sufficient"]
    assert not estimate_batch(manifest, config, tmp_path / "out")["sufficient"]
    assert not check(manifest, config, tmp_path / "out")["ready_for_execution"]


def test_mist_preflight_requires_reviewed_sequence_and_real_runtime_inputs(tmp_path):
    manifest, path = create_demo(tmp_path / "demo")
    manifest = Manifest(manifest)
    core = manifest.cores[0]
    core.positions = None
    core.metadata.update(
        grid_reviewed=True,
        sequence_reviewed=False,
        grid={
            "rows": 2,
            "columns": 2,
            "numbering_pattern": "HORIZONTALCOMBING",
            "origin": "UL",
            "horizontal_overlap": 40,
            "vertical_overlap": 40,
        },
    )
    config = load_config(path)
    config["geometry"].update(backend="mist", mist_classpath=str(tmp_path / "missing.jar"))
    result = check(manifest, config, tmp_path / "out", [core.uid])
    assert not result["ready_for_execution"]
    assert any("acquisition sequence" in row["reason"] for row in result["issues"])
    core.metadata["sequence_reviewed"] = True
    result = check(manifest, config, tmp_path / "out", [core.uid])
    assert any("classpath" in row["reason"] for row in result["issues"])


def test_plan_without_output_cannot_report_operational_readiness(tmp_path):
    manifest, config = create_demo(tmp_path / "demo")
    result = check(Manifest(manifest), load_config(config))
    assert not result["ready_for_execution"]
    assert any("--out" in row["reason"] for row in result["issues"])


@pytest.mark.parametrize("text", ["reserve_bytes=-1", "safety_factor=0.9", "safety_factor=nan"])
def test_invalid_storage_settings_fail_early(tmp_path, text):
    path = tmp_path / "bad.toml"
    path.write_text("[storage]\n" + text + "\n")
    with pytest.raises(ValueError, match="storage"):
        load_config(path)
