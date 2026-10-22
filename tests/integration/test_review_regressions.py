import csv
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import tifffile

from tma_qc.demo import create_demo
from tma_qc.io.images import read_image
from tma_qc.pipeline import run_pipeline
from tma_qc.provenance import read_json, write_json


def fixture(tmp_path):
    manifest, config = create_demo(tmp_path / "demo")
    return manifest, config, tmp_path / "results"


def rows(summary):
    return {(r["unit"], r["stage"]): r for r in summary["stage_results"]}


def test_changed_fov_order_invalidates_mist_and_matches_a_clean_run(tmp_path, monkeypatch):
    manifest, config, output = fixture(tmp_path)
    data = read_json(manifest)
    data["pairs"] = []
    core = data["cores"][0]
    core["positions"] = None
    core["metadata"].update(
        grid_reviewed=True,
        grid={
            "rows": 2,
            "columns": 2,
            "numbering_pattern": "HORIZONTALCOMBING",
            "origin": "UL",
            "horizontal_overlap": 40,
            "vertical_overlap": 40,
        },
    )
    write_json(manifest, data)
    config.write_text(
        config.read_text()
        + '\n[geometry]\nbackend="mist"\ndownsample=1\nmist_classpath="stub.jar"\n'
    )

    def fake_java(command, **kwargs):
        destination = Path(command[command.index("--outputPath") + 1])
        (destination / "img-global-positions-1.txt").write_text(
            "\n".join(
                f"file: FOV_{n:05d}.tif; corr: 1.0; position: ({x}, {y}); grid: ({(n - 1) % 2}, {(n - 1) // 2});"
                for n, (x, y) in enumerate([(0, 0), (96, 0), (0, 96), (96, 96)], 1)
            )
        )

    monkeypatch.setattr("tma_qc.backends.mist.subprocess.run", fake_java)
    first = rows(run_pipeline(manifest, output, config, selected=["demo-A1"]))
    core["fovs"].reverse()
    write_json(manifest, data)
    second = rows(run_pipeline(manifest, output, config, selected=["demo-A1"]))
    fresh_root = tmp_path / "fresh"
    fresh = rows(run_pipeline(manifest, fresh_root, config, selected=["demo-A1"]))
    for stage in ["index", "geometry", "mosaic"]:
        assert not second["demo-A1", stage]["cached"]
        assert first["demo-A1", stage]["fingerprint"] != second["demo-A1", stage]["fingerprint"]
    key = "demo-A1", "geometry"
    assert read_json(output / second[key]["outputs"]["positions"]["path"]) == read_json(
        fresh_root / fresh[key]["outputs"]["positions"]["path"]
    )
    key = "demo-A1", "mosaic"
    np.testing.assert_array_equal(
        read_image(output / second[key]["outputs"]["mosaic"]["path"]),
        read_image(fresh_root / fresh[key]["outputs"]["mosaic"]["path"]),
    )


def test_blocked_cores_remain_in_batch_report_and_coverage(tmp_path):
    manifest, config, output = fixture(tmp_path)
    data = read_json(manifest)
    data["cores"].append(
        {
            **data["cores"][0],
            "uid": "blocked-A2",
            "core": "A2",
            "fovs": [],
            "blocked_reasons": ["Ambiguous autofocus Z selection"],
        }
    )
    write_json(manifest, data)
    result = run_pipeline(manifest, output, config)
    mapping = rows(result)
    assert mapping["blocked-A2", "index"]["execution"] == "blocked"
    assert mapping["demo-B3", "mosaic"]["execution"] == "complete"
    assert result["execution_errors"] == 0
    with (output / "reports/inventory-coverage.csv").open(encoding="utf-8-sig") as handle:
        coverage = {r["uid"]: r for r in csv.DictReader(handle)}
    assert len(coverage) == 3
    assert (
        coverage["blocked-A2"]["input_eligibility"]
        == coverage["blocked-A2"]["execution"]
        == "blocked"
    )
    assert "Z selection" in coverage["blocked-A2"]["eligibility_reasons"]
    assert read_json(output / "reports/coverage.json")["blocked_at_input"] == 1


@pytest.mark.parametrize(
    "damage",
    [
        "sqlite-json",
        "sqlite-nonfinite",
        "receipt-json",
        "foreign-identity",
        "empty-outputs",
        "foreign-output-path",
    ],
)
def test_damaged_cache_metadata_is_recovered_without_stopping_other_cores(tmp_path, damage):
    manifest, config, output = fixture(tmp_path)
    first = rows(run_pipeline(manifest, output, config))
    geometry = first["demo-A1", "geometry"]
    receipt = (output / geometry["outputs"]["positions"]["path"]).parent / "stage.json"
    databases = list((manifest.parent / "cache/state").rglob("state.sqlite3"))
    if damage.startswith("sqlite"):
        with sqlite3.connect(databases[0]) as db:
            if damage == "sqlite-json":
                db.execute("UPDATE stages SET result='{' WHERE unit='demo-A1' AND stage='geometry'")
            else:
                import json

                record = dict(geometry)
                record["metrics"] = {"corrupt": float("nan")}
                db.execute(
                    "UPDATE stages SET result=? WHERE unit='demo-A1' AND stage='geometry'",
                    (json.dumps(record),),
                )
    else:
        if damage == "receipt-json":
            receipt.write_text('{"partial":')
        else:
            record = read_json(receipt)
            if damage == "foreign-identity":
                record["unit"] = "wrong-core"
            elif damage == "empty-outputs":
                record["outputs"] = {}
            else:
                record["outputs"] = first["demo-B3", "geometry"]["outputs"]
            write_json(receipt, record)
        for database in databases:
            database.unlink()
    second = run_pipeline(manifest, output, config)
    mapping = rows(second)
    assert second["processing_attempt_finished"] and second["execution_errors"] == 0
    assert second["cache_recovery_events"]
    assert mapping["demo-A1", "geometry"]["unit"] == "demo-A1"
    assert mapping["demo-A1", "geometry"]["cached"] is damage.startswith("sqlite")
    assert mapping["demo-B3", "mosaic"]["cached"]
    with sqlite3.connect(databases[0]) as db:
        records = db.execute(
            "SELECT result FROM stages WHERE unit='demo-A1' AND stage='geometry'"
        ).fetchall()
    assert len(records) == 1 and '"unit": "demo-A1"' in records[0][0]


def test_interrupted_attempt_does_not_leave_a_current_looking_old_report(tmp_path, monkeypatch):
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)

    def interrupt(ctx, out):
        raise KeyboardInterrupt("interrupt before a new report can finish")

    monkeypatch.setattr("tma_qc.stages.tile_qc.run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_pipeline(manifest, output, config, resume=False)
    assert not read_json(output / "stage-results.json")["processing_attempt_finished"]
    assert (
        "current processing attempt has not finished" in (output / "reports/index.html").read_text()
    )
    assert (
        "Earlier reports do not describe this attempt"
        in (output / "reports/index.html").read_text()
    )


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -np.inf])
def test_nonfinite_he_outside_preview_sample_blocks_registration(tmp_path, bad_value):
    manifest, config, output = fixture(tmp_path)
    data = read_json(manifest)
    path = manifest.parent / "assets/he.tif"
    he = np.full((256, 256), 100, np.float32)
    he[51, 51] = bad_value
    tifffile.imwrite(path, he, compression="deflate")
    from tma_qc.provenance import sha256

    data["pairs"][0]["he_sha256"] = sha256(path)
    write_json(manifest, data)
    config.write_text(config.read_text() + "\n[report]\npreview_size=128\n")
    result = rows(run_pipeline(manifest, output, config))
    assert result["demo-pair", "he_qc"]["execution"] == "error"
    assert "Nonfinite H&E" in result["demo-pair", "he_qc"]["findings"][0]
    assert result["demo-pair", "registration"]["execution"] == "blocked"
    assert result["demo-B3", "mosaic"]["execution"] == "complete"


@pytest.mark.parametrize("axis", [0, 1])
def test_large_compressed_he_is_mapped_and_warped_from_native_roi(tmp_path, monkeypatch, axis):
    manifest, config, output = fixture(tmp_path)
    data = read_json(manifest)
    shape = (32768, 64, 3) if axis == 0 else (64, 32768, 3)
    he = np.full(shape, 200, np.uint8)
    if axis == 0:
        he[32700:] = [120, 60, 30]
    else:
        he[:, 32700:] = [120, 60, 30]
    path = manifest.parent / "assets/he.tif"
    tifffile.imwrite(path, he, compression="deflate", photometric="rgb", rowsperstrip=8)
    from tma_qc.provenance import sha256

    data["pairs"][0]["he_sha256"] = sha256(path)
    write_json(manifest, data)
    # Matrix axes are XY, while image axes above are YX.
    transform = np.eye(3)
    transform[1 - axis, 2] = -32700
    write_json(
        manifest.parent / "assets/transform.json",
        {
            "matrix": transform.tolist(),
            "direction": "he_to_af",
            "coordinate_space": "native_pixels",
        },
    )
    original = tifffile.TiffPageSeries.asarray
    decode_paths = []

    def observed(series, **kwargs):
        assert "out" in kwargs, "Full H&E decoding must use local file-backed output"
        decode_paths.append(Path(kwargs["out"]))
        return original(series, **kwargs)

    monkeypatch.setattr(tifffile.TiffPageSeries, "asarray", observed)
    result = rows(run_pipeline(manifest, output, config, selected=["demo-A1"]))
    assert result["demo-pair", "registration"]["execution"] == "complete"
    aligned = read_image(
        output / result["demo-pair", "registration"]["outputs"]["aligned_he"]["path"], rgb=True
    )
    expected = np.full((256, 256, 3), 255, np.uint8)
    expected[: 68 if axis == 0 else 64, : 64 if axis == 0 else 68] = [120, 60, 30]
    np.testing.assert_array_equal(aligned, expected)
    assert len(decode_paths) == 2
    assert all(not p.exists() for p in decode_paths)


def test_nonfinite_stage_metrics_fail_one_core_without_aborting_the_batch(tmp_path, monkeypatch):
    from tma_qc.stages import tile_qc

    manifest, config, output = fixture(tmp_path)
    original = tile_qc.run

    def bad_measurement(ctx, out):
        payload = original(ctx, out)
        if ctx.core.uid == "demo-A1":
            payload.metrics["bad_value"] = float("nan")
        return payload

    monkeypatch.setattr(tile_qc, "run", bad_measurement)
    result = run_pipeline(manifest, output, config)
    mapping = rows(result)
    assert result["processing_attempt_finished"]
    assert mapping["demo-A1", "tile_qc"]["execution"] == "error"
    assert mapping["demo-B3", "mosaic"]["execution"] == "complete"
