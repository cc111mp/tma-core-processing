"""Controlled diagnostic responses and abstention, not biological validation."""

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from tma_qc.io.images import write_tiff
from tma_qc.qc.boundaries import evaluate_boundaries
from tma_qc.qc.illumination import evaluate_correction
from tma_qc.qc.periodicity import evaluate_periodicity
from tma_qc.qc.stitching import evaluate_overlaps


def grid(rows=4, columns=4, pitch=96):
    return [
        {"id": str(y * columns + x), "x": x * pitch, "y": y * pitch}
        for y in range(rows)
        for x in range(columns)
    ]


def test_constant_overlaps_are_unknown_and_retain_native_locations(tmp_path):
    frames = {
        s: write_tiff(tmp_path / (s + ".tif"), np.full((64, 64), 100, np.uint16))
        for s in ("a", "b")
    }
    poses = [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 32, "y": 0}]
    geometry = evaluate_overlaps(frames, poses)
    edge = geometry["edges"][0]
    assert edge["overlap_bbox_xyxy"] == [32, 0, 64, 64]
    assert geometry["geometry_assessment"] == edge["geometry_assessment"] == "not_assessed"
    photometry = evaluate_correction(frames, frames, poses)
    assert photometry["assessment"] == "not_assessed"
    assert photometry["worst_corrected_pair"] is None
    assert photometry["raw_supported_pairs"] == 0 and photometry["excluded_pairs"] == 1


def test_one_bad_photometric_overlap_is_not_hidden_by_the_median(tmp_path):
    scene = np.random.default_rng(23).integers(3000, 15000, (64, 224), dtype=np.uint16)
    poses = [{"id": str(i), "x": i * 32, "y": 0} for i in range(6)]
    raw, corrected = {}, {}
    for p in poses:
        tile = scene[:, p["x"] : p["x"] + 64]
        raw[p["id"]] = write_tiff(tmp_path / (p["id"] + ".tif"), tile)
        corrected[p["id"]] = write_tiff(
            tmp_path / (p["id"] + "-corrected.tif"),
            tile.astype(np.float32) * (2 if p["id"] == "5" else 1),
        )
    report = evaluate_correction(raw, corrected, poses)
    assert report["corrected_overlap_relative_difference_median"] == 0
    assert report["corrected_overlap_relative_difference_p95"] > 0.3
    assert report["worst_corrected_pair"]["b"] == "5"
    assert report["worst_corrected_pair"]["corrected_relative_difference"] == pytest.approx(0.5)


def test_overlap_residual_is_reported_in_native_pixels(tmp_path):
    scene = (
        gaussian_filter(np.random.default_rng(9).random((128, 200)), 1).astype(np.float32) * 10000
    )
    frames = {
        "a": write_tiff(tmp_path / "a.tif", scene[:, :128]),
        "b": write_tiff(tmp_path / "b.tif", scene[:, 67:195]),
    }
    report = evaluate_overlaps(frames, [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 64, "y": 0}])
    dx, dy = report["edges"][0]["phase_residual_native_pixels"]
    assert abs(dx) == pytest.approx(3, abs=0.7)
    assert abs(dy) < 0.7


def test_opposite_local_intensity_errors_cannot_cancel_in_the_overlap_mean(tmp_path):
    half = np.random.default_rng(32).integers(4000, 9000, (32, 96), dtype=np.uint16)
    scene = np.vstack([half, half[::-1]])
    poses = [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 32, "y": 0}]
    raw, corrected = {}, {}
    for p in poses:
        tile = scene[:, p["x"] : p["x"] + 64]
        raw[p["id"]] = write_tiff(tmp_path / (p["id"] + ".tif"), tile)
        output = tile.astype(np.float32)
        if p["id"] == "b":
            output[:32] += 1000
            output[32:] -= 1000
        corrected[p["id"]] = write_tiff(tmp_path / (p["id"] + "-corrected.tif"), output)
    report = evaluate_correction(raw, corrected, poses)
    assert report["corrected_overlap_relative_difference_median"] == 0
    assert report["raw_matched_pixel_p95_difference_counts_median"] == 0
    assert report["corrected_matched_pixel_p95_difference_counts_median"] == 1000


def test_boundary_step_uses_identical_support_and_retains_control_cuts():
    raw = np.full((256, 384), 3000, np.float32)
    raw[:, 192:] = 6000
    raw[:64] = 100
    corrected = raw.copy()
    corrected[64:] = 3000
    coverage = np.ones(raw.shape, np.uint8)
    poses = [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 128, "y": 0}]
    report = evaluate_boundaries(raw, corrected, coverage, poses, (256, 256))
    row = report["seams"][0]
    assert row["midline"]["cut_native_pixel"] == 192
    measured = row["midline"]["strata"]["raw_supported"]
    assert measured["raw"]["median_absolute_step_over_raw_range"] > 0.4
    assert measured["corrected"]["median_absolute_step_over_raw_range"] == 0
    assert len(row["nearby_controls"]) == 2
    assert report["artifact_area_fraction"] is report["grid_probability"] is None
    # A missing-acquisition strip must not become an artificial dark seam.
    coverage[:, 184:200] = 0
    excluded = evaluate_boundaries(raw, corrected, coverage, poses, (256, 256))
    assert excluded["assessment"] == "not_assessed"
    assert excluded["seams"][0]["midline"]["strata"]["raw_supported"]["line_samples"] == 0


def test_known_tile_pitch_pattern_changes_without_a_grid_probability():
    y, x = np.mgrid[:416, :416]
    raw = (3000 + 200 * np.cos(2 * np.pi * x / 96)).astype(np.float32)
    corrected = np.full_like(raw, 3000)
    report = evaluate_periodicity(raw, corrected, np.ones(raw.shape, np.uint8), grid(), (128, 128))
    row = next(
        r for r in report["measurements"] if r["axis"] == "x" and r["stratum"] == "raw_supported"
    )
    assert row["assessment"] == "review"
    assert row["raw"]["harmonic_fraction_of_detrended_projection"] > 0.99
    assert row["corrected"]["harmonic_fraction_of_detrended_projection"] == 0
    assert row["raw"]["harmonic_amplitudes_counts"][0] == pytest.approx(200, rel=1e-4)
    assert report["grid_probability"] is None


def test_tissue_occupancy_does_not_become_a_false_projection_pattern():
    y, x = np.mgrid[:416, :416]
    height = 80 + 30 * np.cos(2 * np.pi * x / 96)
    raw = np.where(y < height, 3000, 0).astype(np.float32)
    coverage = np.ones(raw.shape, np.uint8)
    coverage[:, 180:200] = 0
    report = evaluate_periodicity(raw, raw, coverage, grid(), (128, 128))
    row = next(
        r for r in report["measurements"] if r["axis"] == "x" and r["stratum"] == "raw_supported"
    )
    assert row["assessment"] == "review"
    assert row["raw"]["harmonic_fraction_of_detrended_projection"] == 0
    assert row["raw_profile_counts"][180:200] == [None] * 20
    assert all(v == 3000 for v in row["raw_profile_counts"] if v is not None)


def test_single_tile_and_sparse_support_are_not_assessed():
    raw = np.full((128, 128), 100, np.float32)
    result = evaluate_periodicity(
        raw, raw, np.ones(raw.shape, np.uint8), [{"id": "a", "x": 0, "y": 0}], raw.shape
    )
    assert result["assessment"] == "not_assessed"
    assert all(r["raw"] is None for r in result["measurements"])


def test_many_bins_at_one_tile_phase_do_not_establish_a_periodic_fit():
    raw = np.zeros((48, 1280), np.float32)
    raw[:, ::32] = 3000
    poses = [{"id": str(i), "x": 32 * i, "y": 0} for i in range(40)]
    report = evaluate_periodicity(
        raw, raw, np.ones(raw.shape, np.uint8), poses, (48, 48), size=2048
    )
    row = next(
        r for r in report["measurements"] if r["axis"] == "x" and r["stratum"] == "raw_supported"
    )
    assert row["supported_bins"] == 40
    assert row["assessment"] == "not_assessed" and row["raw"] is None
    assert "independent tile phases" in row["reason"]
