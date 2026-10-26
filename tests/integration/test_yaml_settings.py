"""YAML is another settings format, with the same execution and review contract."""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tma_qc import api
from tma_qc.cli import main
from tma_qc.config import load_config
from tma_qc.demo import create_demo
from tma_qc.io.images import read_image
from tma_qc.provenance import read_json, sha256
from tma_qc.schema import CORE_STAGES, PAIR_STAGES


def _equivalent_files(tmp_path):
    cache = tmp_path / "cache with spaces"
    toml = tmp_path / "settings.toml"
    toml.write_text(
        "[profile]\nshape = [160, 160]\ndtype = 'uint16'\n"
        "[runtime]\nthreads = 1\ntile_workers = 1\n"
        f"cache_root = {json.dumps(str(cache))}\n"
        "[tile_qc]\nwindow = 80\nstride = 80\n"
        "[mosaic]\nchunk_rows = 7\n"
        "[registration]\nexport_aligned_he = true\n"
        "[registration.matching]\nratio = 0.73\n",
        encoding="utf-8",
    )
    yaml = tmp_path / "settings.yaml"
    yaml.write_text(
        "profile:\n  shape: [160, 160]\n  dtype: uint16\n"
        "runtime:\n  threads: 1\n  tile_workers: 1\n"
        f"  cache_root: {json.dumps(str(cache))}\n"
        "tile_qc:\n  window: 80\n  stride: 80\n"
        "mosaic:\n  chunk_rows: 7\n"
        "registration:\n  export_aligned_he: true\n"
        "  matching:\n    ratio: 0.73\n",
        encoding="utf-8",
    )
    return toml, yaml, cache


@pytest.mark.parametrize("suffix", [".yaml", ".yml"])
def test_yaml_and_toml_resolve_identical_validated_settings(tmp_path, suffix):
    toml, yaml, cache = _equivalent_files(tmp_path)
    renamed = yaml.with_suffix(suffix)
    if renamed != yaml:
        yaml.rename(renamed)
    originals = {path: sha256(path) for path in (toml, renamed)}
    expected = api.settings(toml)
    assert api.settings(renamed) == expected == load_config(renamed)
    assert expected["profile"]["shape"] == [160, 160]
    assert expected["registration"]["export_aligned_he"] is True
    assert not cache.exists()
    assert all(sha256(path) == digest for path, digest in originals.items())


def test_cli_yaml_file_then_typed_parameters_then_method_selectors(tmp_path, capsys):
    toml, yaml, cache = _equivalent_files(tmp_path)
    supplied = {
        "runtime": {"threads": 3},
        "registration": {"matching": {"transform": "similarity"}},
    }
    expected = api.settings(
        toml,
        parameters=supplied,
        geometry_method="mist",
        illumination_method="none",
        registration_method="geometry_roma",
        registration_transform="affine",
        registration_refinement="bspline_mi",
    )
    assert (
        main(
            [
                "settings",
                "--config",
                str(yaml),
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
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert not captured.err
    assert json.loads(captured.out) == expected
    assert expected["runtime"]["threads"] == 3
    assert expected["registration"]["matching"]["transform"] == "affine"
    assert not cache.exists()
    assert supplied["registration"]["matching"]["transform"] == "similarity"


@pytest.mark.parametrize("configured", [False, True])
def test_resolved_cli_yaml_roundtrips_all_defaults_and_sparse_presets(tmp_path, capsys, configured):
    args = ["settings", "--format", "yaml"]
    source = None
    if configured:
        _, source, _ = _equivalent_files(tmp_path)
        args += ["--config", str(source)]
    assert main(args) == 0
    captured = capsys.readouterr()
    assert not captured.err
    dumped = tmp_path / "effective.yaml"
    dumped.write_text(captured.out, encoding="utf-8")
    resolved = load_config(dumped)
    assert resolved == api.settings(source)
    assert resolved["registration"]["refinement"] == {}
    assert resolved["registration"]["roma"] == {}
    if configured:
        assert not Path(resolved["runtime"]["cache_root"]).exists()


def test_yaml_settings_and_stage_inspection_do_not_import_image_runtimes_or_write(tmp_path):
    _, yaml, cache = _equivalent_files(tmp_path)
    root = Path(__file__).resolve().parents[2]
    script = r"""
import importlib.abc
import sys
from contextlib import redirect_stdout
from io import StringIO

class NoImaging(importlib.abc.MetaPathFinder):
    blocked = {'numpy', 'scipy', 'cv2', 'tifffile', 'PIL', 'torch', 'romatch', 'SimpleITK'}
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in self.blocked:
            raise AssertionError('Settings inspection imported optional image runtime: ' + fullname)

sys.meta_path.insert(0, NoImaging())
from tma_qc.cli import main
for command in [
    ['settings', '--config', sys.argv[1], '--format', 'yaml'],
    ['stages', '--json'],
    ['methods', '--json'],
]:
    with redirect_stdout(StringIO()) as stream:
        assert main(command) == 0
    assert stream.getvalue().strip()
assert not NoImaging.blocked.intersection(sys.modules)
"""
    before = {p.relative_to(tmp_path): sha256(p) for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run(
        [sys.executable, "-c", script, str(yaml)],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert not cache.exists()
    assert before == {
        p.relative_to(tmp_path): sha256(p) for p in tmp_path.rglob("*") if p.is_file()
    }


def test_stage_catalog_cli_and_api_share_metadata_and_return_independent_copies(capsys):
    expected = api.stages()
    assert main(["stages", "--json"]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    assert json.loads(captured.out) == expected
    assert [stage["name"] for stage in expected["af_stages"]] == list(CORE_STAGES)
    assert [stage["name"] for stage in expected["pair_stages"]] == list(PAIR_STAGES)
    expected["af_stages"][0]["config_sections"].append("foreign")
    assert "foreign" not in api.stages()["af_stages"][0]["config_sections"]


def test_yaml_and_toml_run_all_nine_af_and_three_pair_stages_with_identical_pixels(
    tmp_path, capsys
):
    manifest, _ = create_demo(tmp_path / "synthetic-study")
    toml, yaml, _ = _equivalent_files(tmp_path)
    source_files = [p for p in manifest.parent.rglob("*") if p.is_file()]
    source_hashes = {p: sha256(p) for p in [*source_files, toml, yaml]}
    toml_out, yaml_out = tmp_path / "toml-results", tmp_path / "yaml-results"

    assert api.plan(manifest, toml_out, toml, selected=["demo-A1"])["ready_for_execution"]
    assert (
        main(
            [
                "plan",
                "--manifest",
                str(manifest),
                "--config",
                str(yaml),
                "--out",
                str(yaml_out),
                "--core",
                "demo-A1",
            ]
        )
        == 0
    )
    assert not toml_out.exists() and not yaml_out.exists()
    capsys.readouterr()
    first = api.run(manifest, toml_out, toml, selected=["demo-A1"], resume=False)
    assert (
        main(
            [
                "run",
                "--manifest",
                str(manifest),
                "--config",
                str(yaml),
                "--out",
                str(yaml_out),
                "--core",
                "demo-A1",
            ]
        )
        == 0
    )
    second = read_json(yaml_out / "stage-results.json")
    expected = {
        *(("demo-A1", stage) for stage in CORE_STAGES),
        *(("demo-pair", stage) for stage in PAIR_STAGES),
    }
    first_rows = {(row["unit"], row["stage"]): row for row in first["stage_results"]}
    second_rows = {(row["unit"], row["stage"]): row for row in second["stage_results"]}
    assert set(first_rows) == set(second_rows) == expected
    assert first["execution_errors"] == first["blocked_stages"] == 0
    assert second["execution_errors"] == second["blocked_stages"] == 0
    assert not first["automatic_image_quality_release"]
    assert not second["automatic_image_quality_release"]
    assert (
        read_json(toml_out / "run.json")["parameters"]
        == read_json(yaml_out / "run.json")["parameters"]
    )
    contracts = {
        row["name"]: row
        for rows in (api.stages()["af_stages"], api.stages()["pair_stages"])
        for row in rows
    }
    image_count = 0
    for key, left in first_rows.items():
        right = second_rows[key]
        assert left["execution"] == right["execution"] == "complete"
        assert left["qc"] == right["qc"]
        assert left["provenance"]["parameters"] == right["provenance"]["parameters"]
        contract = contracts[left["stage"]]
        assert set(left["provenance"]["parameters"]) == set(contract["config_sections"])
        assert set(left["provenance"]["dependencies"]) == set(contract["dependencies"])
        assert left["provenance"]["inputs"] == right["provenance"]["inputs"]
        assert set(left["outputs"]) == set(right["outputs"])
        for label, artifact in left["outputs"].items():
            if Path(artifact["path"]).suffix.lower() in {".tif", ".tiff"}:
                one = read_image(toml_out / artifact["path"], rgb=True)
                two = read_image(yaml_out / right["outputs"][label]["path"], rgb=True)
                assert one.dtype == two.dtype and one.shape == two.shape
                np.testing.assert_array_equal(one, two)
                image_count += 1
    assert image_count >= 4
    assert (yaml_out / "reports/index.html").is_file()
    assert all(sha256(path) == digest for path, digest in source_hashes.items())
    resumed = api.run(manifest, yaml_out, yaml, selected=["demo-A1"])
    assert all(row["cached"] for row in resumed["stage_results"])
