from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from ..provenance import fingerprint, write_json
from ..qc.assessment import artifact_assessments, input_assessment, quality_status
from ..schema import CORE_STAGES, PAIR_STAGES
from .core import build_core, document, escape, href
from .slide import make_grid


def mark_report_incomplete(root, manifest, created):
    """Remove the appearance of a current completed report before processing."""
    root = Path(root)
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    body = '<p class="notice">The current processing attempt has not finished. Earlier reports do not describe this attempt.</p>'
    body += f"<p>Started: {escape(created)}</p>"
    body += '<p><a href="../stage-results.json">Current stage records</a> · <a href="../run.json">Current run provenance</a></p>'
    (reports / "index.html").write_text(
        document(manifest.dataset_id + " — unfinished attempt", body), encoding="utf-8"
    )
    (root / "index.html").write_text(
        document(
            "TMA preprocessing results",
            '<p><a href="reports/index.html">Current attempt status</a></p>',
        ),
        encoding="utf-8",
    )


def build_report(root, manifest, summary, config, cohort=None):
    root = Path(root)
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    page = reports / "index.html"
    if cohort is None and (root / "cohort").is_dir():
        from ..cohort import Cohort

        cohort = Cohort(
            root, manifest, config, summary["code_sha256"], summary["runtime_versions"]
        ).snapshot(summary.get("attempt_id"))
    observations = cohort["units"] if cohort else {}
    if cohort:
        write_json(reports / "cohort-results.json", cohort)
    rows_by_unit = defaultdict(list)
    for row in (cohort or summary)["stage_results"]:
        rows_by_unit[row["unit"]].append(row)
    table = []
    artifact_rows = []
    pages = {}
    for core in manifest.cores:
        rows = rows_by_unit[core.uid]
        pairs = [(p, rows_by_unit[p.uid]) for p in manifest.pairs if p.af_core == core.uid]
        info = observations.get(core.uid, {})
        pages[core.uid] = build_core(root, core, rows, pairs, observation=info)
        pair_rows = [r for _, records in pairs for r in records]
        combined = rows + pair_rows
        artifact_rows.extend({"unit": core.uid, **r} for r in artifact_assessments(rows))
        execution = (
            "not_run"
            if not rows
            else "error"
            if any(r["execution"] == "error" for r in combined)
            else "blocked"
            if any(r["execution"] == "blocked" for r in combined)
            else "complete"
        )
        pair_execution = (
            "not_assessed"
            if not pair_rows
            else "error"
            if any(r["execution"] == "error" for r in pair_rows)
            else "blocked"
            if any(r["execution"] == "blocked" for r in pair_rows)
            else "complete"
        )
        if execution == "complete" and (
            not set(CORE_STAGES) <= {r["stage"] for r in rows}
            or any(not set(PAIR_STAGES) <= {r["stage"] for r in records} for _, records in pairs)
        ):
            execution = "partial"
        if pair_execution == "complete" and any(
            not set(PAIR_STAGES) <= {r["stage"] for r in records} for _, records in pairs
        ):
            pair_execution = "partial"
        if info.get("observation") == "stale":
            execution = "stale"
        elif info.get("processing_finished") is False:
            execution = "partial"
        pair_info = [observations.get(p.uid, {}) for p, _ in pairs]
        if any(i.get("observation") == "stale" for i in pair_info):
            pair_execution = "stale"
        elif any(i.get("processing_finished") is False for i in pair_info):
            pair_execution = "partial"
        if execution == "complete" and pair_execution in {"stale", "partial"}:
            execution = pair_execution
        table.append(
            {
                "uid": core.uid,
                "slide": core.slide,
                "acquisition": core.acquisition,
                "core": core.core,
                "channel": core.channel,
                "execution": execution,
                "input_eligibility": "blocked" if core.blocked_reasons else "eligible",
                "eligibility_reasons": "; ".join(core.blocked_reasons),
                "manifest_fovs": len(core.fovs),
                "input_verification": input_assessment(rows)["assessment"],
                "verified_input_files": input_assessment(rows)["verified_files"],
                "registration_execution": pair_execution,
                "observation": info.get("observation", "current_attempt"),
                "last_processed_utc": info.get("updated_utc", summary["updated_utc"]),
                "attempt_id": info.get("attempt_id", summary.get("attempt_id")),
                "qc": quality_status(combined),
                "reconstruction_complete": any(
                    r["stage"] == "mosaic"
                    and r["execution"] == "complete"
                    and {"mosaic", "coverage"} <= r.get("outputs", {}).keys()
                    for r in rows
                ),
                "execution_error_stages": ";".join(
                    r["stage"] for r in combined if r["execution"] == "error"
                ),
                "quality_failure_stages": ";".join(
                    r["stage"]
                    for r in combined
                    if r["execution"] == "complete" and r["qc"] == "fail"
                ),
                "registration": "not_assessed" if not pairs else ";".join(p.uid for p, _ in pairs),
                "automatic_release": False,
                "findings": " | ".join(
                    f"{r['unit']}/{r['stage']}: {v}" for r in combined for v in r["findings"]
                ),
                "review_page": pages[core.uid].relative_to(root).as_posix(),
            }
        )
    coverage_rows = [
        {
            key: row[key]
            for key in (
                "uid",
                "slide",
                "acquisition",
                "core",
                "channel",
                "input_eligibility",
                "eligibility_reasons",
                "manifest_fovs",
                "execution",
            )
        }
        for row in table
    ]
    write_json(
        reports / "artifact-assessments.json",
        {"automatic_image_quality_release": False, "records": artifact_rows},
    )
    with (reports / "artifact-review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "unit",
            "artifact",
            "stage",
            "assessment",
            "execution",
            "measurement_count",
            "measurement_unit",
            "unassessed_reason",
            "qualification",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(artifact_rows)
    for filename, chosen in [
        ("core-qc.csv", table),
        ("review-needed.csv", [r for r in table if r["qc"] != "pass"]),
        ("inventory-coverage.csv", coverage_rows),
    ]:
        with (reports / filename).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=list(chosen[0]) if chosen else list(table[0]) if table else ["uid"]
            )
            writer.writeheader()
            writer.writerows(chosen)
    body = '<p class="notice">Review pipeline. Automatic image-quality release is disabled. PASS on input integrity does not certify focus, folds, illumination accuracy or registration.</p>'
    if cohort:
        body += '<p class="notice">Cumulative cohort view: the latest compatible result for each core. Historical results are labelled and have not been reverified in this attempt. Changed definitions are STALE. Current attempt counts appear separately below.</p>'
        body += '<p><a href="cohort-results.json">Cumulative stage records and observation times</a> · <a href="../AF-storage-plan.json">AF storage estimate</a></p>'
    if not summary.get("processing_attempt_finished", False):
        body += '<p class="notice">This processing attempt has not finished. Results below are partial.</p>'
    blocked = sum(bool(c.blocked_reasons) for c in manifest.cores)
    coverage = {
        "manifest_core_acquisitions": len(manifest.cores),
        "eligible": len(manifest.cores) - blocked,
        "blocked_at_input": blocked,
        "execution_counts": dict(Counter(r["execution"] for r in table)),
        "source_import_coverage": manifest.data.get("metadata", {}).get("import_coverage"),
    }
    write_json(reports / "coverage.json", coverage)
    body += f"<p>Manifest coverage: {len(manifest.cores)} core acquisitions · {len(manifest.cores) - blocked} eligible · {blocked} blocked at input.</p>"
    body += f"<p>Current attempt updated: {escape(summary['updated_utc'])} · Execution errors: {summary['execution_errors']} · Blocked stages: {summary['blocked_stages']}</p>"
    body += '<p><a href="core-qc.csv">Core QC table</a> · <a href="inventory-coverage.csv">Inventory coverage</a> · <a href="coverage.json">Coverage counts</a> · <a href="review-needed.csv">Review queue</a> · <a href="../stage-results.json">All stage records</a> · <a href="../run.json">Run provenance</a></p>'
    body += '<p><a href="artifact-review.csv">Artifact review queue</a> · <a href="artifact-assessments.json">Artifact measurements and evidence records</a></p>'
    groups = defaultdict(list)
    for core in manifest.cores:
        groups[(core.acquisition, core.channel, core.group)].append(core)
    grid_dir = reports / "slide-grids"
    grid_dir.mkdir(exist_ok=True)
    for (acquisition, channel, group), cores in groups.items():
        path = grid_dir / (
            acquisition[:80] + "-" + channel[:16] + "-" + fingerprint(group)[:12] + ".png"
        )
        body += f"<h2>{escape(acquisition)} / {escape(channel)} / {escape(group)}</h2>"
        try:
            grid_rows = {
                c.uid: rows_by_unit[c.uid]
                + [r for p in manifest.pairs if p.af_core == c.uid for r in rows_by_unit[p.uid]]
                for c in cores
            }
            details = make_grid(
                root,
                cores,
                grid_rows,
                path,
                config["report"]["slide_tile"],
                observations=observations,
            )
            details.update(acquisition=acquisition, channel=channel, illumination_group=group)
            write_json(path.with_suffix(".json"), details)
            body += f'<a href="{href(page, path)}"><img src="{href(page, path)}" alt="TMA acquisition grid"></a>'
        except (ValueError, OSError) as exc:
            body += f'<p class="notice">Grid requires review: {escape(exc)}</p>'
        body += '<div class="grid">'
        for core in cores:
            row = next(r for r in table if r["uid"] == core.uid)
            body += f'<div class="card"><a href="{href(page, pages[core.uid])}">{escape(core.core)}</a><p>Execution: {escape(row["execution"])} · QC: {escape(row["qc"])}</p>'
            body += f"<p>Reconstruction recorded complete: {row['reconstruction_complete']}</p>"
            body += f"<p>{escape(row['observation'])} · Last processed: {escape(row['last_processed_utc'])}</p>"
            if core.blocked_reasons:
                body += (
                    '<p class="notice">Blocked input: '
                    + escape("; ".join(core.blocked_reasons))
                    + "</p>"
                )
            body += "</div>"
        body += "</div>"
    page.write_text(document(manifest.dataset_id + " preprocessing review", body), encoding="utf-8")
    (root / "index.html").write_text(
        document(
            "TMA preprocessing results",
            '<p><a href="reports/index.html">Open the batch review report</a></p>',
        ),
        encoding="utf-8",
    )
    return page
