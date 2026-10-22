"""Finite correction remains available when a valid field has extreme model gain."""

import numpy as np
import pytest

from tests.integration.test_pipeline import fixture, rows
from tma_qc.io.coordinates import position_document
from tma_qc.io.images import read_image, write_tiff
from tma_qc.pipeline import run_pipeline
from tma_qc.provenance import read_json, sha256, write_json


def test_subnormal_flatfield_diagnostics_do_not_block_finite_native_correction(tmp_path):
    manifest, config, output = fixture(tmp_path)
    data = read_json(manifest)
    core = data["cores"][0]
    core["fovs"] = core["fovs"][:1]
    data["cores"], data["pairs"] = [core], []
    raw = read_image(manifest.parent / data["raw_root"] / core["fovs"][0]["path"])
    tiny = np.nextafter(np.float32(0), np.float32(1))
    flatfield = manifest.parent / data["groups"][0]["flatfield"]
    darkfield = manifest.parent / data["groups"][0]["darkfield"]
    write_tiff(flatfield, np.full(raw.shape, tiny, np.float32))
    # The unusual but valid model cancels varying raw counts exactly. Correction
    # is finite; QC should flag its collapsed signal instead of losing the image.
    write_tiff(darkfield, raw.astype(np.float32))
    write_json(
        manifest.parent / data["groups"][0]["model_metadata"],
        {"flatfield_sha256": sha256(flatfield), "darkfield_sha256": sha256(darkfield)},
    )
    write_json(
        manifest.parent / core["positions"],
        position_document([{"id": core["fovs"][0]["id"], "x": 0, "y": 0}]),
    )
    write_json(manifest, data)

    summary = run_pipeline(manifest, output, config)
    assert summary["execution_errors"] == summary["blocked_stages"] == 0
    stages = rows(summary)
    correction = stages[core["uid"], "illumination"]
    assert correction["execution"] == "complete"
    assert correction["metrics"]["gain_p01_p50_p99"] == pytest.approx([1 / float(tiny)] * 3)
    assert np.isfinite(correction["metrics"]["gain_p01_p50_p99"]).all()
    mosaic = read_image(output / stages[core["uid"], "mosaic"]["outputs"]["mosaic"]["path"])
    assert mosaic.dtype == np.float32 and np.all(mosaic == 0)
    quality = stages[core["uid"], "illumination_qc"]
    assert quality["execution"] == "complete" and quality["qc"] == "review"
    assert quality["metrics"]["signal_diagnostics"]["collapsed_fov_ids"] == ["1"]
