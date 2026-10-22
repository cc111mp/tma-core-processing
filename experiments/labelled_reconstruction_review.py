"""Join historical labels to current reconstructions; no automatic quality decisions.

Manual reviews are a JSON list with uid, mosaic_sha256, status, reason and
evidence (objects with run-relative path and observation-time sha256), plus
optional review_flag and reviewer.
Status is pending, not_assessed, review_candidate or no_issue_observed.
No status represents automatic image-quality acceptance.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from tma_qc.provenance import read_json, sha256, write_json
from tma_qc.storage import validate_writable_tree

STATUSES = {"pending", "not_assessed", "review_candidate", "no_issue_observed"}


def _asset(root, value):
    if not value:
        return None
    path = (root / value["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Evidence must be an existing file inside the run: {path}")
    actual = sha256(path)
    if value.get("sha256", actual) != actual:
        raise ValueError(f"Current artifact SHA256 mismatch: {path}")
    return {"path": str(path), "sha256": actual}


def _index(records, known, kind):
    if not isinstance(records, list):
        raise ValueError(f"{kind} must be a list of records")
    indexed = {}
    for record in records:
        uid = record.get("uid") if isinstance(record, dict) else None
        if not isinstance(uid, str) or uid not in known:
            raise ValueError(f"Unknown {kind} UID: {uid!r}")
        if uid in indexed:
            raise ValueError(f"Duplicate {kind} UID: {uid}")
        indexed[uid] = record
    return indexed


def _labels(path, known):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        required = {"uid", "historical_label", "workbook_cell"}
        if len(fields) != len(set(fields)) or not required <= set(fields):
            raise ValueError("Labels require unique uid,historical_label,workbook_cell columns")
        rows = list(reader)
    for row in rows:
        if None in row or any(
            not isinstance(row.get(k), str) or not row[k].strip() for k in required
        ):
            raise ValueError(
                "Label records require nonempty UID, historical label and workbook cell"
            )
    return _index(rows, known, "label")


def _manual(record, mosaic, root):
    if mosaic is None or record.get("mosaic_sha256") != mosaic["sha256"]:
        raise ValueError(f"Stale or unavailable selected-mosaic SHA256 for {record['uid']}")
    if not isinstance(record.get("status"), str) or record["status"] not in STATUSES:
        raise ValueError(f"Manual review status must be one of {sorted(STATUSES)}")
    if not isinstance(record.get("reason"), str) or not record["reason"].strip():
        raise ValueError("Manual review requires an explicit reason")
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("Manual review requires nonempty evidence records")
    verified = []
    for item in evidence:
        path, digest = (
            (item.get("path"), item.get("sha256")) if isinstance(item, dict) else (None, None)
        )
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Manual evidence requires a nonempty run-relative path")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("Manual evidence requires an observation-time 64-hex SHA256")
        if Path(path).is_absolute():
            raise ValueError("Manual evidence paths must be run-relative")
        verified.append(_asset(root, {"path": path, "sha256": digest.lower()}))
    flag = record.get("review_flag")
    if flag is not None and (not isinstance(flag, str) or not flag.strip()):
        raise ValueError("review_flag must be a nonempty description or null")
    if flag is not None and record["status"] != "review_candidate":
        raise ValueError("Reviews outside review_candidate status cannot carry a review flag")
    reviewer = record.get("reviewer")
    if reviewer is not None and (not isinstance(reviewer, str) or not reviewer.strip()):
        raise ValueError("reviewer must be a nonempty description or null")
    return {
        "origin": "explicit_current_review",
        "status": record["status"],
        "review_flag": flag,
        "reason": record["reason"],
        "reviewer": reviewer,
        "mosaic_sha256": mosaic["sha256"],
        "evidence": verified,
    }


def build_review(run, labels, output, reviews=None):
    root, destination = Path(run).resolve(), Path(output).resolve()
    attempt, summary = read_json(root / "run.json"), read_json(root / "stage-results.json")
    manifest_path = Path(attempt["manifest_path"])
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest = read_json(manifest_path)
    manifest_sha256 = sha256(manifest_path)
    if attempt.get("manifest_sha256", manifest_sha256) != manifest_sha256:
        raise ValueError("Frozen run manifest SHA256 mismatch")
    if attempt.get("attempt_id") != summary.get("attempt_id"):
        raise ValueError("Run and stage summary attempt identities differ")
    known = {core["uid"] for core in manifest["cores"]}
    labelled = _labels(labels, known)
    manual = _index(read_json(reviews), set(labelled), "review") if reviews else {}
    stages = {}
    for row in summary["stage_results"]:
        key = row["unit"], row["stage"]
        if key in stages:
            raise ValueError(f"Duplicate current stage identity: {key}")
        stages[key] = row
    results = []
    for uid, label in labelled.items():

        def stage(name):
            row = stages.get((uid, name), {})
            diagnostic = {
                key: row.get(key, default)
                for key, default in (
                    ("execution", "not_run"),
                    ("qc", "not_assessed"),
                    ("metrics", {}),
                    ("findings", []),
                )
            }
            if diagnostic["execution"] != "complete":
                diagnostic.update(
                    metrics={}, qc="not_assessed", unassessed_reason="Stage did not complete"
                )
            return diagnostic

        mosaic = stages.get((uid, "mosaic"), {})
        outputs = mosaic.get("outputs", {}) if mosaic.get("execution") == "complete" else {}
        raw, selected = (_asset(root, outputs.get(name)) for name in ("raw", "mosaic"))
        decision = {
            "origin": "no_current_manual_review",
            "status": "pending" if selected else "not_assessed",
            "review_flag": None,
            "reason": "No current manual review supplied",
            "reviewer": None,
            "mosaic_sha256": selected["sha256"] if selected else None,
            "evidence": [],
        }
        if uid in manual:
            decision = _manual(manual[uid], selected, root)
        measurements = stage("mosaic_qc")
        metrics = measurements["metrics"]
        results.append(
            {
                "uid": uid,
                "historical_label": label["historical_label"],
                "historical_image_version_verified": False,
                "workbook_cell": label["workbook_cell"],
                "current_review": decision,
                "raw_mosaic": raw,
                "selected_mosaic": selected,
                "geometry": stage("geometry_qc"),
                "illumination": stage("illumination_qc"),
                "focus": stage("tile_qc"),
                "mosaic_qc": measurements,
                "seams": {
                    k: v
                    for k, v in metrics.items()
                    if k.startswith("boundary_") or k == "worst_seams"
                },
                "global_periodicity": metrics.get("periodicity_measurements", []),
                "local_periodicity": metrics.get("periodicity_local_measurements", []),
            }
        )
    report = {
        "run": str(root),
        "attempt_id": summary.get("attempt_id"),
        "processing_attempt_finished": summary.get("processing_attempt_finished", False),
        "code_sha256": summary.get("code_sha256"),
        "runtime_versions": summary.get("runtime_versions", {}),
        "historical_image_version_verified": False,
        "labels_sha256": sha256(labels),
        "reviews_sha256": sha256(reviews) if reviews else None,
        "automatic_image_quality_acceptance": False,
        "scope": "Historical labels are context, never current artifact decisions. Measurements and explicit manual observations are not calibrated acceptance or label-agreement estimates.",
        "records": results,
    }
    validate_writable_tree(destination, manifest["raw_root"])
    targets = [destination / ("reconstruction-review." + ext) for ext in ("json", "csv")]
    if destination == root or any(
        path == Path(source).resolve() for path in targets for source in (labels, reviews) if source
    ):
        raise ValueError("Review outputs must not overwrite the run or supplied labels/reviews")
    write_json(targets[0], report)
    fields = [
        "uid",
        "historical_label",
        "workbook_cell",
        "historical_image_version_verified",
        "current_status",
        "review_flag",
        "reason",
        "reviewer",
        "evidence",
        "raw_mosaic",
        "selected_mosaic",
        "geometry",
        "illumination",
        "focus",
        "seams",
        "global_periodicity",
        "local_periodicity",
    ]
    with targets[1].open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in results:
            row = {key: record[key] for key in fields if key in record}
            row.update(
                current_status=record["current_review"]["status"],
                **{
                    key: record["current_review"][key]
                    for key in ("review_flag", "reason", "reviewer", "evidence")
                },
            )
            writer.writerow(
                {
                    k: json.dumps(v, allow_nan=False) if isinstance(v, (dict, list)) else v
                    for k, v in row.items()
                }
            )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run", "labels", "out"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--reviews", help="Optional current manual review JSON list")
    args = parser.parse_args(argv)
    try:
        build_review(args.run, args.labels, args.out, args.reviews)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"labelled reconstruction review: {error}\n")


if __name__ == "__main__":
    main()
