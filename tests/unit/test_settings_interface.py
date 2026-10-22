"""The public settings interface is typed, inspectable and has one precedence rule."""

from copy import deepcopy

import pytest

from tma_qc.api import settings
from tma_qc.schema import CORE_STAGES, PAIR_STAGES
from tma_qc.settings import method_registry, parse_parameters, resolve_settings


def test_toml_literals_preserve_parameter_types_and_nested_paths():
    parameters = parse_parameters(
        [
            "runtime.threads=3",
            "profile.shape=[64, 96]",
            "registration.export_aligned_he=false",
            'registration.matching.transform="affine"',
            'runtime.cache_root="/local/cache with spaces"',
        ]
    )
    assert parameters == {
        "runtime": {"threads": 3, "cache_root": "/local/cache with spaces"},
        "profile": {"shape": [64, 96]},
        "registration": {"export_aligned_he": False, "matching": {"transform": "affine"}},
    }


@pytest.mark.parametrize(
    "arguments",
    [
        ["runtime.threads"],
        ["runtime=2"],
        ["runtime..threads=2"],
        ["runtime.threads=2", "runtime.threads=3"],
        ["registration.matching={max_dimension=512}", "registration.matching.transform='affine'"],
        ["registration.matching.transform='affine'", "registration.matching={max_dimension=512}"],
        ["illumination.mode=none"],
        ["runtime.threads=inf"],
        ["runtime.threads=nan"],
        ["runtime.threads=2000-01-01"],
        ["runtime.threads=3\nother=4"],
    ],
)
def test_invalid_or_conflicting_literal_overrides_are_rejected(arguments):
    with pytest.raises(ValueError):
        parse_parameters(arguments)


def test_selector_precedence_preserves_unrelated_nested_settings_without_mutation(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        '[runtime]\nthreads=3\n[registration]\nbackend="core"\ncore_method="orb"\n'
        '[registration.matching]\nmax_dimension=512\ntransform="similarity"\n'
    )
    base = {"runtime": {"threads": 2}, "registration": {"matching": {"min_inliers": 8}}}
    parameters = {
        "runtime": {"threads": 4},
        "registration": {"core_method": "sift", "matching": {"transform": "similarity"}},
    }
    original = deepcopy((base, parameters))
    effective = resolve_settings(
        path,
        base=base,
        parameters=parameters,
        registration_method="geometry_sift",
        registration_transform="affine",
        registration_refinement="none",
        illumination_method="none",
    )
    assert effective["runtime"]["threads"] == 4
    assert effective["registration"]["backend"] == "core"
    assert effective["registration"]["core_method"] == "geometry_sift"
    assert effective["registration"]["matching"] == {
        "min_inliers": 8,
        "max_dimension": 512,
        "transform": "affine",
    }
    assert effective["illumination"]["mode"] == "none"
    assert (base, parameters) == original


@pytest.mark.parametrize("method", ["sift", "orb", "geometry_sift", "geometry_roma"])
def test_unified_core_method_maps_to_backend_and_initializer(method):
    selected = settings(registration_method=method)["registration"]
    assert selected["backend"] == "core" and selected["core_method"] == method


@pytest.mark.parametrize("method", ["provided", "sift_affine"])
@pytest.mark.parametrize(
    "options",
    [
        {"registration_transform": "affine"},
        {"registration_refinement": "none"},
    ],
)
def test_named_core_selectors_reject_a_noncore_method(method, options):
    with pytest.raises(ValueError, match="require a core method"):
        resolve_settings(registration_method=method, **options)


@pytest.mark.parametrize("method", ["provided", "geometry_sift", "sift_affine"])
def test_effective_settings_round_trip_with_inactive_presets(method):
    parameters = {
        "registration": {
            "max_dimension": 2048,
            "min_matches": 15,
            "ransac_pixels": 5.0,
            "matching": {"max_dimension": 512, "transform": "affine"},
            "roma": {"device": "cpu"},
        }
    }
    effective = settings(parameters=parameters, registration_method=method)
    assert settings(parameters=effective) == effective
    assert effective["registration"]["max_dimension"] == 2048
    assert effective["registration"]["matching"]["max_dimension"] == 512


def test_default_effective_settings_round_trip():
    effective = settings()
    assert settings(parameters=effective) == effective


def test_existing_file_presets_remain_compatible_with_provided_route(tmp_path):
    path = tmp_path / "presets.toml"
    path.write_text(
        '[registration]\nbackend="provided"\nmax_dimension=1024\n'
        '[registration.matching]\ntransform="affine"\n'
    )
    assert resolve_settings(path)["registration"]["backend"] == "provided"


def test_registry_is_json_safe_and_matches_fixed_stage_contract():
    import json

    registry = method_registry()
    json.dumps(registry, allow_nan=False)
    assert registry["fixed_af_stages"] == list(CORE_STAGES)
    assert registry["fixed_pair_stages"] == list(PAIR_STAGES)
    assert registry["methods"]["registration_method"]["default"] == "provided"
    assert registry["core_default_method"] == "geometry_sift"
    registry["methods"]["geometry_method"]["default"] = "changed"
    assert method_registry()["methods"]["geometry_method"]["default"] == "provided"


def test_invalid_python_parameter_tree_and_selector_are_rejected():
    with pytest.raises(ValueError, match="nested settings dictionary"):
        resolve_settings(parameters=[("runtime.threads", 2)])
    with pytest.raises(ValueError, match="geometry_method"):
        resolve_settings(geometry_method=[])
    with pytest.raises(ValueError):
        resolve_settings(parameters={"runtime": {"threds": 2}})
