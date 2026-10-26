"""The public readiness API preserves CLI checks without creating a run."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.test_pipeline import fixture
from tma_qc import api
from tma_qc.io.manifest import Manifest
from tma_qc.preflight import check
from tma_qc.provenance import read_json, write_json


def test_plan_matches_operational_checks_without_creating_outputs(tmp_path):
    manifest, config, output = fixture(tmp_path)
    cache = tmp_path / "planned-cache"
    parameters = {"runtime": {"cache_root": str(cache)}}
    before = set(tmp_path.rglob("*"))

    result = api.plan(manifest, output, config, selected=["demo-A1"], parameters=parameters)
    expected = check(
        Manifest(manifest),
        api.settings(config, parameters=parameters),
        output,
        ["demo-A1"],
    )

    for key in (
        "dataset",
        "cores",
        "pairs",
        "ready_for_execution",
        "issues",
        "warnings",
        "raw_files_hash_and_header_verified",
        "scope",
        "automatic_image_quality_release",
    ):
        assert result[key] == expected[key]
    assert result["ready_for_execution"]
    assert result["cores"] == result["pairs"] == 1
    assert result["raw_files_hash_and_header_verified"] == 4
    assert not result["automatic_image_quality_release"]
    assert set(tmp_path.rglob("*")) == before
    assert not output.exists() and not cache.exists()


def test_plan_checks_only_selected_cores_and_explicit_method_takes_precedence(tmp_path):
    manifest, config, output = fixture(tmp_path)
    data = read_json(manifest)
    for name in ("flatfield", "darkfield", "model_metadata"):
        data["groups"][0][name] = None
    write_json(manifest, data)
    (manifest.parent / "inputs/B3/FOV_1.tif").write_bytes(b"changed source")

    result = api.plan(
        manifest,
        output,
        config,
        selected=["demo-A1"],
        parameters={"illumination": {"mode": "cidre"}},
        illumination_method="none",
    )
    assert result["ready_for_execution"]
    assert result["raw_files_hash_and_header_verified"] == 4
    all_cores = api.plan(manifest, output, config, illumination_method="none")
    assert not all_cores["ready_for_execution"]
    assert any(issue["scope"] == "demo-B3" for issue in all_cores["issues"])
    assert not output.exists()


def test_plan_without_output_reports_incomplete_readiness(tmp_path):
    manifest, config, _ = fixture(tmp_path)
    result = api.plan(manifest, config=config)
    assert not result["ready_for_execution"]
    assert any(
        issue["scope"] == "output" and issue["check"] == "location" for issue in result["issues"]
    )
    assert not (manifest.parent / "cache").exists()


def test_plan_rejects_unknown_core_uids_without_creating_outputs(tmp_path):
    manifest, config, output = fixture(tmp_path)
    with pytest.raises(ValueError, match="Unknown selected core identifiers"):
        api.plan(manifest, output, config, selected=["missing-core"])
    assert not output.exists()


def test_plan_validates_settings_before_opening_a_manifest(tmp_path):
    with pytest.raises(ValueError, match="Unknown.*runtime.*settings"):
        api.plan(tmp_path / "missing.json", parameters={"runtime": {"unknown_option": 1}})


def test_importing_plan_and_inspecting_settings_need_no_image_libraries():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; from tma_qc.api import plan, settings; "
            "assert callable(plan); assert settings()['profile']['dtype'] == 'uint16'; "
            "assert not {'numpy', 'cv2', 'tifffile', 'PIL', 'scipy'} & sys.modules.keys()",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
