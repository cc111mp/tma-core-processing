"""Public inspection stays read-only, and summaries distinguish processing from QC."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tma_qc.cli import main


def test_settings_without_site_packages_resolves_precedence_without_creating_cache(tmp_path):
    root = Path(__file__).resolve().parents[2]
    cache = tmp_path / "unused-cache"
    config = tmp_path / "settings.toml"
    source = (
        f"[runtime]\nthreads = 5\ncache_root = {json.dumps(str(cache))}\n"
        '[illumination]\nmode = "cidre"\n'
        '[registration]\nbackend = "provided"\n'
    )
    config.write_text(source)
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "tma_qc",
            "settings",
            "--config",
            str(config),
            "--set",
            "runtime.threads=3",
            "--set",
            'registration.matching.transform="similarity"',
            "--geometry-method",
            "mist",
            "--illumination-method",
            "none",
            "--registration-method",
            "geometry_roma",
            "--registration-transform",
            "affine",
            "--registration-refinement",
            "bspline_mi",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    effective = json.loads(result.stdout)
    assert effective["runtime"]["threads"] == 3
    assert effective["geometry"]["backend"] == "mist"
    assert effective["illumination"]["mode"] == "none"
    assert effective["registration"]["backend"] == "core"
    assert effective["registration"]["core_method"] == "geometry_roma"
    assert effective["registration"]["matching"]["transform"] == "affine"
    assert effective["registration"]["refinement"]["backend"] == "bspline_mi"
    assert not cache.exists()
    assert config.read_text() == source


@pytest.mark.parametrize(
    "assignment", ["runtime.threds=2", "runtime.threads=0", 'runtime.threads="two"']
)
def test_settings_rejects_invalid_parameters_with_a_plain_cli_error(assignment, capsys):
    assert main(["settings", "--set", assignment]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err.startswith("tma-qc: ")
    assert "Traceback" not in captured.err


def test_plan_uses_the_public_api_and_preserves_structured_readiness_output(monkeypatch, capsys):
    from tma_qc import api

    calls = []
    readiness = {"ready_for_execution": False, "issues": [{"reason": "Geometry required"}]}

    def plan(*args, **kwargs):
        calls.append((args, kwargs))
        return readiness

    monkeypatch.setattr(api, "plan", plan)
    assert (
        main(
            [
                "plan",
                "--manifest",
                "inputs.json",
                "--config",
                "af.toml",
                "--out",
                "run_001",
                "--core",
                "core-A1",
                "--set",
                "runtime.threads=1",
                "--illumination-method",
                "none",
            ]
        )
        == 2
    )
    assert calls[0][0] == ("inputs.json", "run_001", "af.toml")
    assert calls[0][1]["selected"] == ["core-A1"]
    assert calls[0][1]["parameters"] == {"runtime": {"threads": 1}}
    assert calls[0][1]["illumination_method"] == "none"
    assert json.loads(capsys.readouterr().out) == readiness


@pytest.mark.parametrize("errors,blocked,expected", [(0, 0, 0), (0, 2, 2), (1, 2, 1)])
def test_run_summary_keeps_execution_exit_codes_and_quality_separate(
    tmp_path, monkeypatch, capsys, errors, blocked, expected
):
    from tma_qc import pipeline

    summary = {
        "execution_errors": errors,
        "blocked_stages": blocked,
        "stage_results": [
            {"stage": "index", "execution": "complete", "qc": "pass"},
            {"stage": "tile_qc", "execution": "complete", "qc": "review"},
        ],
    }
    monkeypatch.setattr(pipeline, "run_pipeline", lambda *args, **kwargs: summary)
    output = tmp_path / "run"
    assert main(["run", "--manifest", "unused.json", "--out", str(output)]) == expected
    text = capsys.readouterr().out
    error_label = "error" if errors == 1 else "errors"
    assert f"Processing stages: 2 complete, {errors} {error_label}, {blocked} blocked." in text
    assert "Image quality: review required." in text
    assert f"Report: {output / 'reports/index.html'}" in text
    assert not output.exists()


def test_run_summary_does_not_invent_quality_when_stage_records_are_unavailable(
    tmp_path, monkeypatch, capsys
):
    from tma_qc import pipeline

    monkeypatch.setattr(
        pipeline,
        "run_pipeline",
        lambda *args, **kwargs: {"execution_errors": 0, "blocked_stages": 0},
    )
    assert main(["run", "--manifest", "unused.json", "--out", str(tmp_path / "run")]) == 0
    text = capsys.readouterr().out
    assert "Processing stages: 0 errors, 0 blocked." in text
    assert "Image quality: not assessed." in text


def test_qc_summary_reports_saved_run_and_ignores_failed_measurements_as_quality(
    tmp_path, monkeypatch, capsys
):
    from tma_qc import commands, pipeline

    class Runner:
        def __init__(self, manifest, root, config, resume):
            self.state = self

        def run(self, selected):
            return {
                "execution_errors": 1,
                "blocked_stages": 0,
                "stage_results": [{"stage": "tile_qc", "execution": "error", "qc": "fail"}],
            }

        def close(self):
            pass

    output = tmp_path / "run"
    monkeypatch.setattr(commands, "_saved_run", lambda args: (output, {"parameters": {}}, object()))
    monkeypatch.setattr(pipeline, "Runner", Runner)
    assert main(["qc", "--run", str(output)]) == 1
    text = capsys.readouterr().out
    assert "Processing stages: 0 complete, 1 error, 0 blocked." in text
    assert "Image quality: not assessed." in text
    assert f"Report: {output / 'reports/index.html'}" in text
