import csv
from pathlib import Path

import pytest

from tma_qc.cli import main
from tma_qc.demo import create_demo
from tma_qc.pipeline import run_pipeline
from tma_qc.provenance import read_json, write_json


def fixture(tmp_path):
    manifest, config = create_demo(tmp_path / "demo")
    return manifest, config, tmp_path / "output"


def table(output):
    with (output / "reports/core-qc.csv").open(encoding="utf-8-sig") as handle:
        return {row["uid"]: row for row in csv.DictReader(handle)}


def test_incompatible_positions_fail_geometry_without_stopping_other_core(tmp_path):
    manifest, config, output = fixture(tmp_path)
    path = manifest.parent / "assets/A1-positions.json"
    positions = read_json(path)
    positions["coordinate_space"] = "stage_microns"
    write_json(path, positions)
    summary = run_pipeline(manifest, output, config)
    rows = {(r["unit"], r["stage"]): r for r in summary["stage_results"]}
    assert rows["demo-A1", "geometry"]["execution"] == "error"
    assert rows["demo-A1", "mosaic"]["execution"] == "blocked"
    assert rows["demo-B3", "mosaic"]["execution"] == "complete"


def test_whole_corrupt_database_recovers_all_valid_receipts(tmp_path):
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)
    (path,) = (manifest.parent / "cache/state").glob("*/state.sqlite3")
    payload = b"entire checkpoint database corrupted"
    path.write_bytes(payload)
    summary = run_pipeline(manifest, output, config)
    assert all(row["cached"] for row in summary["stage_results"])
    event = next(
        row for row in summary["cache_recovery_events"] if row["source"] == "sqlite-database"
    )
    assert (Path(event["quarantine"]) / "state.sqlite3").read_bytes() == payload
    assert summary["execution_errors"] == summary["blocked_stages"] == 0


def test_separate_selected_runs_accumulate_results_and_observation_dates(tmp_path):
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config, selected=["demo-A1"])
    initial = table(output)
    assert initial["demo-B3"]["execution"] == "not_run"
    summary = run_pipeline(manifest, output, config, selected=["demo-B3"])
    final = table(output)
    assert all(row["execution"] == "complete" for row in final.values())
    assert final["demo-A1"]["observation"] == "historical_not_reverified"
    assert final["demo-B3"]["observation"] == "current_attempt"
    assert initial["demo-A1"]["last_processed_utc"] == final["demo-A1"]["last_processed_utc"]
    assert {row["unit"] for row in summary["stage_results"]} == {"demo-B3"}
    saved = read_json(output / "reports/cohort-results.json")
    assert {row["unit"] for row in saved["stage_results"]} == {"demo-A1", "demo-B3", "demo-pair"}
    assert main(["report", "--run", str(output)]) == 0
    assert table(output) == final


@pytest.mark.parametrize("change", ["manifest", "positions", "model"])
def test_changed_unselected_inputs_make_previous_completion_stale(tmp_path, change):
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)
    if change == "manifest":
        data = read_json(manifest)
        data["cores"][1]["fovs"].reverse()
        write_json(manifest, data)
    elif change == "positions":
        path = manifest.parent / "assets/B3-positions.json"
        value = read_json(path)
        value["positions"][1]["x"] += 1
        write_json(path, value)
    else:
        path = manifest.parent / "assets/model.json"
        value = read_json(path)
        value["model_revision"] = "changed"
        write_json(path, value)
    run_pipeline(manifest, output, config, selected=["demo-A1"])
    final = table(output)
    assert final["demo-A1"]["execution"] == "complete"
    assert final["demo-B3"]["execution"] == "stale"
    assert final["demo-B3"]["observation"] == "stale"


def test_interruption_replaces_selected_completion_with_partial_but_keeps_peer_history(
    tmp_path, monkeypatch
):
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)

    def interrupted(ctx, out):
        raise KeyboardInterrupt("before tile QC")

    monkeypatch.setattr("tma_qc.stages.tile_qc.run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_pipeline(manifest, output, config, selected=["demo-A1"], resume=False)
    assert (
        "current processing attempt has not finished" in (output / "reports/index.html").read_text()
    )
    assert main(["report", "--run", str(output)]) == 0
    rows = table(output)
    assert rows["demo-A1"]["execution"] == "partial"
    assert rows["demo-B3"]["execution"] == "complete"
    assert rows["demo-B3"]["observation"] == "historical_not_reverified"


def test_low_capacity_blocks_one_correction_before_writing_fovs(tmp_path, monkeypatch):
    from tma_qc.storage import volume_info

    manifest, config, output = fixture(tmp_path)

    def capacity(path):
        info = volume_info(path)
        if "demo-A1" in Path(path).parts and "illumination" in Path(path).parts:
            info["free_bytes"] = 0
        return info

    monkeypatch.setattr("tma_qc.storage.volume_info", capacity)
    summary = run_pipeline(manifest, output, config)
    rows = {(r["unit"], r["stage"]): r for r in summary["stage_results"]}
    assert rows["demo-A1", "illumination"]["execution"] == "blocked"
    assert "Insufficient storage" in rows["demo-A1", "illumination"]["findings"][0]
    assert rows["demo-B3", "mosaic"]["execution"] == "complete"
    assert not list(
        (output / "acquisitions/SYNTHETIC-original/A1/AF/demo-A1/illumination").rglob("FOV_*.tif")
    )


def test_malformed_historical_cohort_completion_is_stale_without_stopping_report(tmp_path):
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)
    path = output / "cohort/demo-B3.json"
    data = read_json(path)
    data["processing_finished"] = "false"
    write_json(path, data)
    run_pipeline(manifest, output, config, selected=["demo-A1"])
    assert table(output)["demo-B3"]["execution"] == "stale"
    assert table(output)["demo-A1"]["execution"] == "complete"
