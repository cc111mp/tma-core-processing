"""Adversarial mosaic-QC evidence, without calibrated biological acceptance rules."""

import json

import numpy as np
import pytest

from tma_qc.qc.boundaries import evaluate_boundaries
from tma_qc.qc.periodicity import evaluate_periodicity


@pytest.mark.parametrize("affected_lines", [1, 48])
def test_localized_seam_survives_the_median_summary(affected_lines):
    raw = np.broadcast_to(3000 + np.arange(256, dtype=np.float32)[:, None], (256, 384)).copy()
    corrected = raw.copy()
    corrected[64 : 64 + affected_lines, 192:] += 2000
    positions = [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 128, "y": 0}]
    result = evaluate_boundaries(
        raw, corrected, np.ones(raw.shape, np.uint8), positions, (256, 256)
    )
    summary = result["summaries"]["raw_supported"]["corrected"]
    # The original median contract remains available, with explicit tails added.
    assert summary["median"] == summary["maximum"] == 0
    assert summary["per_seam_maximum"]["maximum"] > 1
    assert (summary["per_seam_p95"]["maximum"] > 1) == (affected_lines == 48)
    worst = result["worst_seams"]["raw_supported"]["corrected"][0]
    assert (worst["a"], worst["b"], worst["axis"], worst["cut_native_pixel"]) == (
        "a",
        "b",
        "x",
        192,
    )
    assert worst["maximum_step_line_native_pixel"] == 64
    assert result["rules_calibrated"] is False
    json.dumps(result, allow_nan=False)


def test_boundary_unknown_support_never_becomes_a_worst_seam():
    raw = np.full((64, 96), 3000, np.float32)
    coverage = np.ones(raw.shape, np.uint8)
    coverage[:, 40:56] = 0
    result = evaluate_boundaries(
        raw,
        raw,
        coverage,
        [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 32, "y": 0}],
        (64, 64),
    )
    assert result["assessment"] == "not_assessed"
    assert result["worst_seams"]["raw_supported"]["corrected"] == []
    assert result["summaries"]["raw_supported"]["corrected"]["per_seam_maximum"]["count"] == 0


def test_fine_texture_cannot_alias_into_a_tile_pitch_fit():
    height, width, pitch = 128, 4224, 384
    x = np.arange(width)
    former_stride = int(np.ceil(width / 1024))
    texture = (3000 + 200 * np.cos(2 * np.pi * x * (1 / former_stride + 1 / pitch))).astype(
        np.float32
    )
    raw = np.broadcast_to(texture, (height, width)).copy()
    raw[-4:] = 0
    positions = [{"id": str(i), "x": pitch * i, "y": 0} for i in range(10)]
    fits = []
    for size in (512, 1024, 2048):
        result = evaluate_periodicity(
            raw, raw, np.ones(raw.shape, np.uint8), positions, (height, 768), size=size
        )
        row = result["measurements"][0]
        assert row["assessment"] == "review"
        assert row["total_bins"] == width
        assert len(row["coordinate_native_pixels"]) <= size
        assert result["sampling_step_native_pixels"] == 1
        fits.append(row["raw"])
        # Previously the 4.94-pixel texture gave a fraction near 1 and amplitude 200.
        assert row["raw"]["harmonic_fraction_of_detrended_projection"] < 1e-5
        assert max(row["raw"]["harmonic_amplitudes_counts"]) < 0.1
        json.dumps(result, allow_nan=False)
    assert fits[0] == fits[1] == fits[2]


@pytest.mark.parametrize("axis", ["x", "y"])
def test_opposite_phase_regions_have_local_evidence_even_when_global_pattern_cancels(axis):
    height = width = 480
    oscillation = (400 * np.cos(2 * np.pi * np.arange(width) / 96)).astype(np.float32)
    raw = np.full((height, width), 3000, np.float32)
    raw[:232] += oscillation
    raw[232:464] -= oscillation
    raw[464:] = 0
    if axis == "y":
        raw = raw.T
    positions = [
        {"id": str(y * 4 + x), "x": x * 96, "y": y * 96} for y in range(4) for x in range(4)
    ]
    result = evaluate_periodicity(raw, raw, np.ones(raw.shape, np.uint8), positions, (192, 192))
    global_row = next(
        r for r in result["measurements"] if r["axis"] == axis and r["stratum"] == "raw_supported"
    )
    assert global_row["raw"]["harmonic_amplitudes_counts"][0] < 0.01
    strongest = next(
        r
        for r in result["strongest_local_patterns"]
        if r["axis"] == axis and r["stratum"] == "raw_supported" and r["image"] == "raw"
    )
    assert strongest["fit"]["harmonic_amplitudes_counts"][0] == pytest.approx(400, rel=1e-4)
    assert strongest["fit"]["harmonic_fraction_of_detrended_projection"] > 0.99
    assert strongest["region_bbox_xyxy"] == ([0, 0, 480, 120] if axis == "x" else [0, 0, 120, 480])
    assert result["grid_probability"] is result["artifact_area_fraction"] is None


def test_native_missing_bins_are_excluded_and_preview_never_bridges_a_gap():
    height, width, pitch = 64, 1025, 128
    raw = np.broadcast_to(
        3000 + 100 * np.cos(2 * np.pi * np.arange(width) / pitch), (height, width)
    ).copy()
    raw[-4:] = 0
    corrected = raw.copy()
    coverage = np.ones(raw.shape, np.uint8)
    coverage[:, 201] = 0
    corrected[:, 202] = np.nan
    positions = [{"id": str(i), "x": pitch * i, "y": 0} for i in range(8)]
    result = evaluate_periodicity(raw, corrected, coverage, positions, (height, 256), size=256)
    row = result["measurements"][0]
    # Five native columns per display bin; both absent columns lie in bin 40.
    assert row["preview_bin_width_native_pixels"] == 5
    assert row["supported_bins"] == width - 2
    assert row["raw_profile_counts"][40] is row["corrected_profile_counts"][40] is None
    assert row["raw"]["harmonic_amplitudes_counts"][0] == pytest.approx(100, rel=1e-4)
    assert row["raw"] == row["corrected"]
    json.dumps(result, allow_nan=False)


def test_full_native_scan_uses_bounded_source_patches():
    class GuardedImage:
        """Permit small patches and the explicitly bounded threshold sample only."""

        ndim = 2

        def __init__(self, image):
            self.image, self.shape = image, image.shape

        def __getitem__(self, selection):
            view = self.image[selection]
            if any((s.step or 1) > 1 for s in selection):
                assert max(view.shape) <= 1024
            else:
                assert max(view.shape) <= 512
            return view

    raw = np.full((1025, 2050), 3000, np.float32)
    raw[-100:] = 0
    source = GuardedImage(raw)
    coverage = GuardedImage(np.ones(raw.shape, np.uint8))
    positions = [{"id": str(i), "x": i * 256, "y": 0} for i in range(7)]
    result = evaluate_periodicity(source, source, coverage, positions, (1025, 512))
    assert result["assessment"] == "review"
    assert result["working_patch_shape_yx"] == [512, 512]
    assert len(result["measurements"]) == 4
    assert len(result["local_measurements"]) == 16
