"""Necessary focus-measurement controls, without defocus classification claims."""

import json

import numpy as np
import pytest

from tma_qc.qc.focus import detail_metrics, tile_windows
from tma_qc.stages.tile_qc import _measure


@pytest.mark.parametrize("sigma", [1.0, 5.0, 10.0])
def test_weak_native_detail_is_invariant_to_count_offset_and_contrast(sigma):
    weak = np.zeros((128, 128), np.uint16)
    weak[64, 64] = 1
    reference = detail_metrics(weak, sigma)
    shifted = detail_metrics(weak + 65000, sigma)
    doubled = detail_metrics(weak * 2 + 65000, sigma)
    assert reference["gradient_over_variance"] is not None
    for key in ("gradient_energy", "local_variance", "gradient_over_variance"):
        assert shifted[key] == pytest.approx(reference[key], rel=1e-12)
    for key in ("gradient_energy", "local_variance"):
        assert doubled[key] == pytest.approx(4 * reference[key], rel=1e-12)
    assert doubled["gradient_over_variance"] == pytest.approx(
        reference["gradient_over_variance"], rel=1e-12
    )


@pytest.mark.parametrize("value", [0, 1, 30001, 65535])
def test_constant_dark_or_saturated_patch_has_no_detail_classification(value):
    measured = detail_metrics(np.full((64, 64), value, np.uint16))
    assert measured["gradient_over_variance"] is None
    assert measured["gradient_energy"] == measured["local_variance"] == 0
    assert measured["detail_unavailable_reason"] == "constant_input"
    assert measured["finite_fraction"] == 1
    json.dumps(measured, allow_nan=False)


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_nonfinite_patch_is_explicitly_unavailable_without_filling_missing_pixels(invalid):
    patch = np.arange(32 * 32, dtype=float).reshape(32, 32)
    patch[3, 7] = invalid
    measured = detail_metrics(patch)
    assert measured["detail_unavailable_reason"] == "nonfinite_input"
    assert measured["finite_fraction"] == 1023 / 1024
    for key in (
        "gradient_over_variance",
        "gradient_energy",
        "local_variance",
        "p95_minus_p05",
        "residual_mad",
    ):
        assert measured[key] is None
    json.dumps(measured, allow_nan=False)


def test_weak_filtered_variance_is_unavailable_without_a_defocus_verdict():
    weak = np.zeros((64, 64), float)
    weak[32, 32] = 1e-7
    measured = detail_metrics(weak)
    assert measured["gradient_over_variance"] is None
    assert measured["gradient_energy"] > 0 and measured["local_variance"] > 0
    assert measured["detail_unavailable_reason"] == "insufficient_filtered_variance"


@pytest.mark.parametrize("phase", [0, 3, 7])
def test_native_percentiles_do_not_alias_periodic_content(phase):
    image = np.full((512, 512), 2000, np.uint16)
    image[:, phase::8] = 0
    _, summary, _ = _measure("1", image, {"window": 64, "stride": 96, "prefilter_sigma": 1})
    assert [summary["p01"], summary["p99"]] == pytest.approx(np.percentile(image, [1, 99]))
    assert summary["percentile_sampling"] == "all_native_uint16_pixels"
    assert summary["zero_fraction"] == 1 / 8
    assert summary["saturation_fraction"] == 0


@pytest.mark.parametrize(
    "shape,size,stride",
    [((273, 301), 64, 96), ((273, 301), 64, 64), ((8, 301), 256, 1000), ((32, 48), 256, 256)],
)
def test_sampling_reports_native_window_union_and_distinct_filter_interiors(shape, size, stride):
    image = np.full(shape, 65535, np.uint16)
    rows, summary, _ = _measure(
        "1", image, {"window": size, "stride": stride, "prefilter_sigma": 1}
    )
    covered = np.zeros(shape, bool)
    interiors = np.zeros(shape, bool)
    for row in rows:
        x, y, w, h = (row[k] for k in ("x", "y", "width", "height"))
        covered[y : y + h, x : x + w] = True
        region = row["measurement_region"]
        ix, iy, iw, ih = (region[k] for k in ("x", "y", "width", "height"))
        assert x < ix < ix + iw < x + w
        assert y < iy < iy + ih < y + h
        interiors[iy : iy + ih, ix : ix + iw] = True
    sampling = summary["detail_sampling"]
    assert sampling["sampled_native_pixels"] == np.count_nonzero(covered)
    assert sampling["omitted_native_pixels"] == np.count_nonzero(~covered)
    assert sampling["fraction"] == covered.mean()
    assert sampling["measurement_interior_native_pixels"] == np.count_nonzero(interiors)
    assert sampling["measurement_interior_fraction"] == interiors.mean()
    assert covered[-1, -1] and covered[0, 0]  # End anchoring includes both native edges.
    assert not interiors[-1, -1] and not interiors[0, 0]
    assert summary["detail_assessed_windows"] == 0
    assert summary["detail_unavailable_windows"] == len(rows)
    assert summary["detail_unavailable_by_reason"] == {"constant_input": len(rows)}
    assert summary["saturation_fraction"] == 1


@pytest.mark.parametrize("size,stride", [(0, 1), (7, 1), (8, 0), (8, -1), (8, True)])
def test_invalid_direct_sampling_settings_raise_actionable_errors(size, stride):
    with pytest.raises(ValueError, match="window size.*stride"):
        list(tile_windows(np.zeros((32, 32)), size, stride))
