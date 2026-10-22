"""CLI, Python and readiness share typed method/settings choices before writes."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tests.integration.test_pipeline import fixture
from tma_qc import api
from tma_qc.cli import main
from tma_qc.io.images import read_image
from tma_qc.provenance import read_json, write_json
from tma_qc.settings import resolve_settings


def test_python_api_forwards_method_and_parameter_choices_without_altering_inputs(monkeypatch):
    from tma_qc import pipeline

    calls = []

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return {"execution_errors": 0, "blocked_stages": 0}

    monkeypatch.setattr(pipeline, "run_pipeline", record)
    parameters = {"registration": {"matching": {"max_dimension": 512}}}
    result = api.run(
        "manifest.json",
        "output",
        "settings.toml",
        parameters=parameters,
        registration_method="geometry_sift",
        registration_transform="affine",
        registration_refinement="none",
        geometry_method="provided",
        illumination_method="none",
        selected=["core01"],
        resume=False,
    )
    assert result["execution_errors"] == 0
    args, options = calls[0]
    assert args == ("manifest.json", "output", "settings.toml")
    assert options == {
        "resume": False,
        "selected": ["core01"],
        "parameters": parameters,
        "registration_method": "geometry_sift",
        "registration_transform": "affine",
        "registration_refinement": "none",
        "geometry_method": "provided",
        "illumination_method": "none",
    }
    assert parameters == {"registration": {"matching": {"max_dimension": 512}}}


def test_cli_passes_typed_choices_with_explicit_selector_precedence(tmp_path, monkeypatch):
    from tma_qc import pipeline

    seen = []

    def record(manifest, output, config, **options):
        seen.append(
            resolve_settings(
                config,
                **{
                    key: value
                    for key, value in options.items()
                    if key not in {"resume", "selected"}
                },
            )
        )
        return {"execution_errors": 0, "blocked_stages": 0}

    monkeypatch.setattr(pipeline, "run_pipeline", record)
    output = tmp_path / "output"
    assert (
        main(
            [
                "run",
                "--manifest",
                "unused.json",
                "--out",
                str(output),
                "--registration-method",
                "geometry_sift",
                "--registration-transform",
                "affine",
                "--set",
                'registration.matching.transform="similarity"',
                "--set",
                "runtime.threads=3",
                "--illumination-method",
                "none",
            ]
        )
        == 0
    )
    assert seen[0]["registration"]["matching"]["transform"] == "affine"
    assert seen[0]["runtime"]["threads"] == 3
    assert seen[0]["illumination"]["mode"] == "none"
    assert not output.exists()


def test_cli_settings_reach_saved_parameters_and_native_processing(tmp_path):
    manifest, config, output = fixture(tmp_path)
    assert (
        main(
            [
                "run",
                "--manifest",
                str(manifest),
                "--config",
                str(config),
                "--out",
                str(output),
                "--core",
                "demo-A1",
                "--geometry-method",
                "provided",
                "--illumination-method",
                "none",
                "--registration-method",
                "provided",
                "--set",
                "runtime.threads=1",
                "--set",
                "mosaic.chunk_rows=7",
            ]
        )
        == 0
    )
    parameters = read_json(output / "run.json")["parameters"]
    assert parameters["runtime"]["threads"] == 1
    assert parameters["mosaic"]["chunk_rows"] == 7
    assert parameters["illumination"]["mode"] == "none"
    stages = read_json(output / "stage-results.json")["stage_results"]
    mosaic = next(row for row in stages if row["stage"] == "mosaic")
    raw = read_image(output / mosaic["outputs"]["raw"]["path"])
    selected = read_image(output / mosaic["outputs"]["mosaic"]["path"])
    assert np.array_equal(raw, selected)


@pytest.mark.parametrize("command", ["run", "plan"])
def test_invalid_override_cannot_create_output_or_cache(tmp_path, command, capsys):
    manifest, config, output = fixture(tmp_path)
    assert (
        main(
            [
                command,
                "--manifest",
                str(manifest),
                "--config",
                str(config),
                "--out",
                str(output),
                "--set",
                "runtime.threds=2",
            ]
        )
        == 1
    )
    assert "threds" in capsys.readouterr().err
    assert not output.exists()
    assert not (manifest.parent / "cache").exists()


def test_cli_qc_applies_file_then_typed_overrides_then_selectors_to_saved_base(
    tmp_path, monkeypatch
):
    from tma_qc import pipeline
    from tma_qc.config import load_config

    manifest, config, output = fixture(tmp_path)
    output.mkdir()
    base = load_config(config)
    base["runtime"]["threads"] = 5
    write_json(
        output / "run.json",
        {"manifest_path": str(manifest), "parameters": base, "selected_cores": ["demo-A1"]},
    )
    overrides = tmp_path / "qc.toml"
    overrides.write_text('[runtime]\nthreads=4\n[illumination]\nmode="cidre"\n')
    seen = []

    class Runner:
        def __init__(self, assigned, root, settings, resume):
            seen.append(settings)
            self.state = self
            assert resume is True and root == output

        def run(self, selected):
            assert selected == ["demo-A1"]
            return {"execution_errors": 0, "blocked_stages": 0}

        def close(self):
            pass

    monkeypatch.setattr(pipeline, "Runner", Runner)
    assert (
        main(
            [
                "qc",
                "--run",
                str(output),
                "--config",
                str(overrides),
                "--set",
                "runtime.threads=3",
                "--illumination-method",
                "none",
            ]
        )
        == 0
    )
    assert seen[0]["runtime"]["threads"] == 3
    assert seen[0]["illumination"]["mode"] == "none"
    assert read_json(output / "run.json")["parameters"]["runtime"]["threads"] == 5


@pytest.mark.parametrize("arguments", [["run", "--help"], ["methods"], ["methods", "--json"]])
def test_method_inspection_and_help_remain_usable_without_site_packages(arguments):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-S", "-m", "tma_qc", *arguments], cwd=root, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    if arguments == ["methods", "--json"]:
        metadata = json.loads(result.stdout)
        assert metadata["methods"]["registration_method"]["default"] == "provided"
        assert metadata["core_default_method"] == "geometry_sift"
    else:
        assert "geometry_sift" in result.stdout


def test_python_settings_inspection_remains_stdlib_only():
    root = Path(__file__).resolve().parents[2]
    script = (
        "import sys; from tma_qc.api import settings; "
        "value=settings(registration_method='geometry_sift',registration_transform='affine'); "
        "assert value['registration']['matching']['transform']=='affine'; "
        "assert not any(name in sys.modules for name in ['numpy','cv2','tifffile','scipy','PIL'])"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", script], cwd=root, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
