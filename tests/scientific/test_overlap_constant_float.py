"""Collapsed finite float32 signal remains measurable without fabricated variance."""

import numpy as np
import pytest

from tma_qc.provenance import read_json, write_json
from tma_qc.qc.illumination import evaluate_correction


@pytest.mark.parametrize("constant", [20.1, 100.1, 1e20])
def test_noninteger_constant_correction_keeps_advisory_evidence_serializable(tmp_path, constant):
    scene = np.random.default_rng(73).integers(3000, 15000, (64, 96), dtype=np.uint16)
    a, b = scene[:, :64], (scene[:, 32:].astype(np.float32) * 1.15).astype(np.uint16)
    selected = np.full(a.shape, constant, np.float32)
    images = {"raw-a": a, "raw-b": b, "selected-a": selected, "selected-b": selected}
    result = evaluate_correction(
        {"a": "raw-a", "b": "raw-b"},
        {"a": "selected-a", "b": "selected-b"},
        [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 32, "y": 0}],
        reader=images.__getitem__,
    )
    path = tmp_path / "illumination-qc.json"
    write_json(path, result)
    saved = read_json(path)
    assert saved["signal_diagnostics"]["collapsed_fov_ids"] == ["a", "b"]
    assert saved["assessment"] == "review"
    assert saved["candidate_pairs"] == saved["assessed_pairs"] == 1
    assert saved["excluded_pairs"] == 0
    assert saved["corrected_overlap_relative_difference_median"] == 0
    edge = saved["corrected"]["edges"][0]
    assert edge["pearson"] is edge["support_pearson"] is None
    assert edge["phase_residual_native_pixels"] is None
    assert edge["raw_supported_evaluation_pair"] is True
    assert edge["photometric_exclusion_reason"] is None
