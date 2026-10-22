import runpy
import tomllib
from pathlib import Path

import numpy as np
import pytest

from tma_qc.config import load_config
from tma_qc.io.coordinates import load_positions, position_document
from tma_qc.io.images import save_preview
from tma_qc.provenance import read_json, write_json
from tma_qc.qc.registration import evaluate_landmarks, load_transform
from tma_qc.schema import safe_id


def test_checkout_and_package_version_contract():
    root = Path(__file__).resolve().parents[2]
    configured = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    shim = runpy.run_path(str(root / "tma_qc/__init__.py"))["__version__"]
    package = runpy.run_path(str(root / "src/tma_qc/__init__.py"))["__version__"]
    assert configured == shim == package


def test_positions_require_complete_explicit_native_ids(tmp_path):
    path = tmp_path / "positions.json"
    for rows, message in [
        ([{"id": "1", "x": 0.25, "y": 0}], "integer"),
        ([{"id": "2", "x": 0, "y": 0}], "match"),
        ([{"id": "1", "x": -1, "y": 0}], "integer"),
        ([{"id": "1", "x": 0, "y": 0}, {"id": "1", "x": 5, "y": 0}], "match"),
    ]:
        write_json(path, position_document(rows))
        with pytest.raises(ValueError, match=message):
            load_positions(path, ["1"])


def test_path_traversal_and_unknown_config_rejected(tmp_path):
    for value in ["../A1", "/tmp/A1", "A/1", "..", "A..1"]:
        with pytest.raises(ValueError):
            safe_id(value)
    config = tmp_path / "bad.toml"
    config.write_text("[tile_qc]\nstrdie = 128\n")
    with pytest.raises(ValueError, match="Unknown"):
        load_config(config)


def test_nonfinite_provenance_rejected(tmp_path):
    with pytest.raises(ValueError):
        write_json(tmp_path / "bad.json", {"score": float("nan")})
    p = tmp_path / "invalid.json"
    p.write_text('{"score": NaN}')
    with pytest.raises(ValueError):
        read_json(p)


def test_native_he_to_af_direction_and_independent_landmarks(tmp_path):
    path = tmp_path / "transform.json"
    write_json(path, {"matrix": np.eye(3).tolist()})
    with pytest.raises(ValueError, match="direction"):
        load_transform(path)
    matrix = np.array([[2.0, 0.0, 3.0], [0.0, 2.0, 5.0], [0.0, 0.0, 1.0]])
    landmarks = tmp_path / "landmarks.json"
    write_json(
        landmarks,
        {
            "role": "evaluation_only",
            "coordinate_space": "native_pixels",
            "points": [{"he": [10, 20], "af": [23, 45]}, {"he": [20, 30], "af": [43, 69]}],
        },
    )
    report = evaluate_landmarks(matrix, landmarks, tolerance=5, minimum=2)
    assert report["errors_native_af_pixels"] == [0, 4]
    assert not report["dense_correspondence_validated"]
    assert not report["tolerance_calibrated"]


def test_uint16_rgb_preview_scales_instead_of_wrapping_counts(tmp_path):
    from PIL import Image

    image = np.full((8, 8, 3), [65535, 32768, 0], np.uint16)
    save_preview(tmp_path / "he.png", image)
    np.testing.assert_array_equal(np.asarray(Image.open(tmp_path / "he.png"))[0, 0], [255, 128, 0])


def test_pair_identity_cannot_be_confused_with_a_core_in_reports(tmp_path):
    from tma_qc.demo import create_demo
    from tma_qc.io.manifest import Manifest

    path, _ = create_demo(tmp_path / "demo")
    data = read_json(path)
    data["pairs"][0]["uid"] = data["cores"][0]["uid"]
    write_json(path, data)
    with pytest.raises(ValueError, match="globally distinct"):
        Manifest(path)
