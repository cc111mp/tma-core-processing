"""Native diagnostic evidence and stage availability summaries must agree."""

from types import SimpleNamespace

import numpy as np

from tma_qc.config import load_config
from tma_qc.io.images import write_tiff
from tma_qc.provenance import read_json
from tma_qc.stages import mosaic


def test_local_and_low_signal_measurements_survive_the_stage_summary(tmp_path):
    raw = np.full((256, 384), 3000, np.float32)
    selected = raw.copy()
    selected[:, 192:] += 1000
    paths = {
        "raw": write_tiff(tmp_path / "raw.tif", raw),
        "mosaic": write_tiff(tmp_path / "selected.tif", selected),
        "coverage": write_tiff(tmp_path / "coverage.tif", np.ones(raw.shape, np.uint8)),
    }
    positions = [{"id": str(i), "x": i * 64, "y": 0} for i in range(3)]
    ctx = SimpleNamespace(
        config=load_config(overrides={"profile": {"shape": [256, 256]}}),
        asset=lambda stage, name: paths[name],
        positions=lambda: positions,
    )
    output = tmp_path / "qc"
    output.mkdir()
    payload = mosaic.evaluate(ctx, output)
    boundary = read_json(payload.outputs["boundaries"])
    periodicity = read_json(payload.outputs["periodicity"])
    metrics = payload.metrics
    assert payload.qc == "review"
    assert metrics["boundary_assessment"] == "review"
    assert metrics["boundary_measurement_counts"] == boundary["measurement_counts"]
    assert metrics["boundary_assessed_seams"] == boundary["assessed_seams"] == 3
    assert metrics["boundary_measurement_counts"]["raw_supported"] == 0
    assert metrics["boundary_measurement_counts"]["low_signal"] == 3
    assert metrics["periodicity_assessed_global_measurements"] == sum(
        row["assessment"] == "review" for row in periodicity["measurements"]
    )
    assert metrics["periodicity_assessed_local_measurements"] == sum(
        row["assessment"] == "review" for row in periodicity["local_measurements"]
    )
    assert metrics["periodicity_assessed_local_measurements"] > 0
    assert len(metrics["periodicity_local_measurements"]) == 16
    for compact, full in zip(
        metrics["periodicity_local_measurements"], periodicity["local_measurements"]
    ):
        assert compact == {key: full[key] for key in compact}
        assert compact["scope"] == "strip" and len(compact["region_bbox_xyxy"]) == 4
    assert payload.outputs["local_projection_evidence"].is_file()
