"""Controlled numerical failures and abstention; no biological acceptance rules."""

import json

import numpy as np
import pytest

from tma_qc.qc.boundaries import evaluate_boundaries
from tma_qc.qc.illumination import evaluate_correction
from tma_qc.qc.periodicity import evaluate_periodicity
from tma_qc.qc.stitching import evaluate_overlaps


def overlap_arrays(a, b):
    images = {"a": a, "b": b}
    return evaluate_overlaps(
        {key: key for key in images},
        [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 0, "y": 1}],
        reader=images.__getitem__,
    )


def test_phase_translation_is_invariant_to_a_large_representable_dc_offset():
    texture = np.random.default_rng(7).integers(-5, 6, (128, 128))
    edges = []
    for baseline in (100, 30000):
        a = (texture + baseline).astype(np.uint16)
        b = np.roll(a, (3, 4), (0, 1))
        report = overlap_arrays(a, b)
        edges.append(report["edges"][0])
        assert report["geometry_assessed_pairs"] == report["geometry_candidate_pairs"] == 1
        assert report["geometry_assessed_pair_fraction"] == 1
        assert report["geometry_exclusions_by_reason"] == {}
    # Cropping the overlap starts a at native y=1 and b at y=0, adding one
    # pixel to the deliberately introduced three-pixel vertical displacement.
    assert edges[0]["phase_residual_native_pixels"] == pytest.approx([4, 4], abs=0.05)
    assert edges[1]["phase_residual_native_pixels"] == pytest.approx(
        edges[0]["phase_residual_native_pixels"], abs=1e-10
    )
    assert edges[1]["phase_response"] == pytest.approx(edges[0]["phase_response"], abs=1e-10)
    assert edges[0]["directional_texture_rank"] == {"a": 2, "b": 2}
    for edge in edges:
        probe = edge["translation_symmetry_probe"]
        assert probe["performed"] and probe["radius_sample_pixels"] == 2
        assert probe["sampling_step_native_pixels"] == 1
        assert probe["shift_sample_xy"] == {"a": None, "b": None}


@pytest.mark.parametrize("degenerate_side", ["both", "a", "b"])
def test_oblique_exact_translation_symmetry_cannot_supply_a_full_phase_vector(degenerate_side):
    y, x = np.mgrid[:128, :128]
    a = (3000 + 100 * ((x + 2 * y) % 32)).astype(np.uint16)
    b = (3000 + 100 * ((x + 2 * (y + 1)) % 32)).astype(np.uint16)
    # A shift (-2,+1) leaves this sampled pattern unchanged, even though its
    # non-collocated forward differences have numerical rank two.
    assert np.array_equal(a[:-1, 2:], a[1:, :-2])
    random = np.random.default_rng(51).integers(2000, 6000, a.shape, dtype=np.uint16)
    if degenerate_side == "a":
        b = random
    elif degenerate_side == "b":
        a = random
    result = overlap_arrays(a, b)
    edge = result["edges"][0]
    assert edge["directional_texture_rank"] == {"a": 2, "b": 2}
    assert edge["phase_residual_native_pixels"] is edge["phase_response"] is None
    assert edge["geometry_exclusion_reason"] == "translation_texture_degenerate"
    probe = edge["translation_symmetry_probe"]
    assert probe["performed"] and probe["radius_sample_pixels"] == 2
    assert probe["sampling_step_native_pixels"] == 1
    for side in ("a", "b"):
        expected = [-2, 1] if degenerate_side in {"both", side} else None
        assert probe["shift_sample_xy"][side] == expected
    assert result["geometry_assessed_pairs"] == 0
    assert result["geometry_exclusions_by_reason"] == {"translation_texture_degenerate": 1}
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("direction", ["x", "y", "diagonal"])
def test_one_directional_texture_cannot_measure_a_full_translation_vector(direction):
    y, x = np.mgrid[:128, :128]
    signal = x if direction == "x" else y if direction == "y" else x + y
    a = (3000 + signal).astype(np.uint16)
    report = overlap_arrays(a, a.copy())
    edge = report["edges"][0]
    assert edge["pearson"] == pytest.approx(1)
    assert edge["directional_texture_rank"] == {"a": 1, "b": 1}
    assert edge["phase_residual_native_pixels"] is edge["phase_response"] is None
    assert edge["geometry_exclusion_reason"] == "directional_texture_degenerate"
    assert report["geometry_assessed_pairs"] == 0
    assert report["geometry_assessed_pair_fraction"] == 0
    assert report["geometry_exclusions_by_reason"] == {"directional_texture_degenerate": 1}
    assert report["geometry_assessment"] == "not_assessed"


@pytest.mark.parametrize("scale", [1e-20, 1e-12, 1.0, 1e15])
def test_projection_fraction_is_invariant_to_signal_count_units(scale):
    pitch, width, height = 64, 256, 64
    raw = (
        np.broadcast_to(3000 + 100 * np.cos(2 * np.pi * np.arange(width) / pitch), (height, width))
        .astype(np.float32)
        .copy()
    )
    raw[-4:] = 0
    positions = [{"id": str(i), "x": i * pitch, "y": 0} for i in range(4)]
    result = evaluate_periodicity(
        raw, raw * scale, np.ones(raw.shape, np.uint8), positions, (height, 96)
    )
    measured = result["measurements"][0]
    assert measured["assessment"] == "review"
    assert measured["corrected"]["harmonic_fraction_of_detrended_projection"] == pytest.approx(
        measured["raw"]["harmonic_fraction_of_detrended_projection"], abs=1e-8
    )
    assert measured["corrected"]["harmonic_amplitudes_counts"][0] == pytest.approx(
        measured["raw"]["harmonic_amplitudes_counts"][0] * scale, rel=1e-5
    )
    json.dumps(result, allow_nan=False)


def test_low_signal_boundary_evidence_is_available_without_becoming_confirmed_background():
    raw = np.full((64, 96), 3000, np.float32)
    corrected = raw.copy()
    corrected[:, 48:] += 1000
    report = evaluate_boundaries(
        raw,
        corrected,
        np.ones(raw.shape, np.uint8),
        [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 32, "y": 0}],
        (64, 64),
    )
    assert report["assessment"] == "review"
    assert report["assessed_seams"] == 1
    assert report["measurement_counts"] == {
        "raw_supported": 0,
        "low_signal": 1,
        "mixed_or_unknown": 0,
    }
    assert report["summaries"]["low_signal"]["corrected"]["maximum"] == 1000
    assert report["grid_probability"] is report["artifact_area_fraction"] is None
    assert report["rules_calibrated"] is False


def test_nonoverlapping_tiles_do_not_claim_geometry_photometry_or_pitch_measurements():
    image = np.arange(64 * 64, dtype=np.uint16).reshape(64, 64)
    images = {"a": image, "b": image.copy()}
    frames = {key: key for key in images}
    positions = [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 64, "y": 0}]
    geometry = evaluate_overlaps(frames, positions, reader=images.__getitem__)
    assert geometry["geometry_candidate_pairs"] == geometry["geometry_assessed_pairs"] == 0
    assert geometry["geometry_assessed_pair_fraction"] is None
    assert geometry["geometric_components"] == 2
    photometry = evaluate_correction(frames, frames, positions, reader=images.__getitem__)
    assert photometry["candidate_pairs"] == photometry["assessed_pairs"] == 0
    assert photometry["assessed_pair_fraction"] is None
    assert photometry["assessment"] == "not_assessed"
    mosaic = np.concatenate([image, image], axis=1).astype(np.float32)
    periodicity = evaluate_periodicity(
        mosaic, mosaic, np.ones(mosaic.shape, np.uint8), positions, image.shape
    )
    assert periodicity["assessment"] == "not_assessed"
    assert all("overlapping tile" in row["reason"] for row in periodicity["measurements"])
