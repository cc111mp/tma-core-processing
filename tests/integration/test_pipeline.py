from html.parser import HTMLParser
from pathlib import Path

import numpy as np

from tma_qc.cli import main
from tma_qc.demo import create_demo
from tma_qc.io.images import read_image, write_tiff
from tma_qc.pipeline import run_pipeline
from tma_qc.provenance import read_json, write_json


def fixture(tmp_path):
    manifest, config = create_demo(tmp_path / "demo")
    output = tmp_path / "results"
    return manifest, config, output


def rows(summary):
    return {(r["unit"], r["stage"]): r for r in summary["stage_results"]}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.links.extend(v for k, v in attrs if k in {"href", "src"})


def test_complete_pipeline_resume_and_offline_report_links(tmp_path):
    manifest, config, output = fixture(tmp_path)
    first = run_pipeline(manifest, output, config)
    assert first["execution_errors"] == first["blocked_stages"] == 0
    assert not first["automatic_image_quality_release"]
    mapping = rows(first)
    assert mapping["demo-A1", "index"]["qc"] == "pass"
    assert mapping["demo-A1", "tissue"]["metrics"]["blurred_tissue_percentage"] is None
    assert mapping["demo-pair", "registration_qc"]["qc"] == "review"
    assert mapping["demo-pair", "registration_qc"]["metrics"]["median_error_native_af_pixels"] == 0
    second = run_pipeline(manifest, output, config)
    assert all(r["cached"] for r in second["stage_results"])
    for page in output.rglob("*.html"):
        parser = Links()
        parser.feed(page.read_text())
        for link in parser.links:
            assert (page.parent / link).resolve().is_file(), (page, link)


def test_qc_changes_do_not_recompute_images_or_use_landmarks_in_fitting(tmp_path):
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)
    landmarks = manifest.parent / "assets/landmarks.json"
    content = read_json(landmarks)
    content["points"][0]["af"][0] += 100
    write_json(landmarks, content)
    config.write_text(config.read_text() + "\n[qc]\nlandmark_tolerance_pixels = 2.0\n")
    result = rows(run_pipeline(manifest, output, config))
    assert result["demo-A1", "mosaic"]["cached"]
    assert result["demo-pair", "registration"]["cached"]
    assert not result["demo-pair", "registration_qc"]["cached"]
    assert (
        result["demo-pair", "registration_qc"]["metrics"]["maximum_error_native_af_pixels"] == 100
    )


def test_corrupt_raw_core_is_isolated_and_cannot_reuse_cached_acceptance(tmp_path):
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)
    fov = manifest.parent / "inputs/B3/FOV_1.tif"
    fov.write_bytes(b"damaged transfer")
    result = run_pipeline(manifest, output, config)
    mapping = rows(result)
    assert mapping["demo-A1", "mosaic"]["execution"] == "complete"
    assert mapping["demo-B3", "index"]["execution"] == "error"
    assert mapping["demo-B3", "mosaic"]["execution"] == "blocked"
    assert "Source SHA256 mismatch" in mapping["demo-B3", "index"]["findings"][0]
    assert (output / "reports/index.html").exists()


def test_missing_geometry_blocks_mosaic_without_inventing_positions(tmp_path):
    manifest, config, output = fixture(tmp_path)
    data = read_json(manifest)
    data["cores"][0]["positions"] = None
    write_json(manifest, data)
    result = rows(run_pipeline(manifest, output, config))
    assert result["demo-A1", "geometry"]["execution"] == "blocked"
    assert result["demo-A1", "mosaic"]["execution"] == "blocked"
    assert result["demo-A1", "illumination"]["execution"] == "complete"
    assert result["demo-B3", "mosaic"]["execution"] == "complete"


def test_corrupt_cached_corrected_image_is_regenerated(tmp_path):
    manifest, config, output = fixture(tmp_path)
    first = rows(run_pipeline(manifest, output, config))
    stage = first["demo-A1", "illumination"]
    frames = output / stage["outputs"]["frames"]["path"]
    index = read_json(frames)
    image_path = frames.parent / index["1"]
    original = read_image(image_path).copy()
    write_tiff(image_path, np.zeros_like(original))
    second = rows(run_pipeline(manifest, output, config))
    assert not second["demo-A1", "illumination"]["cached"]
    assert second["demo-A1", "geometry"]["cached"]
    np.testing.assert_array_equal(read_image(image_path), original)


def test_cli_smoke_and_resume_qc_uses_saved_profile(tmp_path):
    assert main(["demo", "--out", str(tmp_path / "demo")]) == 0
    root = tmp_path / "demo/results"
    assert main(["report", "--run", str(root)]) == 0
    assert main(["qc", "--run", str(root)]) == 0


def test_interrupted_attempt_keeps_receipts_and_resumes(tmp_path, monkeypatch):
    from tma_qc.stages import mosaic

    manifest, config, output = fixture(tmp_path)
    original = mosaic.run

    def interrupted(ctx, out):
        raise KeyboardInterrupt("simulated interruption")

    monkeypatch.setattr(mosaic, "run", interrupted)
    import pytest

    with pytest.raises(KeyboardInterrupt):
        run_pipeline(manifest, output, config)
    partial = read_json(output / "stage-results.json")
    assert not partial["processing_attempt_finished"]
    assert partial["stage_results"]
    monkeypatch.setattr(mosaic, "run", original)
    result = run_pipeline(manifest, output, config)
    assert result["processing_attempt_finished"]
    assert rows(result)["demo-A1", "illumination"]["cached"]
    assert result["execution_errors"] == 0


def test_deleted_local_database_recovers_from_artifact_receipts(tmp_path):
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)
    for p in (manifest.parent / "cache/state").rglob("state.sqlite3*"):
        p.unlink()
    result = run_pipeline(manifest, output, config)
    assert all(r["cached"] for r in result["stage_results"])


def test_saved_manifest_replays_after_source_manifest_is_edited(tmp_path):
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)
    record = read_json(output / "run.json")
    snapshot = Path(record["manifest_path"])
    assert snapshot.is_file()
    assert snapshot.parent == output / "manifests"
    manifest.write_text('{"modified": true}')
    assert main(["qc", "--run", str(output)]) == 0
    resumed = read_json(output / "stage-results.json")
    assert all(r["cached"] for r in resumed["stage_results"])
