"""Portable settings syntax keeps scientific validation and layer semantics."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from tma_qc.config import load_config
from tma_qc.configuration_io import read_configuration, render_yaml_settings
from tma_qc.registration_config import resolve_core_config


def test_yaml_and_toml_share_scientific_defaults_and_layer_precedence(tmp_path):
    toml = tmp_path / "settings.toml"
    toml.write_text(
        "[runtime]\nthreads=5\n[illumination]\nmode='none'\n"
        "[registration.matching]\nratio=0.72\nmax_matches=1000\n"
    )
    yaml = tmp_path / "settings.yaml"
    yaml.write_text(
        "runtime:\n  threads: 5\nillumination:\n  mode: none\n"
        "registration:\n  matching:\n    ratio: 0.72\n    max_matches: 1000\n"
    )
    base = {
        "runtime": {"threads": 3},
        "registration": {"matching": {"ratio": 0.61, "max_dimension": 2048}},
    }
    overrides = {"registration": {"matching": {"ratio": 0.77}}}
    original_base, original_overrides = deepcopy(base), deepcopy(overrides)
    actual = load_config(yaml, base=base, overrides=overrides)
    assert actual == load_config(toml, base=base, overrides=overrides)
    assert actual["runtime"]["threads"] == 5
    assert actual["registration"]["matching"] == {
        "ratio": 0.77,
        "max_dimension": 2048,
        "max_matches": 1000,
    }
    assert base == original_base and overrides == original_overrides


@pytest.mark.parametrize("suffix", [".yaml", ".yml", ".YAML", ".YML"])
def test_yaml_suffixes_are_case_insensitive(tmp_path, suffix):
    path = tmp_path / ("settings" + suffix)
    path.write_text("runtime: {threads: 3}\n")
    assert load_config(path)["runtime"]["threads"] == 3


@pytest.mark.parametrize("suffix", [".toml", ".data", ".json", ""])
def test_other_suffixes_retain_historical_toml_loading(tmp_path, suffix):
    path = tmp_path / ("settings" + suffix)
    path.write_text("[runtime]\nthreads=3\n")
    assert load_config(path)["runtime"]["threads"] == 3


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("runtime:\n  threads: 2\n  threads: 3\n", "Duplicate key"),
        ("runtime: {}\nruntime: {}\n", "Duplicate key"),
        ("runtime: {threads: 2, threads: 3}\n", "Duplicate key"),
        ("runtime: &common {threads: 2}\n", "anchors and aliases"),
        ("runtime: *common\n", "anchors and aliases"),
        ("runtime: !custom {}\n", "Explicit YAML tags"),
        ("runtime: !!map {}\n", "Explicit YAML tags"),
        ("runtime: !!python/object/apply:os.system ['touch should-not-exist']\n", "tags"),
        ("runtime: {1: 2}\n", "keys must be strings"),
        ("true: {}\n", "keys must be strings"),
        ("runtime: {? [one, two]: 2}\n", "keys must be strings"),
        ("runtime: {<<: {threads: 2}}\n", "keys must be strings"),
        ("runtime: {threads: .inf}\n", "finite"),
        ("runtime: {threads: -.Inf}\n", "finite"),
        ("runtime: {threads: .NaN}\n", "finite"),
        ("runtime: {threads: 2026-10-26}\n", "JSON-like values"),
        ("runtime: {threads: 2026-10-26T12:00:00Z}\n", "JSON-like values"),
        ("runtime: !!set {threads: null}\n", "tags"),
        ("runtime: [\n", "Invalid YAML"),
        ("{}\n---\n{}\n", "Invalid YAML"),
        ("", "mapping/object"),
        ("null\n", "mapping/object"),
        ("[runtime]\n", "mapping/object"),
        ("plain scalar\n", "mapping/object"),
    ],
)
def test_reject_unsafe_ambiguous_or_non_json_yaml(tmp_path, contents, message):
    path = tmp_path / "invalid.yaml"
    path.write_text(contents)
    with pytest.raises(ValueError, match=message) as caught:
        read_configuration(path)
    assert str(path) in str(caught.value)
    assert not (tmp_path / "should-not-exist").exists()


@pytest.mark.parametrize(
    "contents",
    [
        "runtime: {threads: '2'}\n",
        "runtime: {threads: true}\n",
        "runtime: {threads: 2.0}\n",
        "runtime: {threads: null}\n",
        "runtime: {threadz: 2}\n",
        "registration: {matching: {ratio: 1.2}}\n",
        "illumination: {mode: invented_method}\n",
    ],
)
def test_yaml_values_use_existing_typed_stage_validation(tmp_path, contents):
    path = tmp_path / "invalid-settings.yaml"
    path.write_text(contents)
    with pytest.raises(ValueError):
        load_config(path)


def test_safe_reader_preserves_json_like_types_and_quoted_strings(tmp_path):
    path = tmp_path / "values.yaml"
    path.write_text(
        "empty: null\nvalues: [true, false, 2, 0.75, '2', '2026-10-26']\n'true': {'01': 'yes'}\n"
    )
    assert read_configuration(path) == {
        "empty": None,
        "values": [True, False, 2, 0.75, "2", "2026-10-26"],
        "true": {"01": "yes"},
    }


def test_yaml_relative_external_core_config_tracks_winning_layer(tmp_path, monkeypatch):
    config_dir, cwd = tmp_path / "config", tmp_path / "cwd"
    config_dir.mkdir()
    cwd.mkdir()
    (config_dir / "core.toml").write_text("[matching]\nmax_dimension=2048\nratio=0.65\n")
    main = config_dir / "settings.yaml"
    main.write_text("registration:\n  backend: core\n  core_config: core.toml\n")
    monkeypatch.chdir(cwd)
    result = load_config(main, overrides={"registration": {"matching": {"ratio": 0.8}}})
    assert result["registration"]["core_config"] == str(config_dir / "core.toml")
    resolved = resolve_core_config(result["registration"])
    assert resolved.matching.max_dimension == 2048 and resolved.matching.ratio == 0.8
    assert load_config(main, overrides={"registration": {"core_config": "override.toml"}})[
        "registration"
    ]["core_config"] == str(cwd / "override.toml")
    assert load_config(base={"registration": {"core_config": "base.toml"}})["registration"][
        "core_config"
    ] == str(cwd / "base.toml")


def test_missing_external_core_file_remains_deferred_for_yaml(tmp_path):
    path = tmp_path / "main.yaml"
    path.write_text("registration: {backend: core, core_config: absent.toml}\n")
    settings = load_config(path)
    assert settings["registration"]["core_config"] == str(tmp_path / "absent.toml")
    with pytest.raises(FileNotFoundError):
        resolve_core_config(settings["registration"])


def test_renderer_roundtrips_resolved_settings_without_changing_order_or_types(tmp_path):
    settings = load_config(
        overrides={
            "illumination": {"mode": "none"},
            "registration": {"matching": {"rotations": [0, 90], "ratio": 0.8}},
        }
    )
    original = deepcopy(settings)
    rendered = render_yaml_settings(settings)
    path = tmp_path / "resolved.yaml"
    path.write_text(rendered)
    assert read_configuration(path) == settings
    assert load_config(path) == settings
    assert list(read_configuration(path)) == list(settings)
    assert settings == original
    assert "&id" not in rendered and "*id" not in rendered and "!!" not in rendered


def test_renderer_quotes_strings_and_expands_shared_values(tmp_path):
    shared = ["yes", "on", "true", "null", "2026-10-26", "123", "染色"]
    values = {"first": shared, "second": shared, "nothing": None}
    path = tmp_path / "shared.yaml"
    rendered = render_yaml_settings(values)
    path.write_text(rendered)
    restored = read_configuration(path)
    assert restored == values
    assert restored["first"] is not restored["second"]
    assert "&id" not in rendered and "*id" not in rendered


@pytest.mark.parametrize(
    "values",
    [
        [],
        {1: "key"},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": 10**500},
        {"value": date(2026, 10, 26)},
        {"value": b"data"},
        {"value": {1, 2}},
        {"value": (1, 2)},
    ],
)
def test_renderer_rejects_non_json_values(values):
    with pytest.raises(ValueError):
        render_yaml_settings(values)


def test_renderer_rejects_recursive_values():
    values = {}
    values["recursive"] = values
    with pytest.raises(ValueError, match="recursive"):
        render_yaml_settings(values)


def test_reader_errors_are_clear_and_filesystem_errors_remain_distinct(tmp_path):
    invalid_toml = tmp_path / "bad.data"
    invalid_toml.write_text("[runtime\n")
    with pytest.raises(ValueError, match="Invalid TOML configuration") as caught:
        read_configuration(invalid_toml)
    assert str(invalid_toml) in str(caught.value)
    invalid_yaml = tmp_path / "bad.yaml"
    invalid_yaml.write_bytes(b"runtime: \xff")
    with pytest.raises(ValueError, match="Invalid YAML configuration"):
        read_configuration(invalid_yaml)
    with pytest.raises(FileNotFoundError):
        read_configuration(tmp_path / "absent.yaml")
    with pytest.raises(ValueError, match="string or Path"):
        read_configuration({})


def test_configuration_import_and_toml_path_do_not_require_yaml_or_image_libraries(tmp_path):
    path = tmp_path / "settings.data"
    path.write_text("[runtime]\nthreads=3\n")
    script = """
import builtins, json, sys
original_import = builtins.__import__
def without_yaml(name, *args, **kwargs):
    if name == 'yaml' or name.startswith('yaml.'):
        raise ImportError('intentionally absent yaml')
    return original_import(name, *args, **kwargs)
builtins.__import__ = without_yaml
from tma_qc.config import load_config
from tma_qc.configuration_io import read_configuration
assert load_config(sys.argv[1])['runtime']['threads'] == 3
try:
    read_configuration(sys.argv[2])
except ValueError as error:
    assert 'PyYAML' in str(error)
else:
    raise AssertionError('Missing YAML dependency should be explained')
assert not {'yaml', 'numpy', 'scipy', 'cv2', 'tifffile', 'PIL', 'torch', 'romatch', 'SimpleITK'}.intersection(sys.modules)
print(json.dumps({'toml_without_yaml': True}))
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    completed = subprocess.run(
        [sys.executable, "-c", script, str(path), str(tmp_path / "absent.yaml")],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout) == {"toml_without_yaml": True}
