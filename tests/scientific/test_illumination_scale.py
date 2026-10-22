"""Signal-scale and eligibility controls, without biological acceptance rules."""

import numpy as np
import pytest

from tma_qc.qc.illumination import evaluate_correction


def evaluate_arrays(a, b, selected_a=None, selected_b=None):
    images = {
        "raw-a": a,
        "raw-b": b,
        "selected-a": a if selected_a is None else selected_a,
        "selected-b": b if selected_b is None else selected_b,
    }
    positions = [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": a.shape[1] // 2, "y": 0}]
    return evaluate_correction(
        {"a": "raw-a", "b": "raw-b"},
        {"a": "selected-a", "b": "selected-b"},
        positions,
        reader=images.__getitem__,
    )


def overlapping_scene():
    scene = np.random.default_rng(73).integers(3000, 15000, (64, 96), dtype=np.uint16)
    return scene[:, :64], (scene[:, 32:].astype(np.float32) * 1.15).astype(np.uint16)


@pytest.mark.parametrize("scale", [1.0, 1e-3, 1e-6])
def test_uniform_compression_does_not_improve_relative_overlap_mismatch(scale):
    a, b = overlapping_scene()
    report = evaluate_arrays(a, b, a.astype(np.float32) * scale, b.astype(np.float32) * scale)
    assert report["candidate_pairs"] == report["assessed_pairs"] == 1
    assert report["assessed_pair_fraction"] == 1
    assert report["exclusions_by_reason"] == {}
    assert report["corrected_overlap_relative_difference_median"] == pytest.approx(
        report["raw_overlap_relative_difference_median"], rel=1e-6
    )
    assert report["relative_difference_assessed_pairs"] == {"raw": 1, "corrected": 1, "paired": 1}
    signal = report["signal_diagnostics"]
    assert signal["span_ratio_minimum_median_maximum"] == pytest.approx([scale] * 3, rel=1e-6)
    assert signal["collapsed_fov_ids"] == []
    assert not report["absolute_intensity_preservation_validated"]


@pytest.mark.parametrize("constant", [0.0, 20.0])
def test_constant_corrected_signal_is_flagged_and_zero_means_have_no_relative_scale(constant):
    a, b = overlapping_scene()
    report = evaluate_arrays(a, b, np.full(a.shape, constant), np.full(b.shape, constant))
    assert report["assessment"] == "review"
    assert report["signal_diagnostics"]["collapsed_fov_ids"] == ["a", "b"]
    assert report["matched_pixel_p95_assessed_pairs"] == 1
    assert report["corrected_matched_pixel_p95_difference_counts_median"] == 0
    expected = {"raw": 1, "corrected": int(constant != 0), "paired": int(constant != 0)}
    assert report["relative_difference_assessed_pairs"] == expected
    if constant == 0:
        assert report["corrected_overlap_relative_difference_median"] is None
        assert report["compared_pairs"][0]["change_corrected_minus_raw"] is None


def test_constant_samples_do_not_become_a_false_native_signal_collapse_flag():
    scene = np.random.default_rng(74).integers(2000, 12000, (1024, 1536), dtype=np.uint16)
    a, b = scene[:, :1024], scene[:, 512:]
    selected = np.zeros(a.shape, np.float32)
    selected[1, 1] = 1  # Outside the four-pixel sampling grid.
    report = evaluate_arrays(a, b, selected, selected)
    signal = report["signal_diagnostics"]
    assert signal["collapsed_fov_ids"] == []
    assert signal["span_ratio_minimum_median_maximum"] == [0, 0, 0]
    for row in signal["fovs"]:
        assert row["sampling_step_native_pixels"] == 4
        assert row["corrected_maximum_counts"] == 1


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("sparse", "insufficient_raw_support"),
        ("constant_overlap", "raw_correlation_unavailable"),
        ("uncorrelated", "raw_correlation_below_selection"),
    ],
)
def test_raw_exclusion_reasons_stay_frozen_on_the_corrected_side(kind, reason):
    rng = np.random.default_rng(75)
    if kind == "sparse":
        a = rng.integers(3000, 15000, (8, 8), dtype=np.uint16)
        b = rng.integers(3000, 15000, (8, 8), dtype=np.uint16)
    elif kind == "constant_overlap":
        scene = np.full((64, 96), 100, np.uint16)
        scene[:, 32:64] = 1000
        a, b = scene[:, :64], scene[:, 32:]
    else:
        a = rng.integers(3000, 15000, (64, 64), dtype=np.uint16)
        b = rng.integers(3000, 15000, (64, 64), dtype=np.uint16)
    corrected_scene = rng.random((a.shape[0], a.shape[1] * 3 // 2)).astype(np.float32) * 10000
    report = evaluate_arrays(
        a, b, corrected_scene[:, : a.shape[1]], corrected_scene[:, a.shape[1] // 2 :]
    )
    assert report["candidate_pairs"] == report["excluded_pairs"] == 1
    assert report["assessed_pairs"] == 0
    assert report["assessed_pair_fraction"] == 0
    assert report["assessment"] == "not_assessed"
    assert report["exclusions_by_reason"] == {reason: 1}
    for side in ("raw", "corrected"):
        assert report[side]["edges"][0]["photometric_exclusion_reason"] == reason
        assert report[side]["edges"][0]["photometric_assessment"] == "not_assessed"


def test_single_fov_has_no_overlap_coverage_denominator():
    image = np.arange(4096, dtype=np.uint16).reshape(64, 64)
    frames = {"a": "raw-a"}
    report = evaluate_correction(
        frames, frames, [{"id": "a", "x": 0, "y": 0}], reader=lambda _: image
    )
    assert report["candidate_pairs"] == report["assessed_pairs"] == report["excluded_pairs"] == 0
    assert report["assessed_pair_fraction"] is None
    assert report["assessment"] == "not_assessed"
    assert report["signal_diagnostics"]["assessed_fovs"] == 1
