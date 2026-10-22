"""Historical labels must never become unverified current image decisions."""

import csv
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tma_qc.provenance import read_json, sha256, write_json

HELPER = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "experiments/labelled_reconstruction_review.py")
)
build_review = HELPER["build_review"]


@pytest.fixture
def study(tmp_path):
    run, raw = tmp_path / "run", tmp_path / "raw"
    run.mkdir()
    raw.mkdir()
    for name in ("raw.tif", "mosaic.tif", "comparison.png", "boundaries.json", "periodicity.json"):
        (run / name).write_bytes(name.encode())
    manifest = run / "manifest.json"
    write_json(
        manifest, {"raw_root": str(raw), "cores": [{"uid": "GRID-core"}, {"uid": "OK-core"}]}
    )
    write_json(
        run / "run.json",
        {
            "attempt_id": "current-attempt",
            "manifest_path": str(manifest),
            "manifest_sha256": sha256(manifest),
        },
    )
    stages = [
        {
            "unit": "GRID-core",
            "stage": "mosaic",
            "execution": "complete",
            "qc": "review",
            "outputs": {
                key: {"path": filename, "sha256": sha256(run / filename)}
                for key, filename in (("raw", "raw.tif"), ("mosaic", "mosaic.tif"))
            },
        }
    ]
    metrics = {
        "geometry_qc": {"geometry_exclusions_by_reason": {"weak_support": 2}},
        "illumination_qc": {"signal_diagnostics": {"exact_collapses": 0}},
        "tile_qc": {"detail_unavailable_by_reason": {"constant_input": 1}},
        "mosaic_qc": {
            "boundary_measurement_counts": {"low_signal": 2},
            "worst_seams": [{"axis": "x"}],
            "periodicity_measurements": [{"assessment": "not_assessed", "reason": "no_pitch"}],
            "periodicity_local_measurements": [{"assessment": "review", "amplitude": 0.2}],
        },
    }
    stages.extend(
        {
            "unit": "GRID-core",
            "stage": name,
            "execution": "complete",
            "qc": "review",
            "metrics": value,
            "findings": [{"message": "advisory measurement"}],
        }
        for name, value in metrics.items()
    )
    write_json(
        run / "stage-results.json",
        {
            "attempt_id": "current-attempt",
            "processing_attempt_finished": True,
            "code_sha256": "frozen-package-identity",
            "runtime_versions": {"python": "3.11"},
            "stage_results": stages,
        },
    )
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "uid,historical_label,workbook_cell\nGRID-core,GRID,UV!F143\nOK-core,OK,UV!F150\n"
    )
    return SimpleNamespace(run=run, labels=labels, out=tmp_path / "review", tmp=tmp_path)


def manual(study, **changes):
    record = {
        "uid": "GRID-core",
        "mosaic_sha256": sha256(study.run / "mosaic.tif"),
        "status": "review_candidate",
        "review_flag": "residual_low_signal_banding",
        "reason": "Current native image XY patch has fine bands; not expert validation",
        "reviewer": "Codex assisted visual inspection; not expert validation",
        "evidence": [
            {"path": name, "sha256": sha256(study.run / name)}
            for name in ("comparison.png", "boundaries.json", "periodicity.json")
        ],
    }
    record.update(changes)
    path = study.tmp / "reviews.json"
    write_json(path, [record])
    return path


def test_exact_join_preserves_history_without_inheriting_current_decision(study):
    before = {p: sha256(p) for p in study.run.iterdir() if p.is_file()}
    report = build_review(study.run, study.labels, study.out)
    first, second = report["records"]
    assert first["historical_label"] == "GRID" and first["workbook_cell"] == "UV!F143"
    assert first["current_review"]["status"] == "pending"
    assert second["historical_label"] == "OK"
    assert second["current_review"]["status"] == "not_assessed"
    assert all(row["current_review"]["review_flag"] is None for row in report["records"])
    assert not report["automatic_image_quality_acceptance"]
    assert not report["historical_image_version_verified"]
    assert all(not row["historical_image_version_verified"] for row in report["records"])
    assert report["code_sha256"] == "frozen-package-identity"
    assert first["geometry"]["metrics"]["geometry_exclusions_by_reason"] == {"weak_support": 2}
    assert first["illumination"]["metrics"]["signal_diagnostics"] == {"exact_collapses": 0}
    assert first["focus"]["metrics"]["detail_unavailable_by_reason"] == {"constant_input": 1}
    assert first["seams"]["boundary_measurement_counts"] == {"low_signal": 2}
    assert first["global_periodicity"][0]["reason"] == "no_pitch"
    assert first["local_periodicity"][0]["amplitude"] == 0.2
    assert first["selected_mosaic"]["sha256"] == sha256(study.run / "mosaic.tif")
    assert first["raw_mosaic"]["sha256"] == sha256(study.run / "raw.tif")
    assert read_json(study.out / "reconstruction-review.json") == report
    with (study.out / "reconstruction-review.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["current_status"] == "pending" and not rows[0]["review_flag"]
    assert rows[0]["historical_image_version_verified"] == "False"
    assert json.loads(rows[0]["local_periodicity"]) == first["local_periodicity"]
    assert {p: sha256(p) for p in before} == before


def test_manual_review_binds_current_mosaic_evidence_and_reviewer(study):
    reviews = manual(study)
    report = build_review(study.run, study.labels, study.out, reviews)
    decision = report["records"][0]["current_review"]
    assert decision["origin"] == "explicit_current_review"
    assert decision["status"] == "review_candidate"
    assert decision["review_flag"] == "residual_low_signal_banding"
    assert "not expert validation" in decision["reviewer"]
    assert decision["mosaic_sha256"] == sha256(study.run / "mosaic.tif")
    assert decision["evidence"][0] == {
        "path": str(study.run / "comparison.png"),
        "sha256": sha256(study.run / "comparison.png"),
    }
    assert report["reviews_sha256"] == sha256(reviews)


@pytest.mark.parametrize("status", ["error", "blocked", "not_run"])
def test_incomplete_stages_keep_reasons_but_never_assessed_looking_metrics(study, status):
    path = study.run / "stage-results.json"
    summary = read_json(path)
    for row in summary["stage_results"]:
        row["execution"] = status
        row["findings"] = [{"message": "Required input unavailable"}]
    write_json(path, summary)
    first = build_review(study.run, study.labels, study.out)["records"][0]
    assert first["selected_mosaic"] is None and first["raw_mosaic"] is None
    assert first["current_review"]["status"] == "not_assessed"
    for key in ("geometry", "illumination", "focus", "mosaic_qc"):
        assert first[key]["execution"] == status
        assert first[key]["qc"] == "not_assessed" and first[key]["metrics"] == {}
        assert first[key]["findings"] == [{"message": "Required input unavailable"}]
        assert first[key]["unassessed_reason"] == "Stage did not complete"
    assert first["seams"] == {} and first["global_periodicity"] == first["local_periodicity"] == []


@pytest.mark.parametrize(
    "rows,error",
    [
        ("GRID-core,GRID,UV!F143\nGRID-core,OK,UV!F150\n", "Duplicate label UID"),
        ("grid-core,GRID,UV!F143\n", "Unknown label UID"),
        ("GRID-core,,UV!F143\n", "nonempty"),
        ("GRID-core,GRID,UV!F143,extra\n", "nonempty"),
    ],
)
def test_bad_label_records_fail_before_writing(study, rows, error):
    study.labels.write_text("uid,historical_label,workbook_cell\n" + rows)
    with pytest.raises(ValueError, match=error):
        build_review(study.run, study.labels, study.out)
    assert not study.out.exists()


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"mosaic_sha256": "old-image-hash"}, "Stale"),
        ({"status": "pass"}, "status"),
        ({"status": []}, "status"),
        ({"status": "pending"}, "cannot carry"),
        ({"status": "not_assessed"}, "cannot carry"),
        ({"status": "no_issue_observed"}, "cannot carry"),
        ({"reason": " "}, "explicit reason"),
        ({"evidence": []}, "evidence"),
        ({"evidence": ["comparison.png"]}, "run-relative path"),
        ({"evidence": [{"path": "comparison.png"}]}, "64-hex SHA256"),
        ({"evidence": [{"path": "comparison.png", "sha256": "x" * 64}]}, "64-hex SHA256"),
        ({"evidence": [{"path": "comparison.png", "sha256": 123}]}, "64-hex SHA256"),
        ({"evidence": [{"path": "../labels.csv", "sha256": "a" * 64}]}, "inside the run"),
        ({"evidence": [{"path": "missing.png", "sha256": "a" * 64}]}, "existing file"),
        ({"reviewer": " "}, "reviewer"),
    ],
)
def test_invalid_manual_decisions_fail_before_writing(study, changes, error):
    reviews = manual(study, **changes)
    with pytest.raises(ValueError, match=error):
        build_review(study.run, study.labels, study.out, reviews)
    assert not study.out.exists()


def test_stale_receipt_does_not_accept_matching_old_manual_hash(study):
    reviews = manual(study)
    (study.run / "mosaic.tif").write_bytes(b"replacement-image")
    with pytest.raises(ValueError, match="artifact SHA256 mismatch"):
        build_review(study.run, study.labels, study.out, reviews)
    assert not study.out.exists()


def test_replaced_evidence_cannot_rebind_an_unchanged_manual_observation(study):
    reviews = manual(study)
    mosaic_hash = sha256(study.run / "mosaic.tif")
    (study.run / "comparison.png").write_bytes(b"replacement-evidence")
    with pytest.raises(ValueError, match="artifact SHA256 mismatch.*comparison.png"):
        build_review(study.run, study.labels, study.out, reviews)
    assert sha256(study.run / "mosaic.tif") == mosaic_hash
    assert not study.out.exists()


def test_manual_evidence_requires_relative_path_even_if_file_is_inside_run(study):
    reviews = manual(
        study,
        evidence=[
            {
                "path": str(study.run / "comparison.png"),
                "sha256": sha256(study.run / "comparison.png"),
            }
        ],
    )
    with pytest.raises(ValueError, match="must be run-relative"):
        build_review(study.run, study.labels, study.out, reviews)
    assert not study.out.exists()


@pytest.mark.parametrize(
    "kind", ["duplicate_review", "unknown_review", "duplicate_stage", "attempt"]
)
def test_ambiguous_or_mixed_run_identities_are_rejected(study, kind):
    reviews = manual(study)
    if kind.endswith("review"):
        records = read_json(reviews)
        records += records if kind == "duplicate_review" else [{**records[0], "uid": "unknown"}]
        write_json(reviews, records)
    else:
        summary_path = study.run / "stage-results.json"
        summary = read_json(summary_path)
        if kind == "duplicate_stage":
            summary["stage_results"].append(summary["stage_results"][0])
        else:
            summary["attempt_id"] = "other-attempt"
        write_json(summary_path, summary)
    with pytest.raises(ValueError, match="[Dd]uplicate|Unknown review|attempt identities"):
        build_review(study.run, study.labels, study.out, reviews)
    assert not study.out.exists()


def test_cli_reports_validation_failure_without_traceback(study, capsys):
    with pytest.raises(SystemExit, match="1"):
        HELPER["main"](
            ["--run", str(study.run), "--labels", str(study.labels), "--out", str(study.run)]
        )
    assert "must not overwrite" in capsys.readouterr().err
