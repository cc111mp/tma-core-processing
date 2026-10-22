"""Deliver signal and coverage evidence through saved stages and core reports."""

import numpy as np

from tests.integration.test_pipeline import fixture, rows
from tma_qc.io.images import read_image, write_tiff
from tma_qc.pipeline import run_pipeline
from tma_qc.provenance import read_json
from tma_qc.reporting.core import illumination_summary


def test_legacy_saved_metrics_still_render_without_signal_fields():
    html = illumination_summary(
        [
            {
                "stage": "illumination_qc",
                "execution": "complete",
                "metrics": {"raw_supported_pairs": 3, "excluded_pairs": 4},
            }
        ]
    )
    assert "3 / 7" in html
    assert "Excluded overlap pairs are unknown" in html
    assert "Per-FOV signal measurements" not in html


def test_illumination_coverage_gain_and_signal_measurements_reach_the_core_report(tmp_path):
    manifest, config, output = fixture(tmp_path)
    stages = rows(run_pipeline(manifest, output, config))
    stage = stages["demo-A1", "illumination_qc"]
    report = read_json(output / stage["outputs"]["overlaps"]["path"])
    metrics = stage["metrics"]
    assert metrics["candidate_pairs"] == metrics["assessed_pairs"] + metrics["excluded_pairs"]
    assert (
        metrics["model_gain_p01_p50_p99"]
        == stages["demo-A1", "illumination"]["metrics"]["gain_p01_p50_p99"]
    )
    assert metrics["signal_diagnostics"] == report["signal_diagnostics"]
    assert metrics["signal_diagnostics"]["assessed_fovs"] == 4
    assert metrics["signal_diagnostics"]["collapsed_fov_ids"] == []
    html = (output / "reports/cores/demo-A1.html").read_text()
    assert f"{metrics['assessed_pairs']} / {metrics['candidate_pairs']}" in html
    assert "Model gain p01 / p50 / p99" in html
    assert "Per-FOV signal measurements" in html
    assert "biological intensity preservation remains unvalidated" in html


def test_collapsed_signal_remains_available_with_a_visible_review_flag(tmp_path, monkeypatch):
    from tma_qc.stages import illumination

    original = illumination.run

    def collapsed(ctx, out):
        payload = original(ctx, out)
        for value in read_json(payload.outputs["frames"]).values():
            path = out / value
            write_tiff(path, np.zeros_like(read_image(path)))
        return payload

    monkeypatch.setattr(illumination, "run", collapsed)
    manifest, config, output = fixture(tmp_path)
    stages = rows(run_pipeline(manifest, output, config))
    qc = stages["demo-A1", "illumination_qc"]
    assert qc["execution"] == "complete" and qc["qc"] == "review"
    assert qc["metrics"]["signal_diagnostics"]["collapsed_fov_ids"] == ["1", "2", "3", "4"]
    assert any("zero native dynamic range" in message for message in qc["findings"])
    assert stages["demo-A1", "mosaic"]["execution"] == "complete"
    html = (output / "reports/cores/demo-A1.html").read_text()
    assert "exactly constant corrected output from varying raw input in FOVs 1, 2, 3, 4" in html
    assert "Native corrected core TIFF" in html
