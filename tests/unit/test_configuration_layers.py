"""Strict, sparse and reproducible settings without optional image runtimes."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tma_qc.config import DEFAULTS, load_config
from tma_qc.registration_config import Experiment, resolve_core_config
from tma_qc.registration_config import load_config as load_core_config


def test_recursive_main_layer_precedence_retains_nested_siblings_without_mutation(tmp_path):
    base = {
        "runtime": {"threads": 3},
        "registration": {
            "matching": {"ratio": 0.61, "max_dimension": 2048},
            "refinement": {"iterations": 8},
        },
    }
    overrides = {"registration": {"matching": {"ratio": 0.77}}}
    original_base, original_overrides = deepcopy(base), deepcopy(overrides)
    path = tmp_path / "main.toml"
    path.write_text("[runtime]\nthreads=5\n[registration.matching]\nmax_matches=1000\nratio=0.72\n")
    result = load_config(path, base=base, overrides=overrides)
    assert result["runtime"]["threads"] == 5
    assert result["registration"]["matching"] == {
        "ratio": 0.77,
        "max_dimension": 2048,
        "max_matches": 1000,
    }
    assert result["registration"]["refinement"] == {"iterations": 8}
    assert result["registration"]["roma"] == {}
    assert base == original_base and overrides == original_overrides
    assert DEFAULTS["registration"]["matching"] == {}


def test_list_overrides_do_not_share_caller_state():
    supplied = {
        "profile": {"shape": [160, 160]},
        "registration": {"matching": {"rotations": [0, 90]}},
    }
    result = load_config(overrides=supplied)
    supplied["profile"]["shape"][0] = 99
    supplied["registration"]["matching"]["rotations"].append(180)
    assert result["profile"]["shape"] == [160, 160]
    assert result["registration"]["matching"]["rotations"] == [0, 90]


@pytest.mark.parametrize("layer", ["base", "overrides"])
@pytest.mark.parametrize(
    "malformed",
    [
        [],
        {"unknown": {}},
        {"registration": None},
        {"registration": {"matching": []}},
        {"registration": {"matching": {"max_dimention": 256}}},
        {"registration": {"refinement": {"iterations": True}}},
        {"runtime": {"threads": "2"}},
        {"runtime": {"threads": 2**31}},
        {"runtime": {"cache_root": " "}},
        {"geometry": {"mist_classpath": []}},
        {"geometry": {"timeout_seconds": True}},
        {"geometry": {"timeout_seconds": 0}},
        {"geometry": {"timeout_seconds": float("inf")}},
        {"profile": {"shape": [160, True]}},
    ],
)
def test_all_dictionary_layers_reject_unknown_keys_and_malformed_types(layer, malformed):
    with pytest.raises(ValueError):
        load_config(**{layer: malformed})


@pytest.mark.parametrize(
    "inline",
    [
        {"matching": {"min_inlier_fraction": True}},
        {"matching": {"max_anisotropy": True}},
        {"matching": {"rotations": [True]}},
        {"matching": {"ratio": ".7"}},
        {"refinement": {"max_displacement_view_pixels": True}},
        {"roma": {"device": []}},
        {"matching": {"ratio": float("nan")}},
        {"matching": {"ransac_view_pixels": -1}},
        {"refinement": {"min_jacobian": 1.0}},
    ],
)
def test_inline_core_fields_reject_bool_and_invalid_values(inline):
    with pytest.raises(ValueError):
        load_config(overrides={"registration": inline})


def test_core_file_then_sparse_inline_then_selected_single_method(tmp_path):
    path = tmp_path / "core.toml"
    path.write_text(
        "methods=['sift','orb']\nexport_chunk=128\n[matching]\nmax_dimension=2048\nratio=0.63\nransac_view_pixels=7.0\n[refinement]\nbackend='bspline_mi'\niterations=9\n[roma]\ndevice='cpu'\n"
    )
    settings = {
        "core_config": str(path),
        "core_method": "geometry_roma",
        "matching": {"ratio": 0.71},
        "refinement": {"iterations": 12},
    }
    original = deepcopy(settings)
    resolved = resolve_core_config(settings)
    assert resolved.methods == ("geometry_roma",)
    assert resolved.matching.max_dimension == 2048 and resolved.matching.ransac_view_pixels == 7
    assert resolved.matching.ratio == 0.71
    assert resolved.refinement.backend == "bspline_mi" and resolved.refinement.iterations == 12
    assert resolved.roma.device == "cpu" and resolved.export_chunk == 128
    assert settings == original


def test_inline_can_repair_overridden_external_value_before_combined_validation(tmp_path):
    path = tmp_path / "core.toml"
    path.write_text("[matching]\nmax_dimension=12\nratio='bad'\n")
    with pytest.raises(ValueError):
        load_core_config(path)
    resolved = resolve_core_config(
        {"core_config": str(path), "matching": {"max_dimension": 256, "ratio": 0.8}}
    )
    assert resolved.matching.max_dimension == 256
    assert resolved.matching.ratio == 0.8


def test_partial_inline_cross_field_constraint_uses_external_file_not_defaults(tmp_path):
    path = tmp_path / "core.toml"
    path.write_text("[matching]\nmax_dimension=2048\n")
    main = load_config(
        overrides={
            "registration": {
                "backend": "core",
                "core_config": str(path),
                "matching": {"ransac_view_pixels": 1500},
            }
        }
    )
    assert main["registration"]["matching"] == {"ransac_view_pixels": 1500}
    assert resolve_core_config(main["registration"]).matching.ransac_view_pixels == 1500
    with pytest.raises(ValueError, match="RANSAC"):
        resolve_core_config({"matching": {"max_dimension": 1024, "ransac_view_pixels": 1500}})


def test_missing_external_file_is_deferred_and_paths_remain_relative_to_main_toml(tmp_path):
    main = tmp_path / "main.toml"
    main.write_text("[registration]\nbackend='core'\ncore_config='absent.toml'\n")
    config = load_config(main)
    assert config["registration"]["core_config"] == str(tmp_path / "absent.toml")
    with pytest.raises(FileNotFoundError):
        resolve_core_config(config["registration"])
    replay = load_config(base=config, overrides={"registration": {"matching": {"ratio": 0.8}}})
    assert replay["registration"]["core_config"] == config["registration"]["core_config"]


def test_old_main_and_core_example_configs_still_load():
    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "configs/registration-core.toml")
    resolved = resolve_core_config(config["registration"])
    assert isinstance(resolved, Experiment)
    assert resolved.matching.max_dimension == 1024
    assert resolved.refinement.backend == "none"
    assert load_core_config(root / "configs/core-registration.toml").matching == resolved.matching


def test_resolved_config_and_import_path_need_no_image_libraries(tmp_path):
    script = """
import json, sys
from tma_qc.config import load_config
from tma_qc.registration_config import resolve_core_config
config=load_config(overrides={'registration':{'backend':'core','matching':{'ratio':.8}}})
resolved=resolve_core_config(config['registration'])
assert resolved.matching.ratio==.8
assert not {'numpy','scipy','cv2','tifffile','PIL','torch','romatch','SimpleITK'}.intersection(sys.modules)
print(json.dumps(resolved.to_dict()))
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    completed = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True
    )
    assert json.loads(completed.stdout)["matching"]["ratio"] == 0.8


def test_geometry_timeout_accepts_positive_real_seconds():
    assert (
        load_config(overrides={"geometry": {"timeout_seconds": 0.5}})["geometry"]["timeout_seconds"]
        == 0.5
    )


def test_core_config_relative_path_tracks_winning_layer_origin(tmp_path, monkeypatch):
    config_dir = tmp_path / "config-directory"
    cwd = tmp_path / "working-directory"
    config_dir.mkdir()
    cwd.mkdir()
    main = config_dir / "main.toml"
    main.write_text("[registration]\ncore_config='from-main.toml'\n")
    monkeypatch.chdir(cwd)
    assert load_config(main)["registration"]["core_config"] == str(config_dir / "from-main.toml")
    assert load_config(main, overrides={"registration": {"core_config": "from-override.toml"}})[
        "registration"
    ]["core_config"] == str(cwd / "from-override.toml")
    assert load_config(base={"registration": {"core_config": "from-base.toml"}})["registration"][
        "core_config"
    ] == str(cwd / "from-base.toml")
    # A nested override does not change the winning filename layer.
    assert load_config(main, overrides={"registration": {"matching": {"ratio": 0.8}}})[
        "registration"
    ]["core_config"] == str(config_dir / "from-main.toml")


@pytest.mark.parametrize(
    "overrides",
    [
        {"registration": {"matching": {"ratio": 10**500}}},
        {"registration": {"matching": {"rotations": [10**500]}}},
        {"tile_qc": {"prefilter_sigma": 10**500}},
        {"geometry": {"timeout_seconds": 10**500}},
    ],
)
def test_huge_numbers_raise_configuration_value_errors(overrides):
    with pytest.raises(ValueError):
        load_config(overrides=overrides)
