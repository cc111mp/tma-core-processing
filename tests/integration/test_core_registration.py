"""Controlled registration checks, not evidence of accuracy on biological sections."""

import csv

import cv2
import numpy as np
import pytest
import tifffile

from tma_qc.backends.core_registration.images import transform_points, warp
from tma_qc.io.coordinates import position_document
from tma_qc.pipeline import run_pipeline
from tma_qc.provenance import read_json, sha256, write_json


def core_fixture(tmp_path, *, refinement=False):
    root = tmp_path / "input"
    raw, assets = root / "raw", root / "assets"
    raw.mkdir(parents=True)
    assets.mkdir()
    rng = np.random.default_rng(47281)
    side = 512
    yy, xx = np.indices((side, side))
    mask = ((xx - 255) ** 2 / 215**2 + (yy - 249) ** 2 / 199**2) < 1
    mask &= (xx - 318) ** 2 + (yy - 226) ** 2 > 30**2
    mask |= (xx - 48) ** 2 + (yy - 437) ** 2 < 17**2
    signal = np.full((side, side), 0.18, np.float32)
    for _ in range(550):
        cv2.circle(
            signal,
            tuple(rng.integers(30, side - 30, 2).tolist()),
            int(rng.integers(2, 11)),
            float(rng.uniform(0.05, 0.95)),
            -1,
        )
    signal = cv2.GaussianBlur(signal, (0, 0), 0.7) * mask
    angle, scale = np.deg2rad(127), 0.8
    matrix = np.eye(3)
    matrix[:2, :2] = scale * np.array(
        [[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]]
    )
    matrix[:2, 2] = np.array([255, 250]) - matrix[:2, :2] @ np.array([315, 280])
    he_signal = warp(signal, np.linalg.inv(matrix), (576, 640))
    he = np.uint8(np.exp(-he_signal[..., None] * np.array([1.2, 2.5, 0.8])) * 255)
    af = np.uint16(np.clip(signal**0.8 * 50000 + rng.normal(100, 45, signal.shape), 0, 65535))
    tifffile.imwrite(raw / "FOV_1.tif", af)
    tifffile.imwrite(assets / "he.tif", he, photometric="rgb")
    write_json(assets / "positions.json", position_document([{"id": "1", "x": 0, "y": 0}]))
    points = np.array([[150, 150], [350, 150], [150, 350], [350, 350], [250, 250]], float)
    source = transform_points(np.linalg.inv(matrix), points)
    with (assets / "held-out.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["he_x", "he_y", "af_x", "af_y"])
        writer.writerows(np.column_stack([source, points]))
    write_json(
        root / "manifest.json",
        {
            "schema_version": 1,
            "dataset_id": "synthetic-registration",
            "raw_root": "raw",
            "groups": [{"id": "uncorrected"}],
            "cores": [
                {
                    "uid": "AF-A1",
                    "acquisition": "synthetic",
                    "slide": "AF-slide",
                    "core": "A1",
                    "channel": "AF",
                    "group": "uncorrected",
                    "fovs": [{"id": "1", "path": "FOV_1.tif", "sha256": sha256(raw / "FOV_1.tif")}],
                    "positions": "assets/positions.json",
                    "geometry_reviewed": True,
                }
            ],
            "pairs": [
                {
                    "uid": "pair-A1",
                    "af_core": "AF-A1",
                    "he_path": "assets/he.tif",
                    "he_sha256": sha256(assets / "he.tif"),
                    "relationship": "neighboring_sections",
                    "landmarks": "assets/held-out.csv",
                }
            ],
        },
    )
    (root / "core.toml").write_text(
        "export_chunk=128\n[matching]\nmax_dimension=512\n[refinement]\nbackend='"
        + ("bspline_mi" if refinement else "none")
        + "'\niterations=3\n"
    )
    (root / "main.toml").write_text(
        f"[profile]\nshape=[512,512]\n[runtime]\ncache_root='{tmp_path / 'cache'}'\n[illumination]\nmode='none'\n[registration]\nbackend='core'\ncore_method='geometry_sift'\ncore_config='core.toml'\n"
    )
    return root / "manifest.json", root / "main.toml", tmp_path / "run"


def mapping(summary):
    return {(row["unit"], row["stage"]): row for row in summary["stage_results"]}


@pytest.mark.parametrize("refinement", [False, True])
def test_integrated_core_registration_export_independent_qc_and_resume(tmp_path, refinement):
    if refinement:
        pytest.importorskip("SimpleITK")
    manifest, config, out = core_fixture(tmp_path, refinement=refinement)
    first = mapping(run_pipeline(manifest, out, config))
    result = first["pair-A1", "registration"]
    assert result["execution"] == "complete", result["findings"]
    assert result["qc"] == "review"
    assert first["pair-A1", "registration_qc"]["metrics"]["median_error_native_af_pixels"] < 2
    assert {
        "transform",
        "candidates",
        "refinement",
        "fit_correspondences",
        "aligned_he",
        "geometric_overlap",
    } <= set(result["outputs"])
    transform = read_json(out / result["outputs"]["transform"]["path"])
    assert transform["direction"] == "he_to_af"
    assert transform["native_coordinate_convention"] == "pixel_centres_xy"
    assert transform["diagnostics"]["refinement"]["status"] in {
        "disabled",
        "rejected",
        "applied_experimental",
    }
    exported = tifffile.memmap(out / result["outputs"]["aligned_he"]["path"])
    assert exported.shape == (512, 512, 3)
    exported._mmap.close()
    second = mapping(run_pipeline(manifest, out, config))
    assert all(row["cached"] for row in second.values())
    # Landmark edits cannot recompute or alter the fitted transform.
    landmarks = manifest.parent / "assets/held-out.csv"
    lines = landmarks.read_text().splitlines()
    values = lines[1].split(",")
    values[2] = str(float(values[2]) + 40)
    lines[1] = ",".join(values)
    landmarks.write_text("\n".join(lines) + "\n")
    third = mapping(run_pipeline(manifest, out, config))
    assert third["pair-A1", "registration"]["cached"]
    assert not third["pair-A1", "registration_qc"]["cached"]
    # Core configuration is content-addressed independently of the main TOML.
    core_config = config.parent / "core.toml"
    core_config.write_text(core_config.read_text().replace("export_chunk=128", "export_chunk=256"))
    fourth = mapping(run_pipeline(manifest, out, config))
    assert not fourth["pair-A1", "registration"]["cached"]
    assert fourth["AF-A1", "mosaic"]["cached"]


def test_absent_foreground_is_blocked_with_preparation_evidence(tmp_path):
    manifest, config, out = core_fixture(tmp_path)
    data = read_json(manifest)
    he = manifest.parent / "assets/he.tif"
    tifffile.imwrite(he, np.full((576, 640, 3), 255, np.uint8), photometric="rgb")
    data["pairs"][0]["he_sha256"] = sha256(he)
    write_json(manifest, data)
    result = mapping(run_pipeline(manifest, out, config))
    assert result["pair-A1", "registration"]["execution"] == "blocked"
    assert "diagnostic:preparation.json" in result["pair-A1", "registration"]["outputs"]
    assert result["pair-A1", "registration_qc"]["execution"] == "blocked"
    assert result["AF-A1", "mosaic"]["execution"] == "complete"


def test_unrelated_core_has_no_consensus_and_preserves_candidate_checks(tmp_path):
    manifest, config, out = core_fixture(tmp_path)
    data = read_json(manifest)
    he = manifest.parent / "assets/he.tif"
    tifffile.imwrite(
        he, np.random.default_rng(98).integers(20, 230, (576, 640, 3), np.uint8), photometric="rgb"
    )
    data["pairs"][0]["he_sha256"] = sha256(he)
    write_json(manifest, data)
    result = mapping(run_pipeline(manifest, out, config))
    stage = result["pair-A1", "registration"]
    assert stage["execution"] == "blocked"
    assert "no eligible consensus" in stage["findings"][0]
    candidates = read_json(out / stage["outputs"]["diagnostic:candidates.json"]["path"])
    assert len(candidates) == 4
    assert all(not candidate["fit_eligible"] for candidate in candidates)


def test_missing_core_config_is_isolated_from_af_processing(tmp_path):
    manifest, config, out = core_fixture(tmp_path)
    (config.parent / "core.toml").unlink()
    result = mapping(run_pipeline(manifest, out, config))
    assert result["AF-A1", "mosaic"]["execution"] == "complete"
    assert result["pair-A1", "registration"]["execution"] == "error"
    assert result["pair-A1", "registration_qc"]["execution"] == "blocked"


@pytest.mark.parametrize("declared_shape", [None, [123, 123]])
def test_provided_residual_requires_native_canvas_coverage_and_shape_match(
    tmp_path, declared_shape
):
    from tma_qc.backends.core_registration.transforms import Transform

    manifest, config, out = core_fixture(tmp_path)
    field = np.zeros((2, 2, 2), np.float32)
    path = manifest.parent / "assets/provided-transform.json"
    np.save(path.parent / "provided-field.npy", field)
    record = Transform(np.eye(3), np.eye(3), np.eye(3), field).record("provided-field.npy")
    if declared_shape is not None:
        record["af_native_shape_yx"] = declared_shape
    write_json(path, record)
    data = read_json(manifest)
    data["pairs"][0]["transform"] = str(path)
    write_json(manifest, data)
    result = mapping(run_pipeline(manifest, out, config))
    assert result["AF-A1", "mosaic"]["execution"] == "complete"
    assert result["pair-A1", "registration"]["execution"] == "error"
    assert "actual" in result["pair-A1", "registration"]["findings"][0]
