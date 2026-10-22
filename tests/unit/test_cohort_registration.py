"""Historical reports must invalidate changed registration prerequisites."""

import numpy as np
import pytest

from tma_qc.backends.core_registration.transforms import Transform
from tma_qc.cohort import Cohort
from tma_qc.config import load_config
from tma_qc.demo import create_demo
from tma_qc.io.manifest import Manifest
from tma_qc.provenance import write_json


def observed_pair(tmp_path):
    path, config = create_demo(tmp_path / "inputs")
    manifest = Manifest(path)
    settings = load_config(config)
    settings["registration"]["backend"] = "core"
    manifest.pairs[0].transform = None
    output = tmp_path / "run"
    return manifest, settings, output


def save_observation(manifest, settings, output):
    cohort = Cohort(output, manifest, settings, "code", {})
    cohort.begin("demo-pair", "original")
    cohort.finish("demo-pair")


def observation(manifest, settings, output):
    return Cohort(output, manifest, settings, "code", {}).snapshot("other")["units"]["demo-pair"][
        "observation"
    ]


def test_external_core_config_content_makes_historical_pair_stale(tmp_path):
    manifest, settings, output = observed_pair(tmp_path)
    external = tmp_path / "core.toml"
    external.write_text("export_chunk=512\n")
    settings["registration"]["core_config"] = str(external)
    save_observation(manifest, settings, output)
    assert observation(manifest, settings, output) == "historical_not_reverified"
    external.write_text("export_chunk=1024\n")
    assert observation(manifest, settings, output) == "stale"


def test_referenced_residual_content_makes_historical_pair_stale(tmp_path):
    manifest, settings, output = observed_pair(tmp_path)
    path = tmp_path / "transform.json"
    field = tmp_path / "field.npy"
    np.save(field, np.zeros((4, 4, 2), np.float32))
    transform = Transform(np.eye(3), np.eye(3), np.eye(3), np.zeros((4, 4, 2), np.float32))
    write_json(path, transform.record(field.name))
    manifest.pairs[0].transform = str(path)
    save_observation(manifest, settings, output)
    assert observation(manifest, settings, output) == "historical_not_reverified"
    np.save(field, np.full((4, 4, 2), 0.25, np.float32))
    assert observation(manifest, settings, output) == "stale"


def test_optional_runtime_identity_change_makes_historical_pair_stale(tmp_path, monkeypatch):
    from tma_qc.backends import core_registration

    manifest, settings, output = observed_pair(tmp_path)
    runtime = {"optional_versions": {"romatch": "first"}, "model_weights": {"sha": "first"}}
    monkeypatch.setattr(core_registration, "runtime_identity", lambda _: runtime.copy())
    save_observation(manifest, settings, output)
    assert observation(manifest, settings, output) == "historical_not_reverified"
    runtime["model_weights"] = {"sha": "changed"}
    assert observation(manifest, settings, output) == "stale"


@pytest.mark.parametrize("prerequisite", ["missing_config", "missing_optional_runtime"])
def test_missing_registration_prerequisite_does_not_abort_af_observation(
    tmp_path, monkeypatch, prerequisite
):
    manifest, settings, output = observed_pair(tmp_path)
    if prerequisite == "missing_config":
        settings["registration"]["core_config"] = str(tmp_path / "absent.toml")
    else:
        from tma_qc.backends import core_registration
        from tma_qc.schema import NeedsInput

        def unavailable(_):
            raise NeedsInput("Optional registration runtime absent")

        monkeypatch.setattr(core_registration, "runtime_identity", unavailable)
    cohort = Cohort(output, manifest, settings, "code", {})
    cohort.begin("demo-A1", "new")
    cohort.begin("demo-pair", "new")
    assert cohort.snapshot("new")["units"]["demo-A1"]["observation"] == "current_attempt"
