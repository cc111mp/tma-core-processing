"""Process one complete source core through local SSD and publish final products.

This AF-only research recipe reuses tma_qc's verified numerical pipeline. It
requires an explicit, independently complete source manifest and expected hashes;
it never reads or changes the bulk transfer's progress database. Run --help for
paths. Full checkpoints stay local; the published ZIP contains lossless final
images, masks, QC, frozen assets and a replay manifest pointing to original raw.
"""

from __future__ import annotations

import argparse
import base64
import csv
import fcntl
import hashlib
import json
import os
import shutil
import sys
import uuid
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from html import escape
from pathlib import Path

import numpy as np
import tifffile

from tma_qc.config import load_config
from tma_qc.io.images import read_image
from tma_qc.io.manifest import Manifest
from tma_qc.pipeline import Runner
from tma_qc.provenance import code_identity, fingerprint, read_json, sha256, utc, write_json
from tma_qc.schema import CORE_STAGES, NeedsInput
from tma_qc.state import filesystem_type
from tma_qc.storage import require_space


def verified_copy(source, target, expected, progress=None):
    """Stream a pinned file, detect source changes, then rehash its local copy."""
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve():
        raise ValueError("Copy source and target must differ")
    if target.exists():
        if sha256(target) != expected:
            raise ValueError(f"Existing staged/published file differs: {target}")
        return {"source": str(source), "target": str(target), "sha256": expected, "reused": True}
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name("." + target.name + "." + uuid.uuid4().hex + ".partial")
    before = source.stat()
    require_space([(target.parent, before.st_size, "Verified file copy")])
    digest, count = hashlib.sha256(), 0
    try:
        with source.open("rb") as src, partial.open("xb") as dst:
            while block := src.read(1024 * 1024):
                dst.write(block)
                digest.update(block)
                count += len(block)
                if progress and count % (8 * 1024 * 1024) == 0:
                    progress(count, before.st_size)
            dst.flush()
            os.fsync(dst.fileno())
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"Source changed during copying: {source}")
        if count != before.st_size or digest.hexdigest() != expected or sha256(partial) != expected:
            raise ValueError(f"Copy SHA256 mismatch: {source}")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return {"source": str(source), "target": str(target), "sha256": expected, "reused": False}


def select_core(manifest, uid):
    """Keep one independently complete AF core and its frozen illumination group."""
    data = manifest.snapshot()
    core = next((c for c in data["cores"] if c["uid"] == uid), None)
    if core is None:
        raise ValueError(f"Unknown core UID: {uid}")
    if core["channel"] != "AF":
        raise ValueError("This recipe requires an AF core")
    if core["blocked_reasons"] or core["metadata"].get("source_FOV_set_verified") is not True:
        raise ValueError(
            "Source FOV completeness must be independently verified before this recipe"
        )
    if any(p["af_core"] == uid for p in data["pairs"]):
        raise ValueError("This recipe processes AF cores; use the full workflow for section pairs")
    data["cores"], data["pairs"] = [core], []
    data["groups"] = [g for g in data["groups"] if g["id"] == core["group"]]
    return data


@contextmanager
def workspace_lock(work):
    """Only one process may stage, reconstruct or publish this local workspace."""
    work.mkdir(parents=True, exist_ok=True)
    with (work / ".source.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another source job is using this workspace") from exc
        yield


def stage_inputs(manifest, source, work, asset_hashes):
    """Pin one core's raw bytes and small replay assets on local storage."""
    staged, replay = deepcopy(source), deepcopy(source)
    staged["raw_root"] = str(work / "raw")
    receipts = []
    original_core = next(c for c in manifest.cores if c.uid == source["cores"][0]["uid"])
    original_fovs = {f.id: f for f in original_core.fovs}
    for number, (original, target) in enumerate(
        zip(source["cores"][0]["fovs"], staged["cores"][0]["fovs"]), 1
    ):
        path = manifest.raw_path(original_fovs[original["id"]])
        target["path"] = "FOV_" + original["id"] + ".tif"
        receipts.append(verified_copy(path, work / "raw" / target["path"], original["sha256"]))
        write_json(
            work / "status.json",
            {
                "updated_utc": utc(),
                "phase": "staging",
                "FOVs": number,
                "expected_FOVs": len(source["cores"][0]["fovs"]),
            },
        )
    for container in ("groups", "cores"):
        names = (
            ("flatfield", "darkfield", "model_metadata")
            if container == "groups"
            else ("positions",)
        )
        for key in names:
            original = source[container][0].get(key)
            if not original:
                continue
            name = key + Path(original).suffix
            receipts.append(verified_copy(original, work / "assets" / name, asset_hashes[original]))
            staged[container][0][key] = str(work / "assets" / name)
            replay[container][0][key] = "assets/" + name
    write_json(work / "manifest.json", staged)
    write_json(work / "replay-manifest.json", replay)
    write_json(
        work / "staging-receipt.json",
        {
            "verified_utc": utc(),
            "files": receipts,
            "expected_source_hashes_verified": True,
            "bulk_copy_status_required": False,
        },
    )
    return Manifest(work / "manifest.json")


def pixel_hash(image):
    """Hash native dtype/shape and every pixel bit, independent of TIFF encoding."""
    digest = hashlib.sha256(f"{image.dtype.str}:{image.shape}".encode())
    for y in range(0, image.shape[0], 256):
        digest.update(np.ascontiguousarray(image[y : y + 256]).tobytes())
    return digest.hexdigest()


def export_image(source, target):
    """Write losslessly compressed final TIFF and verify all native pixel bits."""
    image = read_image(source)
    before = pixel_hash(image)
    tifffile.imwrite(
        target,
        image,
        bigtiff=True,
        metadata={"axes": "YX"},
        compression="deflate",
        compressionargs={"level": 1},
        predictor=3 if image.dtype.kind == "f" else 2,
        rowsperstrip=256,
        maxworkers=2,
    )
    restored = read_image(target)
    if pixel_hash(restored) != before:
        raise ValueError(f"Compressed export changed native pixels: {target}")
    return {
        "file": target.name,
        "shape_yx": list(image.shape),
        "dtype": str(image.dtype),
        "pixel_sha256": before,
        "sha256": sha256(target),
        "bytes": target.stat().st_size,
    }


def review_records(summary, job, exports, delivery_findings):
    """Keep image availability, diagnostic execution and image quality separate."""
    core = job["source"]["cores"][0]
    observed = {r["stage"]: r for r in summary["stage_results"] if r["unit"] == core["uid"]}
    rows = []
    for stage in dict.fromkeys([*CORE_STAGES, *observed]):
        row = observed.get(stage)
        rows.append(
            {
                k: row.get(k, {} if k in {"metrics", "outputs", "provenance"} else [])
                for k in (
                    "stage",
                    "execution",
                    "qc",
                    "metrics",
                    "findings",
                    "outputs",
                    "provenance",
                )
            }
            if row
            else {
                "stage": stage,
                "execution": "not_run",
                "qc": "not_assessed",
                "metrics": {},
                "findings": ["No stage result was recorded."],
                "outputs": {},
                "provenance": {},
            }
        )
    from tma_qc.qc.assessment import artifact_assessments, quality_status

    quality = quality_status(rows)
    execution_rows = [r for r in rows if r["stage"] not in {"publication", "archive_export"}]
    execution = (
        "error"
        if any(r["execution"] == "error" for r in execution_rows)
        else "blocked"
        if any(r["execution"] == "blocked" for r in execution_rows)
        else "partial"
        if any(r["execution"] != "complete" for r in execution_rows)
        else "complete"
    )
    qc_stages = {"tile_qc", "geometry_qc", "illumination_qc", "mosaic_qc", "tissue"}
    names = {r["file"] for r in exports}
    return {
        "schema_version": 2,
        "unit": core["uid"],
        "slide": core["slide"],
        "acquisition": core["acquisition"],
        "core": core["core"],
        "channel": core["channel"],
        "job_identity": job["identity"],
        "attempt_id": summary.get("attempt_id"),
        "pipeline_execution": execution,
        "final_image_available": {"corrected.tif", "coverage.tif"} <= names,
        "native_archive_available": {"corrected.tif", "coverage.tif"} <= names,
        "qc_execution_complete": all(
            r["execution"] == "complete" for r in rows if r["stage"] in qc_stages
        ),
        "quality_status": quality,
        "expert_review": "pending",
        "automatic_image_quality_release": False,
        "stages": rows,
        "artifact_assessments": artifact_assessments(rows),
        "delivery_findings": delivery_findings,
    }


def write_review(products, qc, evidence):
    """Publish an inspectable report even when no core image can be exported."""
    folder = products / "qc"
    folder.mkdir(exist_ok=True)
    for row in qc["artifact_assessments"]:
        name = Path(row["evidence"]["path"]).name if row["evidence"] else None
        row["published_evidence"] = "evidence/" + name if name in evidence else None
    write_json(folder / "qc.json", qc)
    write_json(products / "qc.json", qc)  # Keep the archive's established record name.
    fields = ["unit", "job_identity", "stage", "execution", "qc", "findings", "metrics_json"]
    table = [
        {
            "unit": qc["unit"],
            "job_identity": qc["job_identity"],
            **{k: row[k] for k in ("stage", "execution", "qc")},
            "findings": " | ".join(row["findings"]),
            "metrics_json": json.dumps(row["metrics"], allow_nan=False, sort_keys=True),
        }
        for row in qc["stages"]
    ]
    for name, selected in (
        ("stages.csv", table),
        (
            "review-needed.csv",
            [r for r in table if r["execution"] != "complete" or r["qc"] != "pass"],
        ),
    ):
        with (folder / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(selected)
    body = f"<h1>{escape(qc['unit'])}: QC review</h1>"
    body += '<p><a href="../index.html">Core output</a> · <a href="stages.csv">All stages CSV</a> · <a href="review-needed.csv">Review queue CSV</a> · <a href="qc.json">Full records</a></p>'
    body += f"<p>Final image available locally: <strong>{qc['final_image_available']}</strong> · Native archive available: <strong>{qc['native_archive_available']}</strong> · Processing: {escape(qc['pipeline_execution'])} · Image quality: {escape(qc['quality_status'])} · Expert review: pending.</p>"
    body += "<p>REVIEW is a request for inspection. A diagnostic execution error does not establish an image-quality defect. Automatic acceptance is disabled; blur/fold segmentation and tissue-area ratios are unvalidated.</p>"
    body += "<h2>Artifact measurements</h2><table><tr><th>Target</th><th>Assessment</th><th>Measured regions</th><th>Limits</th></tr>"
    for row in qc["artifact_assessments"]:
        body += f"<tr><td>{escape(row['artifact'])}</td><td>{escape(row['assessment'])}</td><td>{row['measurement_count']}</td><td>{escape(row['qualification'])}</td></tr>"
    body += "</table><p>NOT ASSESSED does not establish artifact-free tissue. Spatial locations and measurement units are retained in the evidence JSON files.</p>"
    for note in qc["delivery_findings"]:
        body += f'<p class="notice">{escape(note)}</p>'
    body += '<label>Show stages: <select id="filter"><option value="all">All</option><option value="execution">Execution errors / blocked / not run</option><option value="fail">Image-quality failures</option><option value="review">Needs review</option></select></label>'
    body += "<table><thead><tr><th>Stage</th><th>Execution</th><th>QC</th><th>Findings and measurements</th></tr></thead><tbody>"
    for row in qc["stages"]:
        actual_fail = row["execution"] == "complete" and row["qc"] == "fail"
        flags = f"{'execution ' if row['execution'] != 'complete' else ''}{'fail ' if actual_fail else ''}{'review' if row['qc'] != 'pass' or row['execution'] != 'complete' else ''}"
        findings = "<br>".join(escape(v) for v in row["findings"])
        body += f'<tr data-flags="{flags}"><td>{escape(row["stage"])}</td><td>{escape(row["execution"])}</td><td>{escape(row["qc"])}</td><td>{findings}<details><summary>Measurements</summary><pre>{escape(json.dumps(row["metrics"], indent=2))}</pre></details></td></tr>'
    body += '</tbody></table><script>document.getElementById("filter").addEventListener("change",e=>{document.querySelectorAll("tr[data-flags]").forEach(r=>r.hidden=e.target.value!=="all"&&!r.dataset.flags.split(" ").includes(e.target.value))});</script>'
    for name, caption in (
        ("comparison.png", "Raw and corrected reconstruction"),
        ("tile-grid.png", "Before-stitch FOVs"),
        ("geometry-overlaps.png", "Largest measured overlap residuals: native centre crops"),
        ("photometric-overlaps.png", "Largest corrected overlap brightness mismatch"),
        ("tile-boundaries.png", "Known tile-overlap midlines; layout, not an artifact mask"),
        ("tile-projections.png", "Raw-supported and low-signal intensity projections"),
    ):
        if name in evidence:
            content = base64.b64encode(evidence[name].read_bytes()).decode()
            body += f'<h2>{caption}</h2><img alt="{caption}" src="data:image/png;base64,{content}">'
    style = "body{font:16px system-ui;max-width:1400px;margin:24px auto;padding:0 16px}table{border-collapse:collapse;width:100%;margin:20px 0}th,td{padding:10px;border:1px solid #ccc;text-align:left;vertical-align:top}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}.notice{background:#fff1d1;padding:12px}tr[hidden]{display:none}"
    (folder / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Core QC review</title>'
        + f"<style>{style}</style><body>{body}</body></html>"
    )
    link = (
        '<a href="results.zip">Download native corrected core and replay assets</a>'
        if qc["native_archive_available"]
        else "The reconstructed image is retained on the processing workstation; its native archive could not be created. See the QC report for the export error."
        if qc["final_image_available"]
        else "No reconstructed core image is available. See the stage report for the reason."
    )
    (products / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Core output and QC</title><body style="font:16px system-ui;max-width:1000px;margin:24px auto;padding:16px">'
        + f"<h1>{escape(qc['unit'])}</h1><h2>Final output</h2><p>{link}</p>"
        + f"<h2>QC review</h2><p>Processing: {escape(qc['pipeline_execution'])} · Image quality: {escape(qc['quality_status'])} · Expert review: pending.</p>"
        + '<p><a href="qc/index.html">Open stage-by-stage QC report</a></p><p>Image availability does not indicate QC acceptance.</p></body></html>'
    )


def package_results(work, summary, job):
    """Export a verified reconstruction and an independently available QC report."""
    root, products = work / "run", work / "products"
    rows = {r["stage"]: r for r in summary["stage_results"]}

    def asset(stage, name):
        if rows[stage]["execution"] != "complete":
            raise ValueError(f"Stage did not complete: {stage}")
        record = rows[stage]["outputs"][name]
        path = (root / record["path"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Stage artifact is outside its run directory")
        if sha256(path) != record["sha256"]:
            raise ValueError(f"Local stage artifact changed: {path}")
        return path

    exports = [
        ("mosaic", "mosaic", "corrected.tif"),
        ("mosaic", "coverage", "coverage.tif"),
        ("tissue", "candidate", "possible-tissue.tif"),
        ("tissue", "unknown", "unknown.tif"),
    ]
    products.mkdir(exist_ok=True)
    records, notes, evidence = [], [], {}
    for stage, name, output in exports:
        if stage == "tissue" and not {"corrected.tif", "coverage.tif"} <= {
            r["file"] for r in records
        }:
            continue
        try:
            source = asset(stage, name)
            require_space(
                [(work, 2 * source.stat().st_size, "Lossless export and archive")],
                job["config"]["storage"],
            )
            write_json(
                work / "status.json",
                {"updated_utc": utc(), "phase": "exporting", "product": output},
            )
            records.append(export_image(source, products / output))
        except (KeyError, OSError, ValueError, NeedsInput) as exc:
            notes.append(f"Output unavailable: {output}: {exc}")
    if not {"corrected.tif", "coverage.tif"} <= {r["file"] for r in records}:
        records = []  # Never deliver a partial/unchecked required image pair as final.
    write_json(
        products / "exports.json",
        {
            "products": records,
            "lossless_native_pixels_verified": True,
            "scope": "Available final AF products; provisional masks are optional; checkpoints remain local",
        },
    )
    for stage, name, output in (
        ("mosaic_qc", "comparison", "comparison.png"),
        ("tile_qc", "tile_grid", "tile-grid.png"),
        ("tile_qc", "measurements", "tile-measurements.json"),
        ("geometry_qc", "evidence", "geometry-overlaps.png"),
        ("geometry_qc", "overlaps", "geometry-overlaps.json"),
        ("illumination_qc", "evidence", "photometric-overlaps.png"),
        ("illumination_qc", "overlaps", "photometric-overlaps.json"),
        ("mosaic_qc", "boundary_evidence", "tile-boundaries.png"),
        ("mosaic_qc", "projection_evidence", "tile-projections.png"),
        ("mosaic_qc", "boundaries", "boundary-qc.json"),
        ("mosaic_qc", "periodicity", "periodicity-qc.json"),
    ):
        try:
            target = products / "qc" / "evidence" / output
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(asset(stage, name), target)
            evidence[output] = target
        except (KeyError, OSError, ValueError) as exc:
            notes.append(f"Review evidence unavailable: {output}: {exc}")
    qc = review_records(summary, job, records, notes)
    qc["full_local_run"] = str(root)
    write_review(products, qc, evidence)
    archive = work / "results.zip"
    delivery_stages = []
    if qc["final_image_available"]:
        partial = work / (".results-" + uuid.uuid4().hex + ".partial")
        try:
            required = sum((products / r["file"]).stat().st_size for r in records)
            require_space([(work, required, "Final native archive")], job["config"]["storage"])
            with zipfile.ZipFile(
                partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True
            ) as z:
                for name in [*(r["file"] for r in records), "qc.json", "exports.json"]:
                    z.write(products / name, name)
                for path in sorted((work / "assets").iterdir()):
                    z.write(path, "assets/" + path.name)
                for name in (
                    "job.json",
                    "source-manifest.json",
                    "replay-manifest.json",
                    "staging-receipt.json",
                ):
                    z.write(work / name, name)
                z.write(Path(__file__), "software/process_source_core.py")
            with zipfile.ZipFile(partial) as z:
                if z.testzip() is not None:
                    raise ValueError("Final result archive CRC verification failed")
            partial.replace(archive)
        except (OSError, ValueError, NeedsInput, zipfile.BadZipFile) as exc:
            note = f"Native archive unavailable; verified local TIFFs retained: {type(exc).__name__}: {exc}"
            notes.append(note)
            delivery_stages.append(
                {
                    "unit": job["source"]["cores"][0]["uid"],
                    "stage": "archive_export",
                    "execution": "error",
                    "qc": "not_assessed",
                    "findings": [note],
                    "metrics": {},
                    "outputs": {},
                    "provenance": {},
                }
            )
            qc = review_records(
                {**summary, "stage_results": [*summary["stage_results"], *delivery_stages]},
                job,
                records,
                notes,
            )
            qc.update(native_archive_available=False, full_local_run=str(root))
            write_review(products, qc, evidence)
        finally:
            partial.unlink(missing_ok=True)
    files = [(products / "index.html", "index.html")]
    files.extend(
        (path, path.relative_to(products).as_posix())
        for path in sorted((products / "qc").rglob("*"))
        if path.is_file()
    )
    if qc["native_archive_available"]:
        files.append((archive, "results.zip"))
    package = {
        "job_identity": job["identity"],
        "archive_sha256": sha256(archive) if qc["native_archive_available"] else None,
        "archive_bytes": archive.stat().st_size if qc["native_archive_available"] else 0,
        "review_sha256": sha256(products / "index.html"),
        "exports": records,
        "delivery_findings": notes,
        "delivery_stage_results": delivery_stages,
        "core_status": {
            k: qc[k]
            for k in (
                "final_image_available",
                "native_archive_available",
                "pipeline_execution",
                "qc_execution_complete",
                "quality_status",
                "expert_review",
                "automatic_image_quality_release",
            )
        },
        "files": [
            {
                "source": p.relative_to(work).as_posix(),
                "path": name,
                "sha256": sha256(p),
                "bytes": p.stat().st_size,
            }
            for p, name in files
        ],
        "created_utc": utc(),
    }
    write_json(work / "package.json", package)
    return package


def publish(work, destination, package):
    """Verify delivered image/report components before marking publication complete."""
    destination.mkdir(parents=True, exist_ok=True)
    owner = destination / "owner.json"
    if owner.exists():
        if read_json(owner)["job_identity"] != package["job_identity"]:
            raise FileExistsError("Publication directory belongs to a different frozen job")
    elif any(destination.iterdir()):
        raise FileExistsError("Use an empty, dedicated publication directory")
    else:
        write_json(owner, {"job_identity": package["job_identity"]})
    write_json(
        destination / "publication.json",
        {
            "job_identity": package["job_identity"],
            "complete": False,
            "updated_utc": utc(),
            "phase": "publishing_and_verifying",
        },
    )

    def progress(count, total):
        write_json(
            work / "status.json",
            {
                "updated_utc": utc(),
                "phase": "publishing",
                "bytes_written": count,
                "archive_bytes": total,
            },
        )

    delivered, failures = [], []
    for record in package["files"]:
        source, target = (work / record["source"]).resolve(), destination / record["path"]
        if not source.is_relative_to(work.resolve()) or not target.resolve().is_relative_to(
            destination.resolve()
        ):
            raise ValueError("Publication artifact is outside its dedicated directory")
        try:
            verified_copy(
                source,
                target,
                record["sha256"],
                progress if record["path"] == "results.zip" else None,
            )
            delivered.append(record["path"])
        except OSError as exc:
            failures.append({"path": record["path"], "error": f"{type(exc).__name__}: {exc}"})
    image_published = (
        package["core_status"]["native_archive_available"] and "results.zip" in delivered
    )
    qc_published = all(
        r["path"] in delivered for r in package["files"] if r["path"].startswith("qc/")
    )
    publication = {
        **package,
        "published_utc": utc(),
        "complete": not failures,
        "archive": str(destination / "results.zip")
        if package["core_status"]["native_archive_available"]
        else None,
        "review": str(destination / "qc/index.html"),
        "destination_files_SHA256_verified": not failures,
        "destination_archive_SHA256_verified": image_published,
        "final_output_published": image_published,
        "qc_report_published": qc_published,
        "publication_errors": failures,
        "automatic_image_quality_release": False,
    }
    write_json(destination / "publication.json", publication)
    write_json(work / "publication.json", publication)
    write_json(
        work / "status.json",
        {
            "updated_utc": utc(),
            "phase": "complete" if not failures else "publication_partial",
            "publication": publication,
        },
    )
    return publication


def run(args):
    manifest = Manifest(args.manifest)
    source = select_core(manifest, args.core)
    work, destination = Path(args.workspace).resolve(), Path(args.out).resolve()
    if work == destination or work in destination.parents or destination in work.parents:
        raise ValueError("Working and publication directories must be separate")
    if filesystem_type(work) in {"cifs", "smbfs", "nfs", "nfs4", "fuse.sshfs"}:
        raise ValueError("Use local SSD storage for the working directory")
    for path in (work, destination):
        if path == manifest.raw_root or manifest.raw_root in path.parents:
            raise ValueError("Working/publication locations must be outside original raw storage")
    config = load_config(args.config)
    config["runtime"]["cache_root"] = str(work / "cache")
    assets = [
        source["cores"][0].get("positions"),
        *(source["groups"][0].get(key) for key in ("flatfield", "darkfield", "model_metadata")),
    ]
    asset_hashes = {p: sha256(p) for p in assets if p}
    job = {
        "source": source,
        "config": config,
        "code_sha256": code_identity(),
        "recipe_sha256": sha256(Path(__file__)),
        "asset_hashes": asset_hashes,
    }
    job["identity"] = fingerprint(job)
    with workspace_lock(work):
        if (work / "job.json").exists() and read_json(work / "job.json")["identity"] != job[
            "identity"
        ]:
            raise ValueError(
                "Workspace belongs to different inputs/configuration/code; use a new one"
            )
        write_json(work / "job.json", job)
        write_json(work / "source-manifest.json", source)
        if (work / "package.json").exists():
            package = read_json(work / "package.json")
            if package["job_identity"] != job["identity"]:
                raise ValueError("Frozen local publication package changed")
            for record in package["files"]:
                local = (work / record["source"]).resolve()
                if not local.is_relative_to(work) or sha256(local) != record["sha256"]:
                    raise ValueError("Frozen local publication package changed")
        else:
            summary = {"stage_results": []}
            phase, runner = "staging", None
            try:
                staged = stage_inputs(manifest, source, work, asset_hashes)
                write_json(work / "status.json", {"updated_utc": utc(), "phase": "processing"})
                phase = "processing"
                runner = Runner(staged, work / "run", config, resume=True)
                summary = runner.run()
            except Exception as exc:
                if runner is not None:
                    summary = runner.summary()
                    if set(CORE_STAGES) <= {r["stage"] for r in summary["stage_results"]}:
                        phase = "report_generation"
                summary["stage_results"].append(
                    {
                        "unit": args.core,
                        "stage": phase,
                        "execution": "error",
                        "qc": "not_assessed",
                        "metrics": {},
                        "findings": [f"{type(exc).__name__}: {exc}"],
                        "outputs": {},
                        "provenance": {},
                    }
                )
            finally:
                if runner is not None:
                    runner.state.close()
            package = package_results(work, summary, job)
        return publish(work, destination, package)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Explicit ready source manifest")
    parser.add_argument("--core", required=True, help="One AF core UID")
    parser.add_argument("--workspace", required=True, help="Dedicated local SSD working directory")
    parser.add_argument("--out", required=True, help="Dedicated final publication directory")
    parser.add_argument("--config")
    publication = run(parser.parse_args())
    print(publication)
    if publication["core_status"]["pipeline_execution"] == "blocked":
        sys.exit(2)
    if (
        not publication["complete"]
        or publication["core_status"]["pipeline_execution"] != "complete"
        or not publication["core_status"]["final_image_available"]
        or not publication["core_status"]["native_archive_available"]
    ):
        sys.exit(1)


if __name__ == "__main__":
    main()
