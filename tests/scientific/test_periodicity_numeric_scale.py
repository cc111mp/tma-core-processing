"""Numerical DC offsets must not become tile-pitch harmonic evidence."""

import numpy as np
import pytest

from tma_qc.qc.periodicity import evaluate_periodicity


def reference():
    x = np.arange(416)
    signal = 3000 + np.rint(200 * np.cos(2 * np.pi * x / 96))
    raw = np.broadcast_to(signal, (416, 416)).astype(np.float32).copy()
    raw[-16:] = 0
    positions = [
        {"id": str(y * 4 + x), "x": x * 96, "y": y * 96} for y in range(4) for x in range(4)
    ]
    return raw, positions


@pytest.mark.parametrize("constant", [1e9, 1e20])
def test_exact_constant_native_counts_have_no_fitted_harmonics(constant):
    raw, positions = reference()
    result = evaluate_periodicity(
        raw,
        np.full_like(raw, constant),
        np.ones(raw.shape, np.uint8),
        positions,
        (128, 128),
    )
    rows = result["measurements"] + result["local_measurements"]
    assessed = [row for row in rows if row["corrected"] is not None]
    assert assessed
    for row in assessed:
        fit = row["corrected"]
        assert fit["harmonic_fraction_of_detrended_projection"] == 0
        assert fit["harmonic_amplitudes_counts"] == [0, 0, 0]


def test_representable_uniform_offset_preserves_harmonic_evidence():
    raw, positions = reference()
    fits = []
    # Integer counts and these offsets remain exactly representable in float32;
    # this compares the calculation, without conflating pixel quantization.
    for offset in (0, 2**20):
        result = evaluate_periodicity(
            raw,
            raw + np.float32(offset),
            np.ones(raw.shape, np.uint8),
            positions,
            (128, 128),
        )
        fits.append(result["measurements"][0]["corrected"])
    assert fits[0]["harmonic_fraction_of_detrended_projection"] > 0.99
    assert fits[1]["harmonic_fraction_of_detrended_projection"] == pytest.approx(
        fits[0]["harmonic_fraction_of_detrended_projection"], abs=1e-12
    )
    assert fits[1]["harmonic_amplitudes_counts"] == pytest.approx(
        fits[0]["harmonic_amplitudes_counts"], rel=1e-8, abs=1e-8
    )
