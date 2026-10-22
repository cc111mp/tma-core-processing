"""A sparse native acquisition is not empty because a preview grid misses it."""

import numpy as np

from tests.integration.test_pipeline import fixture, rows
from tma_qc.io.coordinates import position_document
from tma_qc.io.images import read_image, write_tiff
from tma_qc.pipeline import run_pipeline
from tma_qc.provenance import read_json, sha256, write_json


def test_sparse_valid_native_coverage_completes_qc_and_preserves_unknown_support(tmp_path):
    manifest, config, output = fixture(tmp_path)
    data = read_json(manifest)
    core = data["cores"][0]
    core["fovs"] = core["fovs"][:3]
    data["cores"], data["pairs"] = [core], []
    poses = []
    for fov, x in zip(core["fovs"], (1, 2, 9992)):
        path = manifest.parent / data["raw_root"] / fov["path"]
        write_tiff(path, (3000 + np.arange(64)).reshape(8, 8).astype(np.uint16))
        fov["sha256"] = sha256(path)
        poses.append({"id": fov["id"], "x": x, "y": 0})
    write_json(manifest.parent / core["positions"], position_document(poses))
    write_json(manifest, data)
    config.write_text(
        "[profile]\nshape = [8, 8]\n[tile_qc]\nwindow = 8\nstride = 8\n"
        f'[runtime]\ncache_root = "{tmp_path / "cache"}"\n'
    )

    summary = run_pipeline(manifest, output, config)
    assert summary["execution_errors"] == summary["blocked_stages"] == 0
    stages = rows(summary)
    mosaic = stages[core["uid"], "mosaic"]
    coverage = read_image(output / mosaic["outputs"]["coverage"]["path"])
    assert coverage.shape == (8, 10000) and int(coverage.sum()) == 136
    review = stages[core["uid"], "mosaic_qc"]
    assert review["metrics"]["preview_grid_missed_acquired_pixels"] is True
    for key in ("boundaries", "periodicity"):
        record = read_json(output / review["outputs"][key]["path"])
        assert record["raw_reference_sampling"] == "native_coverage_fallback"
        assert record["raw_reference_samples"] == 136
    tissue = stages[core["uid"], "tissue"]
    candidate = read_image(output / tissue["outputs"]["candidate"]["path"])
    unknown = read_image(output / tissue["outputs"]["unknown"]["path"])
    assert not candidate.any()
    assert np.array_equal(unknown, coverage)
    assert tissue["metrics"]["coarse_grid_acquired_samples"] == 0
    assert any("all native acquisition remains unknown" in line for line in tissue["findings"])
