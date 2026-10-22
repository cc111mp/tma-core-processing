"""Keep execution, measurement availability and scientific acceptance separate."""

from __future__ import annotations

DIAGNOSTIC_STAGES = frozenset(
    {"tile_qc", "geometry_qc", "illumination_qc", "mosaic_qc", "tissue", "he_qc", "registration_qc"}
)


def quality_status(rows):
    completed = [
        r for r in rows if r["execution"] == "complete" and r["stage"] in DIAGNOSTIC_STAGES
    ]
    if any(r["qc"] == "fail" for r in completed):
        return "fail"
    return "review" if completed else "not_assessed"


def input_assessment(rows):
    """Describe verification of manifest-listed files separately from image QC."""
    stage = next((row for row in rows if row["stage"] == "index"), {})
    execution = stage.get("execution", "not_run")
    return {
        "assessment": "pass"
        if execution == "complete" and stage.get("qc") == "pass"
        else execution
        if execution in {"error", "blocked", "not_run"}
        else "not_assessed",
        "verified_files": stage.get("metrics", {}).get("verified_files")
        if execution == "complete"
        else None,
        "scope": "Manifest-listed source files, hashes, single-plane TIFF payload and profile",
        "physical_acquisition_completeness_verified": False,
    }


def artifact_assessments(rows):
    """Summarize saved measurements without inventing artifact classifications."""
    stages = {r["stage"]: r for r in rows}
    definitions = [
        (
            "tile_alignment",
            "geometry_qc",
            "geometry_assessed_pairs",
            "evidence",
            "Phase residuals require adequate texture and independent geometry review.",
        ),
        (
            "overlap_brightness",
            "illumination_qc",
            "raw_supported_pairs",
            "evidence",
            "Only raw-supported, correlated overlaps enter photometric comparison; excluded pairs are unknown.",
        ),
        (
            "local_detail",
            "tile_qc",
            "detail_assessed_windows",
            "tile_grid",
            "Contrast-normalized detail is not defocus probability.",
        ),
        (
            "tile_boundary_steps",
            "mosaic_qc",
            None,
            "boundary_evidence",
            "Boundary steps and nearby controls can also respond to anatomy.",
        ),
        (
            "tile_pitch_pattern",
            "mosaic_qc",
            None,
            "projection_evidence",
            "Projection harmonic fractions are not GRID probabilities or artifact-area ratios.",
        ),
        (
            "out_of_focus_segmentation",
            "tile_qc",
            None,
            None,
            "Validated blur segmentation and a tissue-area denominator are unavailable.",
        ),
        (
            "tissue_fold",
            "tissue",
            None,
            None,
            "No validated fold detector; neighboring-section differences are not fold evidence.",
        ),
    ]
    results = []
    for artifact, stage, count_key, evidence_key, note in definitions:
        row = stages.get(stage, {})
        metrics = row.get("metrics", {}) if row.get("execution") == "complete" else {}
        count = metrics.get(count_key, 0) if count_key else 0
        unit = "overlap pairs"
        breakdown = {}
        extra_evidence = {}
        if artifact == "tile_alignment":
            breakdown = {
                "candidate_pairs": metrics.get(
                    "geometry_candidate_pairs", metrics.get("overlapping_pairs")
                ),
                "excluded_by_reason": metrics.get("geometry_exclusions_by_reason", {}),
            }
        if artifact == "local_detail":
            unit = "detail windows"
            breakdown = {
                "total_windows": metrics.get("windows"),
                "unavailable_windows": metrics.get("detail_unavailable_windows"),
                "unavailable_by_reason": metrics.get("detail_unavailable_by_reason", {}),
                "geometric_sampling": metrics.get("detail_sampling"),
            }
        if artifact == "tile_boundary_steps":
            strata = metrics.get("boundary_measurement_counts")
            if strata is None:
                strata = {
                    name: values.get("raw", {}).get("count", 0)
                    for name, values in metrics.get("boundary_summaries", {}).items()
                }
            # A seam may contribute to more than one signal stratum. New
            # receipts count its location once; historical summaries only
            # establish the number of seam/stratum measurements.
            count = metrics.get("boundary_assessed_seams", sum(strata.values()))
            unit = (
                "distinct seams"
                if "boundary_assessed_seams" in metrics
                else "seam/stratum measurements"
            )
            breakdown = {"by_signal_stratum": strata}
        elif artifact == "tile_pitch_pattern":
            global_count = sum(
                r.get("assessment") == "review" for r in metrics.get("periodicity_measurements", [])
            )
            local_count = sum(
                r.get("assessment") == "review"
                for r in metrics.get("periodicity_local_measurements", [])
            )
            count = global_count + local_count
            unit = "axis/stratum profile fits"
            breakdown = {"global": global_count, "local": local_count}
            if value := row.get("outputs", {}).get("local_projection_evidence"):
                extra_evidence["Local projections"] = value
        assessed = row.get("execution") == "complete" and bool(count)
        unsupported = artifact in {"out_of_focus_segmentation", "tissue_fold"}
        reason = (
            "Validated detector unavailable"
            if unsupported
            else "Stage not run"
            if not row
            else "Stage did not complete"
            if row.get("execution") != "complete"
            else "No supported measurement available; inspect stage reasons and evidence"
            if not assessed
            else None
        )
        results.append(
            {
                "artifact": artifact,
                "stage": stage,
                "assessment": "review" if assessed else "not_assessed",
                "measurement_count": count if assessed else 0,
                "measurement_unit": unit if not unsupported else None,
                "measurement_breakdown": breakdown,
                "unassessed_reason": reason,
                "execution": row.get("execution", "not_run"),
                "evidence": row.get("outputs", {}).get(evidence_key) if evidence_key else None,
                "additional_evidence": extra_evidence,
                "rules_calibrated": False,
                "artifact_confirmed": None,
                "affected_tissue_fraction": None,
                "qualification": note,
            }
        )
    return results
