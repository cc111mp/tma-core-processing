"""Measurement availability must survive aggregation without becoming a defect label."""

from tma_qc.qc.assessment import artifact_assessments, input_assessment, quality_status
from tma_qc.reporting.core import artifact_table, detail_summary, mosaic_summary


def stage(name, metrics, *, execution="complete", qc="review", outputs=None):
    return {
        "stage": name,
        "execution": execution,
        "qc": qc,
        "metrics": metrics,
        "outputs": outputs or {},
    }


def assessment(rows, target):
    return next(row for row in artifact_assessments(rows) if row["artifact"] == target)


def test_locally_assessed_patterns_remain_visible_without_global_fit(tmp_path):
    evidence = {"path": "local-projections.png", "sha256": "unused"}
    rows = [
        stage(
            "mosaic_qc",
            {
                "periodicity_measurements": [{"assessment": "not_assessed"}],
                "periodicity_local_measurements": [
                    {"assessment": "review"},
                    {"assessment": "review"},
                    {"assessment": "not_assessed"},
                ],
            },
            outputs={"local_projection_evidence": evidence},
        )
    ]
    result = assessment(rows, "tile_pitch_pattern")
    assert result["assessment"] == "review"
    assert result["measurement_count"] == 2
    assert result["measurement_breakdown"] == {"global": 0, "local": 2}
    assert result["additional_evidence"]["Local projections"] == evidence
    assert result["artifact_confirmed"] is None and not result["rules_calibrated"]
    text = artifact_table(tmp_path, tmp_path / "index.html", rows)
    assert "local-projections.png" in text and "Global 0 · local 2" in text


def test_low_signal_seams_are_measured_without_becoming_tissue_area():
    rows = [
        stage(
            "mosaic_qc",
            {
                "boundary_assessed_seams": 2,
                "boundary_measurement_counts": {
                    "raw_supported": 0,
                    "low_signal": 2,
                    "mixed_or_unknown": 1,
                },
            },
        )
    ]
    result = assessment(rows, "tile_boundary_steps")
    assert result["assessment"] == "review"
    assert result["measurement_count"] == 2  # Do not double-count shared seams.
    assert result["measurement_unit"] == "distinct seams"
    assert result["affected_tissue_fraction"] is None
    assert result["artifact_confirmed"] is None


def test_historical_seam_summary_retains_low_signal_evidence():
    rows = [
        stage(
            "mosaic_qc",
            {
                "boundary_summaries": {
                    "raw_supported": {"raw": {"count": 0}},
                    "low_signal": {"raw": {"count": 2}},
                    "mixed_or_unknown": {"raw": {"count": 1}},
                }
            },
        )
    ]
    result = assessment(rows, "tile_boundary_steps")
    assert result["assessment"] == "review"
    assert result["measurement_count"] == 3
    assert result["measurement_unit"] == "seam/stratum measurements"


def test_failed_stage_metrics_do_not_look_assessed():
    rows = [
        stage(
            "mosaic_qc",
            {"periodicity_local_measurements": [{"assessment": "review"}]},
            execution="error",
            qc="not_assessed",
        )
    ]
    result = assessment(rows, "tile_pitch_pattern")
    assert result["assessment"] == "not_assessed" and result["measurement_count"] == 0
    assert result["measurement_breakdown"] == {"global": 0, "local": 0}
    assert result["unassessed_reason"] == "Stage did not complete"
    assert quality_status(rows) == "not_assessed"
    # Historical failed observations must not display retained diagnostic fits.
    rows[0]["metrics"]["strongest_local_patterns"] = [{"image": "corrected"}]
    assert mosaic_summary(rows) == ""


def test_unimplemented_masks_are_explicitly_unavailable():
    rows = [stage("tile_qc", {"detail_assessed_windows": 30}), stage("tissue", {})]
    for target in ("out_of_focus_segmentation", "tissue_fold"):
        result = assessment(rows, target)
        assert result["assessment"] == "not_assessed"
        assert result["unassessed_reason"] == "Validated detector unavailable"
        assert result["artifact_confirmed"] is None


def test_detail_report_separates_geometric_sampling_from_focus_decision():
    rows = [
        stage(
            "tile_qc",
            {
                "windows": 4,
                "detail_assessed_windows": 2,
                "detail_unavailable_by_reason": {"constant_input": 2},
                "detail_sampling": {"fraction": 0.6, "measurement_interior_fraction": 0.4},
            },
        )
    ]
    text = detail_summary(rows)
    assert "2 / 4 windows" in text and "constant input: 2" in text
    assert "60.0%" in text and "40.0%" in text
    assert "not tissue coverage, valid-focus area or blurred-area fractions" in text


def test_input_pass_does_not_imply_image_or_acquisition_completeness():
    rows = [stage("index", {"verified_files": 12}, qc="pass")]
    result = input_assessment(rows)
    assert result["assessment"] == "pass" and result["verified_files"] == 12
    assert not result["physical_acquisition_completeness_verified"]
    assert quality_status(rows) == "not_assessed"


def test_input_errors_and_unrun_inputs_cannot_pass():
    assert input_assessment([])["assessment"] == "not_run"
    for execution in ("error", "blocked"):
        result = input_assessment([stage("index", {}, execution=execution, qc="not_assessed")])
        assert result["assessment"] == execution and result["verified_files"] is None
