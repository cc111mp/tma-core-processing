"""Build an immutable QC snapshot without changing a running source-first batch."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
from html import escape
from pathlib import Path

from tma_qc.io.manifest import Manifest
from tma_qc.provenance import read_json, sha256, utc, write_json
from tma_qc.schema import safe_id

SPEC = importlib.util.spec_from_file_location(
    "source_review_recipe", Path(__file__).with_name("process_source_core.py")
)
recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe)


def build_review(plan_path, output, workspace_map=None):
    plan_path, output = Path(plan_path), Path(output).resolve()
    plan = read_json(plan_path)
    if sha256(plan["manifest"]) != plan["manifest_sha256"]:
        raise ValueError("Frozen source manifest changed")
    manifest = Manifest(plan["manifest"])
    cores = {c["uid"]: c for c in manifest.snapshot()["cores"]}
    workspaces = {
        spec["uid"]: Path((workspace_map or {}).get(spec["uid"], spec["workspace"])).resolve()
        for spec in plan["cores"]
    }
    if any(output == p or p in output.parents for p in [manifest.raw_root, *workspaces.values()]):
        raise ValueError("Review output must be outside source and processing workspaces")
    output.mkdir(parents=True, exist_ok=False)
    records, stage_table, receipts = [], [], []

    def observe(path, default):
        if not path.exists():
            return default
        raw = path.read_bytes()
        receipts.append(
            {"file": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "observed_utc": utc()}
        )
        return json.loads(raw)

    batch = observe(plan_path.parent / "status.json", {"cores": []})
    batch_rows = {r["uid"]: r for r in batch["cores"]}
    for spec in plan["cores"]:
        uid = safe_id(spec["uid"])
        work = workspaces[uid]
        job_path, summary_path = work / "job.json", work / "run/stage-results.json"
        job = observe(
            job_path,
            {
                "identity": None,
                "source": {"cores": [cores[uid]]},
            },
        )
        summary = observe(summary_path, {"stage_results": []})
        package_path, publication_path = work / "package.json", work / "publication.json"
        package = observe(package_path, {})
        summary["stage_results"].extend(package.get("delivery_stage_results", []))
        publication = observe(publication_path, spec.get("previous_publication", {}))
        notes = [
            "Read-only snapshot of recorded results. Raw data, native images and destination archives were not reverified by this report."
        ]
        notes.extend(package.get("delivery_findings", []))
        published = bool(publication.get("destination_archive_SHA256_verified"))
        if publication and publication.get("job_identity") != job["identity"]:
            raise ValueError(f"Publication identity differs from its core job: {uid}")
        prior = batch_rows.get(uid, {})
        summary["stage_results"].append(
            {
                "unit": uid,
                "stage": "publication",
                "execution": "complete"
                if publication.get("complete")
                else "error"
                if publication.get("publication_errors")
                or (prior.get("status") == "failed" and package)
                else "not_run",
                "qc": "not_assessed",
                "metrics": {},
                "outputs": {},
                "provenance": {},
                "findings": [
                    "Recorded NAS image delivery is verified."
                    if published
                    else prior.get("reason", "No completed NAS image publication was recorded.")
                ],
            }
        )
        qc = recipe.review_records(summary, job, package.get("exports", []), notes)
        qc["native_archive_available"] = bool(package.get("archive_sha256"))
        evidence = {}
        for row in summary["stage_results"]:
            for label, name in (("comparison", "comparison.png"), ("tile_grid", "tile-grid.png")):
                artifact = row.get("outputs", {}).get(label)
                if artifact and row["execution"] == "complete":
                    try:
                        path = (work / "run" / artifact["path"]).resolve()
                        if (
                            not path.is_relative_to((work / "run").resolve())
                            or sha256(path) != artifact["sha256"]
                        ):
                            raise ValueError(
                                "Preview path or SHA256 differs from its stage receipt"
                            )
                        evidence[name] = path
                    except (OSError, ValueError) as exc:
                        notes.append(
                            f"Preview unavailable: {uid}/{name}: {type(exc).__name__}: {exc}"
                        )
        folder = output / uid
        folder.mkdir()
        recipe.write_review(folder, qc, evidence)
        page = folder / "index.html"
        if published:
            download = Path(os.path.relpath(publication["archive"], folder)).as_posix()
            page.write_text(
                page.read_text().replace(
                    'href="results.zip"', f'href="{escape(download, quote=True)}"'
                )
            )
        else:
            page.write_text(
                page.read_text().replace(
                    '<a href="results.zip">Download native corrected core and replay assets</a>',
                    "Native export is recorded locally; NAS publication is pending.",
                )
            )
        unavailable = [r["stage"] for r in qc["stages"] if r["execution"] != "complete"]
        failed_quality = [
            r["stage"] for r in qc["stages"] if r["execution"] == "complete" and r["qc"] == "fail"
        ]
        records.append(
            {
                "uid": uid,
                "slide": qc["slide"],
                "acquisition": qc["acquisition"],
                "core": qc["core"],
                "job_identity": qc["job_identity"],
                "final_image_recorded": qc["final_image_available"],
                "native_archive_recorded": qc["native_archive_available"],
                "publication_recorded_complete": bool(publication.get("complete")),
                "native_image_published": published,
                "pipeline_execution": qc["pipeline_execution"],
                "quality_status": qc["quality_status"],
                "expert_review": "pending",
                "execution_problem_stages": "; ".join(unavailable),
                "image_quality_failure_stages": "; ".join(failed_quality),
                "review_page": f"{uid}/qc/index.html",
            }
        )
        stage_table.extend(
            {
                "uid": uid,
                **{k: r[k] for k in ("stage", "execution", "qc")},
                "findings": " | ".join(r["findings"]),
            }
            for r in qc["stages"]
        )
    for name, rows in (
        ("cores.csv", records),
        ("stages.csv", stage_table),
        (
            "review-needed.csv",
            [r for r in stage_table if r["execution"] != "complete" or r["qc"] != "pass"],
        ),
    ):
        with (output / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["uid"])
            writer.writeheader()
            writer.writerows(rows)
    write_json(
        output / "snapshot.json",
        {
            "created_utc": utc(),
            "plan": str(plan_path),
            "plan_sha256": sha256(plan_path),
            "report_script_sha256": sha256(Path(__file__)),
            "recipe_sha256": sha256(Path(recipe.__file__)),
            "scope": "Recorded per-core observations sampled sequentially; active processing may advance. No processing or publication status is changed.",
            "records": records,
            "input_receipts": receipts,
        },
    )
    body = "<h1>AF core outputs and QC review</h1><p>This snapshot separates recorded image availability, publication and QC. REVIEW does not mean a confirmed artifact. Preview hashes were checked; native-image and NAS verification states are historical records.</p>"
    body += '<p><a href="cores.csv">Core summary CSV</a> · <a href="stages.csv">All stages CSV</a> · <a href="review-needed.csv">Review queue CSV</a> · <a href="snapshot.json">Snapshot receipt</a></p>'
    body += '<label>Show cores: <select id="filter"><option value="all">All</option><option value="execution">Execution incomplete / error</option><option value="quality">Image-quality failure</option><option value="publication">NAS publication pending</option></select></label>'
    body += "<table><thead><tr><th>Core</th><th>Native image recorded</th><th>NAS save recorded</th><th>Processing</th><th>Image quality</th><th>Problem stages</th></tr></thead><tbody>"
    for row in records:
        flags = f"{'execution ' if row['execution_problem_stages'] else ''}{'quality ' if row['image_quality_failure_stages'] else ''}{'publication' if not row['native_image_published'] else ''}"
        issue = (
            row["execution_problem_stages"]
            or row["image_quality_failure_stages"]
            or "No stage execution failure; scientific QC awaits review"
        )
        native_label = "Yes" if row["final_image_recorded"] else "Not yet"
        nas_label = "Verified" if row["native_image_published"] else "Pending"
        body += f'<tr data-flags="{flags}"><td><a href="{row["review_page"]}">{escape(row["uid"])}</a><br><a href="{escape(row["uid"])}/index.html">Output availability</a></td><td>{native_label}</td><td>{nas_label}</td><td>{escape(row["pipeline_execution"])}</td><td>{escape(row["quality_status"])}</td><td>{escape(issue)}</td></tr>'
    body += '</tbody></table><script>document.getElementById("filter").addEventListener("change",e=>{document.querySelectorAll("tr[data-flags]").forEach(r=>r.hidden=e.target.value!=="all"&&!r.dataset.flags.split(" ").includes(e.target.value))});</script>'
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Batch QC review</title><style>body{font:16px system-ui;max-width:1500px;margin:24px auto;padding:16px}table{border-collapse:collapse;width:100%;margin:20px 0}th,td{padding:10px;border:1px solid #ccc;text-align:left;vertical-align:top}tr[hidden]{display:none}</style><body>'
        + body
        + "</body></html>"
    )
    return output / "index.html"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out", required=True, help="New, dedicated snapshot directory")
    parser.add_argument(
        "--workspace-map", help="Optional UID-to-local-workspace JSON for earlier pilots"
    )
    args = parser.parse_args()
    print(
        build_review(
            args.plan, args.out, read_json(args.workspace_map) if args.workspace_map else None
        )
    )


if __name__ == "__main__":
    main()
