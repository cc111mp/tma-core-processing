"""Core-scoped fitting, corrected MIST, and honest residual provenance."""

import importlib.metadata
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

from tma_qc.config import load_config
from tma_qc.demo import create_demo
from tma_qc.io.images import read_image
from tma_qc.pipeline import Runner, run_pipeline
from tma_qc.provenance import read_json, sha256, write_json
from tma_qc.schema import BASICPY_CORE_STAGES


class FakeFitter:
    identity = {"versions": {"basicpy": "2.0.0", "numpy": "1.26.4"}, "device": "cpu"}
    flat = 0.5
    converged = True

    def __init__(self):
        self.calls = []

    def fit(self, stack_path, indices, settings, output):
        stack = np.load(stack_path, allow_pickle=False)
        self.calls.append((stack.copy(), indices))
        shape = stack.shape[1:]
        return (
            np.full(shape, self.flat, np.float32),
            np.full(shape, 20000, np.float32),
            {
                "basicpy_version": "2.0.0",
                "device": "cpu",
                "runtime": self.identity,
                "converged": self.converged,
                "score": 0.0001,
                "reweight_score": 0.001,
                "elapsed_fit_seconds": 0.01,
            },
        )


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    manifest, config = create_demo(tmp_path / "input")
    data = read_json(manifest)
    data["pairs"] = []
    data["cores"] = data["cores"][:1]
    data["cores"][0]["metadata"].update(
        grid_reviewed=True,
        grid={
            "rows": 2,
            "columns": 2,
            "numbering_pattern": "HORIZONTALCOMBING",
            "origin": "UL",
            "horizontal_overlap": 40,
            "vertical_overlap": 40,
            "overlap_uncertainty": 3,
        },
    )
    # Candidate must work without group fields; retain supplied baseline geometry.
    data["groups"] = [{"id": "synthetic-group"}]
    write_json(manifest, data)
    jar = tmp_path / "mist.jar"
    jar.write_bytes(b"fixture-only")
    parameters = {
        "illumination": {"mode": "basicpy", "device": "cpu"},
        "geometry": {
            "backend": "mist",
            "input": "corrected",
            "downsample": 1,
            "threads": 8,
            "mist_classpath": str(jar),
        },
    }
    fitter = FakeFitter()
    monkeypatch.setattr(Runner, "fitter", lambda self: fitter)

    def java(command, **kwargs):
        options = {
            command[i]: command[i + 1]
            for i in range(len(command) - 1)
            if command[i].startswith("--")
        }
        assert options["--numCPUThreads"] == "8"
        inputs = Path(options["--imageDir"])
        for path in inputs.glob("*.tif"):
            with tifffile.TiffFile(path) as tif:
                assert not tif.is_bigtiff
                assert tif.pages[0].compression == 1
                assert tif.asarray().dtype == np.float32
                assert np.max(tif.asarray()) < 0
        out = Path(options["--outputPath"])
        (out / "img-global-positions-1.txt").write_text(
            "\n".join(
                f"file: FOV_{n:05d}.tif; corr: 1.0; position: ({x}, {y}); grid: (0, 0);"
                for n, (x, y) in enumerate([(0, 0), (96, 0), (0, 96), (96, 96)], 1)
            )
        )

    monkeypatch.setattr("tma_qc.backends.mist.subprocess.run", java)
    return manifest, config, tmp_path / "results", parameters, fitter


def execute(candidate, **kwargs):
    manifest, config, output, parameters, _ = candidate
    summary = run_pipeline(manifest, output, config, parameters=parameters, **kwargs)
    return summary, {r["stage"]: r for r in summary["stage_results"]}


def asset(candidate, stage, label):
    return candidate[2] / stage["outputs"][label]["path"]


@pytest.mark.parametrize(
    "parameters",
    [
        {"illumination": {"mode": "unknown"}},
        {"illumination": {"mode": "basicpy"}},
        {"illumination": {"mode": "cidre"}, "geometry": {"input": "corrected"}},
        {"illumination": {"mode": "none"}, "geometry": {"input": "corrected"}},
        {"illumination": {"mode": "basicpy"}, "geometry": {"backend": "mist", "input": "raw"}},
    ],
)
def test_incompatible_modes_fail_before_images(parameters):
    with pytest.raises(ValueError):
        load_config(overrides=parameters)


def test_fit_receipt_apply_and_one_raw_decode_per_fov(candidate, monkeypatch):
    import tma_qc.workflows as workflows
    from tma_qc.io import tile_cache

    decoded = []

    def read(path):
        if Path(path).is_relative_to(candidate[0].parent / "inputs"):
            decoded.append(str(path))
        return read_image(path)

    monkeypatch.setattr(workflows, "read_image", read)
    monkeypatch.setattr(tile_cache, "read_image", read)
    summary, stages = execute(candidate)
    assert summary["execution_errors"] == summary["blocked_stages"] == 0
    assert [r["stage"] for r in summary["stage_results"]] == list(BASICPY_CORE_STAGES)
    assert len(decoded) == len(set(decoded)) == 4
    fit = read_json(asset(candidate, stages["illumination_fit"], "fit"))
    assert fit["sample_scope"] == "core" and fit["tile_count"] == fit["stack_size"] == 4
    assert fit["field_identifiability"] == "limited"
    assert fit["settings"] == load_config(overrides=candidate[3])["illumination"]["basicpy"]
    assert all(f["membership"] == "in_fit" and len(f["sha256"]) == 64 for f in fit["sources"])
    frames_file = asset(candidate, stages["illumination"], "frames")
    frames = read_json(frames_file)
    raw = candidate[4].calls[0][0]
    for i, name in enumerate(frames.values()):
        result = read_image(frames_file.parent / name)
        assert result.dtype == np.float32 and np.max(result) < 0
        assert np.array_equal(result, (raw[i].astype(np.float32) - 20000) / 0.5)
    assert stages["geometry"]["metrics"]["source"] == "MIST_corrected_sample"
    assert all(r["qc"] == "review" for r in stages.values() if r["stage"] != "index")
    model = read_json(asset(candidate, stages["illumination"], "model"))
    assert model["quality_controls"] == "not_passed"
    assert model["absolute_intensity_preservation_validated"] is False


@pytest.mark.parametrize("invalid", ["nonpositive", "not_converged"])
def test_bad_field_or_convergence_blocks_apply_and_mosaic(candidate, invalid):
    if invalid == "nonpositive":
        candidate[4].flat = 0.0
    else:
        candidate[4].converged = False
    _, stages = execute(candidate)
    assert stages["illumination"]["execution"] in {"blocked", "error"}
    assert stages["mosaic"]["execution"] == "blocked"
    assert not list(candidate[2].rglob("raw.tif"))


@pytest.mark.parametrize("holdout", [False, True])
def test_qc_membership_and_geometry_residual_labels(candidate, holdout):
    if holdout:
        data = read_json(candidate[0])
        data["cores"][0]["illumination_holdout_fov_ids"] = ["1", "2"]
        write_json(candidate[0], data)
        candidate[3]["illumination"]["holdout"] = "declared"
    _, stages = execute(candidate)
    report = read_json(asset(candidate, stages["illumination_qc"], "overlaps"))
    assert report["compared_pairs"]
    memberships = {p["fit_membership"] for p in report["compared_pairs"]}
    if holdout:
        assert "held_out" in memberships and "partial" in memberships
        assert candidate[4].calls[0][1] == [2, 3]
    else:
        assert memberships == {"in_sample"}
        assert report["photometric_independence"] == "in_sample"
    assert report["worst_corrected_pair"]["fit_membership"] in memberships
    assert stages["geometry_qc"]["metrics"]["geometry_independence"] == "in_sample"
    assert stages["geometry_qc"]["metrics"]["estimation_inputs"] == "corrected_float32"
    geometry = read_json(asset(candidate, stages["geometry_qc"], "overlaps"))
    assert geometry["geometry_independence"] == "in_sample"


@pytest.mark.parametrize("missing", ["file", "partial", "grid", "jars"])
def test_missing_mist_outputs_or_prerequisites_do_not_publish_mosaic(
    candidate, monkeypatch, missing
):
    if missing in {"file", "partial"}:

        def java(command, **kwargs):
            out = Path(command[command.index("--outputPath") + 1])
            if missing == "partial":
                (out / "img-global-positions-1.txt").write_text(
                    "file: FOV_00001.tif; corr: 1.0; position: (0, 0); grid: (0, 0);\n"
                )

        monkeypatch.setattr("tma_qc.backends.mist.subprocess.run", java)
    elif missing == "grid":
        data = read_json(candidate[0])
        data["cores"][0]["metadata"].pop("grid")
        write_json(candidate[0], data)
    else:
        candidate[3]["geometry"]["mist_classpath"] += ".missing"
    _, stages = execute(candidate)
    assert stages["geometry"]["execution"] == (
        "error" if missing in {"file", "partial"} else "blocked"
    )
    assert stages["mosaic"]["execution"] == "blocked"


def test_resume_cannot_reuse_cidre_or_different_fit_geometry(candidate):
    manifest, config, output, parameters, _ = candidate
    data = read_json(manifest)
    data["groups"][0].update(
        flatfield="assets/flatfield.tif",
        darkfield="assets/darkfield.tif",
        model_metadata="assets/model.json",
    )
    write_json(manifest, data)
    baseline = run_pipeline(manifest, output, config)
    old = {r["stage"]: r for r in baseline["stage_results"]}
    _, candidate_stages = execute(candidate)
    for name in ("illumination", "geometry"):
        assert not candidate_stages[name]["cached"]
        assert candidate_stages[name]["fingerprint"] != old[name]["fingerprint"]
        assert candidate_stages[name]["outputs"] != old[name]["outputs"]
    _, resumed = execute(candidate)
    assert all(r["cached"] for r in resumed.values())
    parameters["illumination"]["basicpy"] = {"epsilon": 0.2}
    _, changed = execute(candidate)
    assert changed["geometry"]["fingerprint"] != candidate_stages["geometry"]["fingerprint"]
    deps = changed["geometry"]["provenance"]["dependencies"]
    assert "illumination_fit" in deps and "illumination" in deps


def test_landmarks_must_match_candidate_geometry(candidate):
    data = read_json(candidate[0])
    data["pairs"] = [
        {
            "uid": "pair",
            "af_core": "demo-A1",
            "he_path": "assets/he.tif",
            "he_sha256": sha256(candidate[0].parent / "assets/he.tif"),
            "transform": "assets/transform.json",
            "landmarks": "assets/landmarks.json",
        }
    ]
    write_json(candidate[0], data)
    _, stages = execute(candidate)
    assert stages["registration_qc"]["execution"] == "blocked"
    data["pairs"][0]["metadata"] = {
        "landmarks_geometry_fingerprint": stages["geometry"]["fingerprint"]
    }
    write_json(candidate[0], data)
    _, stages = execute(candidate)
    assert stages["registration_qc"]["execution"] == "complete"
    assert (
        stages["registration_qc"]["metrics"]["geometry_fingerprint"]
        == stages["geometry"]["fingerprint"]
    )


def test_profile_example_and_thread_budget():
    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "configs/basicpy_mist_core_v1.toml")
    assert config["geometry"]["threads"] == config["runtime"]["read_threads"] == 8
    with pytest.raises(ValueError, match="threads"):
        load_config(base=config, overrides={"geometry": {"threads": 16}})


def test_qualified_basicpy_synthetic_integration(tmp_path):
    try:
        qualified = (
            importlib.metadata.version("basicpy") == "2.0.0"
            and importlib.metadata.version("numpy") == "1.26.4"
        )
    except importlib.metadata.PackageNotFoundError:
        qualified = False
    if not qualified:
        pytest.skip("Requires BaSiCPy 2.0.0 / NumPy 1.26.4")
    import torch

    from tma_qc.backends.basicpy import ResidentFitter

    rng = np.random.default_rng(118)
    y, x = np.mgrid[:32, :32]
    flat = (0.7 + 0.3 * (x + y) / 62).astype(np.float32)
    stack = (rng.uniform(1000, 5000, (20, 32, 32)) * flat + 100).astype(np.uint16)
    path = tmp_path / "stack.npy"
    np.save(path, stack)
    settings = load_config()["illumination"]
    settings.update(
        device="cuda" if torch.cuda.is_available() else "cpu", basicpy_python=sys.executable
    )
    worker = ResidentFitter(settings, 2, tmp_path / "worker.log")
    try:
        s, d, receipt = worker.fit(
            path, list(range(len(stack))), settings["basicpy"], tmp_path / "fit"
        )
        assert receipt["converged"]
        assert np.isfinite(s).all() and np.isfinite(d).all() and np.min(s) > 0
        assert np.isfinite((stack.astype(np.float32) - d) / s).all()
    finally:
        worker.close()
