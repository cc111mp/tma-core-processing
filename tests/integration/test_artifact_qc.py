"""Review semantics, evidence delivery and independent diagnostic reruns."""

import csv

import numpy as np
import pytest

from tests.integration.test_pipeline import Links, fixture, rows
from tma_qc.config import load_config
from tma_qc.pipeline import run_pipeline
from tma_qc.provenance import read_json


def core_row(output, uid="demo-A1"):
    with (output / "reports/core-qc.csv").open(encoding="utf-8-sig") as stream:
        return next(r for r in csv.DictReader(stream) if r["uid"] == uid)


def test_diagnostic_error_is_not_an_image_quality_failure(tmp_path, monkeypatch):
    manifest, config, output = fixture(tmp_path)

    def crash(*args):
        raise RuntimeError("Review diagnostic unavailable")

    monkeypatch.setattr("tma_qc.stages.geometry.evaluate", crash)
    summary = run_pipeline(manifest, output, config)
    stages = rows(summary)
    assert stages["demo-A1", "geometry_qc"]["execution"] == "error"
    assert stages["demo-A1", "geometry_qc"]["qc"] == "not_assessed"
    assert stages["demo-A1", "mosaic"]["execution"] == "complete"
    core = core_row(output)
    assert core["execution"] == "error" and core["qc"] == "review"
    assert core["reconstruction_complete"] == "True"
    assert core["quality_failure_stages"] == ""
    artifacts = read_json(output / "reports/artifact-assessments.json")["records"]
    entry = next(
        r for r in artifacts if r["unit"] == "demo-A1" and r["artifact"] == "tile_alignment"
    )
    assert entry["assessment"] == "not_assessed"


def test_real_quality_failure_remains_visible_without_removing_reconstruction(
    tmp_path, monkeypatch
):
    from tma_qc.stages import tile_qc

    original = tile_qc.run

    def flagged(*args):
        payload = original(*args)
        payload.qc = "fail"
        return payload

    monkeypatch.setattr(tile_qc, "run", flagged)
    manifest, config, output = fixture(tmp_path)
    run_pipeline(manifest, output, config)
    core = core_row(output)
    assert core["execution"] == "complete" and core["qc"] == "fail"
    assert core["reconstruction_complete"] == "True"
    assert core["quality_failure_stages"] == "tile_qc"


def test_review_settings_rerun_diagnostics_without_changing_native_images(tmp_path):
    manifest, config, output = fixture(tmp_path)
    first = rows(run_pipeline(manifest, output, config))
    config.write_text(
        config.read_text() + "\n[artifact_qc]\nevidence_pairs = 2\nboundary_band_pixels = 4\n"
    )
    second = rows(run_pipeline(manifest, output, config))
    for stage in ("geometry", "illumination", "mosaic"):
        assert second["demo-A1", stage]["cached"]
        assert second["demo-A1", stage]["outputs"] == first["demo-A1", stage]["outputs"]
    for stage in ("geometry_qc", "illumination_qc", "mosaic_qc"):
        assert not second["demo-A1", stage]["cached"]
    for page in output.rglob("*.html"):
        links = Links()
        links.feed(page.read_text())
        assert all((page.parent / link).resolve().is_file() for link in links.links)


def test_unreadable_core_has_no_assessed_image_quality(tmp_path):
    manifest, config, output = fixture(tmp_path)
    (manifest.parent / "inputs/B3/FOV_1.tif").write_bytes(b"incomplete transfer")
    run_pipeline(manifest, output, config)
    core = core_row(output, "demo-B3")
    assert core["execution"] == "error" and core["qc"] == "not_assessed"
    assert core["reconstruction_complete"] == "False"


@pytest.mark.parametrize("value", [np.nan, np.inf, True, -1])
def test_invalid_tissue_multiplier_cannot_silently_produce_empty_support(value):
    with pytest.raises(ValueError, match=r"tissue(?:\.mad_multiplier| support)"):
        load_config(base={"tissue": {"mad_multiplier": value}})


@pytest.mark.parametrize(
    "key,value", [("boundary_band_pixels", 0), ("evidence_pairs", True), ("crop_size", 4)]
)
def test_invalid_artifact_qc_settings_rejected(key, value):
    with pytest.raises(ValueError):
        load_config(base={"artifact_qc": {key: value}})
