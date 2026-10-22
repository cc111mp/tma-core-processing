"""Regression controls for defects reproduced in the 2026-10-18 review."""

import sqlite3
from pathlib import Path

import numpy as np
import pytest
import tifffile

from tma_qc.backends.cidre import apply_field
from tma_qc.cli import main
from tma_qc.io.images import read_image, write_tiff
from tma_qc.io.manifest import Manifest, import_copy_inventory
from tma_qc.provenance import read_json, sha256, write_json
from tma_qc.qc.integrity import verify_core
from tma_qc.registration_check import check_core_safely
from tma_qc.report import load_settings


def inventory(tmp_path, files, directories=()):
    path = tmp_path / "copy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)")
        db.execute(
            "CREATE TABLE entries (path TEXT,kind TEXT,copied INTEGER,copy_error TEXT,inventory_error TEXT,source_sha256 TEXT,destination_verified INTEGER)"
        )
        db.execute("INSERT INTO meta VALUES ('inventory_complete','true')")
        for kind, paths in [("file", files), ("dir", directories)]:
            for entry in paths:
                db.execute(
                    "INSERT INTO entries VALUES (?,?,1,NULL,NULL,?,1)", (entry, kind, "a" * 64)
                )
    before = sha256(path)
    data = import_copy_inventory(path, tmp_path / "raw", tmp_path / "manifest.json")
    assert sha256(path) == before
    return data


def test_import_keeps_problem_and_empty_cores_visible(tmp_path):
    base = "project/slide/slide"
    data = inventory(
        tmp_path,
        [
            f"{base}/A1/middle lamp 275/stitch/FOV_1.tif",
            f"{base}/A2/middle lamp 275/scan/1_2/100.tif",
            f"{base}/A2/middle lamp 275/scan/1_2/101.tif",
            f"{base}/A3/middle lamp 275/stitch/FOV_1.tif",
            f"{base}/A3/middle lamp 275/stitch/FOV_3.tif",
            f"{base}/A5/middle lamp 275/stitch_folder_bg/FOV_1.tif",
        ],
        [f"{base}/A1", f"{base}/A4", f"{base}/A4/middle lamp 275"],
    )
    cores = {c["core"]: c for c in data["cores"]}
    assert set(cores) == {"A1", "A2", "A3", "A4", "A5"}
    assert not cores["A1"]["blocked_reasons"]
    for core in ["A2", "A3", "A4", "A5"]:
        assert cores[core]["blocked_reasons"]
        assert cores[core]["fovs"] == []
    assert "Z/exposure" in cores["A2"]["blocked_reasons"][0]
    assert "Missing FOV" in cores["A3"]["blocked_reasons"][0]
    assert data["metadata"]["import_coverage"]["discovered_core_acquisitions"] == 5
    assert data["metadata"]["import_coverage"]["blocked"] == 4
    assert len(Manifest(tmp_path / "manifest.json").cores) == 5


def test_import_orders_unpadded_fov_numbers_numerically(tmp_path):
    data = inventory(
        tmp_path,
        [f"project/slide/slide/A1/middle lamp 275/stitch/FOV_{i}.tif" for i in range(12, 0, -1)],
    )
    assert [f["id"] for f in data["cores"][0]["fovs"]] == [str(i) for i in range(1, 13)]


def test_import_preserves_cores_under_slide_names_with_a_leading_underscore(tmp_path):
    data = inventory(tmp_path, ["project/_slide/_slide/A1/middle lamp 275/stitch/FOV_1.tif"])
    assert len(data["cores"]) == 1
    assert len(Manifest(tmp_path / "manifest.json").cores) == 1


def test_all_blocked_inventory_still_produces_a_report_and_blocked_exit_code(tmp_path):
    inventory(
        tmp_path, [f"project/slide/slide/A1/middle lamp 275/scan/1_2/{i}.tif" for i in [100, 101]]
    )
    status = main(
        ["run", "--manifest", str(tmp_path / "manifest.json"), "--out", str(tmp_path / "results")]
    )
    assert status == 2
    assert "1 blocked at input" in (tmp_path / "results/reports/index.html").read_text()
    assert read_json(tmp_path / "results/reports/coverage.json")["manifest_core_acquisitions"] == 1


def test_scan_import_preserves_xy_identity_and_requires_sequence_review(tmp_path):
    data = inventory(
        tmp_path,
        [f"project/slide/slide/A1/middle lamp 275/scan/{p}/100.tif" for p in ["9_2", "10_2"]],
    )
    core = data["cores"][0]
    assert not core["blocked_reasons"]
    assert core["metadata"]["sequence_reviewed"] is False
    assert {f["metadata"]["stage_xy_folder"] for f in core["fovs"]} == {"9_2", "10_2"}


def legacy_core(
    tmp_path,
    matrix="0.85 0 0\n0 0.85 0\n",
    evaluation="postcrop_dice: 0.95\nlocal_phase_mean_magnitude: 1\n",
):
    core = tmp_path / "slide" / "A1"
    reg = core / "Registration"
    reg.mkdir(parents=True)
    write_tiff(reg / "A1_AF_Reg.tif", np.full((8, 8), 100, np.uint16))
    write_tiff(reg / "A1_Stain_Reg.tif", np.full((8, 8, 3), 200, np.uint8), photometric="rgb")
    (reg / "matrix.txt").write_text(matrix)
    (reg / "postcrop_eval_result.txt").write_text(evaluation)
    (reg / "registration_log.csv").write_text("good_matches\n100\n")
    return core


@pytest.mark.parametrize(
    "matrix",
    [
        "nan 0 0\n0 nan 0\n",
        "0.85 0 inf\n0 0.85 0\n",
        "0.85 0 0\n0 0 0\n",
        "0.85 0 0\n",
        "0.85 0 0\n0 0.85 0 1\n",
        "0.85 0 0\n0 0.95 0\n",
        "0.85 0 0\n0 0.85 0\n0 0 2\n",
    ],
)
def test_legacy_invalid_transforms_never_report_ok(tmp_path, matrix):
    settings = load_settings(Path(__file__).resolve().parents[2] / "settings.toml")
    row = check_core_safely(legacy_core(tmp_path, matrix=matrix), settings)
    assert row["status"] == "ALERT" and row["problems"]


@pytest.mark.parametrize(
    "dice,residual",
    [
        ("nan", "1"),
        ("0.95", "nan"),
        ("inf", "1"),
        ("1e309", "1"),
        ("garbage", "1"),
        (None, "1"),
        ("0.95", None),
        ("0.95invalid", "1"),
        ("-1", "1"),
        ("0.95%", "1"),
        ("0.95", "1%"),
    ],
)
def test_legacy_missing_or_invalid_evaluation_is_not_ok(tmp_path, dice, residual):
    evaluation = "" if dice is None else f"postcrop_dice: {dice}\n"
    if residual is not None:
        evaluation += f"local_phase_mean_magnitude: {residual}\n"
    settings = load_settings(Path(__file__).resolve().parents[2] / "settings.toml")
    row = check_core_safely(legacy_core(tmp_path, evaluation=evaluation), settings)
    assert row["status"] != "OK" and row["problems"]


def test_raw_shape_is_checked_before_decoding_a_compressed_wrong_input(tmp_path, monkeypatch):
    path = tmp_path / "FOV_1.tif"
    tifffile.imwrite(path, np.ones((1024, 1024), np.uint16), compression="deflate")
    write_json(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "dataset_id": "shape",
            "raw_root": str(tmp_path),
            "groups": [{"id": "group"}],
            "cores": [
                {
                    "uid": "core",
                    "acquisition": "acq",
                    "slide": "slide",
                    "core": "A1",
                    "channel": "AF",
                    "group": "group",
                    "fovs": [{"id": "1", "path": path.name, "sha256": sha256(path)}],
                }
            ],
            "pairs": [],
        },
    )

    def no_decode(*args, **kwargs):
        pytest.fail("Wrong-shape raw input should fail on its header")

    monkeypatch.setattr("tma_qc.qc.integrity.read_image", no_decode)
    manifest = Manifest(tmp_path / "manifest.json")
    with pytest.raises(ValueError, match="expected"):
        verify_core(manifest.cores[0], manifest, {"shape": [8, 8], "dtype": "uint16"})


@pytest.mark.parametrize("alias", ["paths", "contents", None])
def test_cidre_pool_requires_independent_sources(tmp_path, monkeypatch, alias):
    raw = tmp_path / "raw"
    cores, pool = [], []
    for k in range(4):
        frames = []
        for i in range(16):
            path = raw / ("core0" if alias == "paths" else f"core{k}") / f"FOV_{i}.tif"
            if not path.exists():
                values = np.full((8, 8), 100 + i + (0 if alias else 16 * k), np.uint16)
                write_tiff(path, values)
            frames.append({"id": str(i), "path": str(path), "sha256": sha256(path)})
            pool.append({"core_uid": f"core{k}", "fov_id": str(i)})
        cores.append(
            {
                "uid": f"core{k}",
                "acquisition": "acquisition",
                "slide": "slide",
                "core": f"A{k + 1}",
                "channel": "AF",
                "group": "group",
                "fovs": frames,
            }
        )
    write_json(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "dataset_id": "pool",
            "raw_root": str(raw),
            "groups": [{"id": "group"}],
            "cores": cores,
            "pairs": [],
        },
    )
    write_json(tmp_path / "pool.json", pool)
    calls = []

    def fitting(inputs, output, runtime):
        calls.append(inputs)
        return {"quality_controls": "not_passed"}

    monkeypatch.setattr("tma_qc.backends.cidre.fit_model", fitting)
    status = main(
        [
            "fit-cidre",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--pool",
            str(tmp_path / "pool.json"),
            "--group",
            "group",
            "--runtime",
            str(tmp_path / "runtime"),
            "--out",
            str(tmp_path / "model"),
        ]
    )
    if alias:
        assert status == 1 and not calls
    else:
        assert status == 0 and len(calls[0]) == len(set(calls[0])) == 64
        assert len(read_json(tmp_path / "model/model.json")["verified_pool"]) == 64


@pytest.mark.parametrize("compression", [None, "deflate"])
def test_bigendian_raw_is_verified_and_corrected_without_changing_counts(tmp_path, compression):
    path = tmp_path / "FOV_1.tif"
    image = np.arange(64, dtype=np.uint16).reshape(8, 8) * 100
    tifffile.imwrite(path, image, byteorder=">", compression=compression)
    write_json(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "dataset_id": "endian",
            "raw_root": str(tmp_path),
            "groups": [{"id": "group"}],
            "cores": [
                {
                    "uid": "core",
                    "acquisition": "acq",
                    "slide": "slide",
                    "core": "A1",
                    "channel": "AF",
                    "group": "group",
                    "fovs": [{"id": "1", "path": path.name, "sha256": sha256(path)}],
                }
            ],
            "pairs": [],
        },
    )
    manifest = Manifest(tmp_path / "manifest.json")
    verified, _ = verify_core(manifest.cores[0], manifest, {"shape": [8, 8], "dtype": "uint16"})
    assert verified["verified_files"] == 1
    np.testing.assert_array_equal(read_image(path), image)
    np.testing.assert_array_equal(
        apply_field(image.astype(">u2"), np.ones((2, 2)), np.zeros((2, 2))), image
    )
