from __future__ import annotations

import html
import os
from pathlib import Path

from ..qc.assessment import artifact_assessments, input_assessment, quality_status

STYLE = """body{font:16px system-ui;margin:0;background:#121720;color:#e8edf4}main{max-width:1250px;margin:auto;padding:28px}a{color:#9cceff}table{border-collapse:collapse;width:100%;margin:18px 0}td,th{padding:9px;border-bottom:1px solid #394457;text-align:left;vertical-align:top}img{max-width:100%;height:auto}code{overflow-wrap:anywhere}.notice{padding:16px;background:#3a3020;border-left:4px solid #e5ad49}.review,.blocked{color:#f5c86b}.fail,.error{color:#ff9797}.pass{color:#93d6ae}.muted{color:#aab7c8}.card{padding:14px;background:#1c2432;margin:16px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere} .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:18px}"""


def escape(value):
    return html.escape(str(value), quote=True)


def href(page, target):
    return escape(Path(os.path.relpath(target, page.parent)).as_posix())


def document(title, body):
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{escape(title)}</title><style>{STYLE}</style><main><h1>{escape(title)}</h1>{body}</main></html>'


def stage_table(root, page, rows):
    body = "<table><thead><tr><th>Stage</th><th>Execution</th><th>QC</th><th>Findings and evidence</th></tr></thead><tbody>"
    for row in rows:
        links = " ".join(
            f'<a href="{href(page, root / value["path"])}">{escape(name)}</a>'
            for name, value in row["outputs"].items()
            if not name.startswith("artifact:")
        )
        findings = "<br>".join(escape(v) for v in row["findings"])
        body += f'<tr><td>{escape(row["stage"])}</td><td class="{escape(row["execution"])}">{escape(row["execution"])}</td><td class="{escape(row["qc"])}">{escape(row["qc"])}</td><td>{findings}<br>{links}<details><summary>Measurements</summary><pre>{escape(__import__("json").dumps(row["metrics"], indent=2))}</pre></details></td></tr>'
    return body + "</tbody></table>"


def artifact_table(root, page, rows):
    body = "<h2>Artifact measurements</h2><p>REVIEW means measurements are available, not a confirmed defect. NOT ASSESSED is not a pass. Artifact locations and raw/corrected values are saved in the stage evidence.</p>"
    body += "<table><thead><tr><th>Target</th><th>Assessment</th><th>Measured regions</th><th>Evidence and limits</th></tr></thead><tbody>"
    for row in artifact_assessments(rows):
        item = row["evidence"]
        link = f'<a href="{href(page, root / item["path"])}">View evidence</a> · ' if item else ""
        for label, item in row["additional_evidence"].items():
            link += f'<a href="{href(page, root / item["path"])}">{escape(label)}</a> · '
        count = str(row["measurement_count"])
        if row["measurement_unit"]:
            count += " " + escape(row["measurement_unit"])
        if row["artifact"] == "tile_pitch_pattern":
            counts = row["measurement_breakdown"]
            count += f"<br>Global {counts['global']} · local {counts['local']}"
        reason = "<br>" + escape(row["unassessed_reason"]) if row["unassessed_reason"] else ""
        exclusions = row["measurement_breakdown"].get("excluded_by_reason", {})
        excluded = (
            "<br>Unavailable: "
            + "; ".join(
                f"{escape(name.replace('_', ' '))}: {value}" for name, value in exclusions.items()
            )
            if exclusions
            else ""
        )
        body += f"<tr><td>{escape(row['artifact'])}</td><td>{escape(row['assessment'])}{reason}</td><td>{count}</td><td>{link}{escape(row['qualification'])}{excluded}</td></tr>"
    return body + "</tbody></table>"


def detail_summary(rows):
    """Expose where local-detail sampling exists without claiming a blur mask."""
    stage = next((row for row in rows if row["stage"] == "tile_qc"), {})
    if stage.get("execution") != "complete":
        return ""
    metrics = stage.get("metrics", {})
    body = "<h2>Focus and local detail</h2>"
    body += (
        f"<p>Detail measurements available in {metrics.get('detail_assessed_windows', 0)} / "
        f"{metrics.get('windows', 0)} windows. Low or unavailable detail does not establish "
        "defocus; naturally smooth tissue and weak signal also limit measurement.</p>"
    )
    reasons = metrics.get("detail_unavailable_by_reason", {})
    if reasons:
        body += (
            "<p>Unavailable: "
            + "; ".join(
                f"{escape(reason.replace('_', ' '))}: {count}" for reason, count in reasons.items()
            )
            + ".</p>"
        )
    sampling = metrics.get("detail_sampling")
    if sampling:
        body += (
            f"<p>Window bounds cover {sampling['fraction']:.1%} of native FOV pixels; "
            f"gradient/variance measurement interiors cover {sampling['measurement_interior_fraction']:.1%}. "
            "These are geometric sampling fractions across all FOVs, including unavailable "
            "windows. They are not tissue coverage, valid-focus area or blurred-area fractions.</p>"
        )
    else:
        body += "<p>Geometric detail-sampling coverage was not recorded in this observation.</p>"
    return body


def illumination_summary(rows):
    """Expose assessment coverage and signal scale without defining acceptance."""
    stages = {row["stage"]: row for row in rows}
    stage = stages.get("illumination_qc", {})
    if stage.get("execution") != "complete":
        return ""
    metrics = stage.get("metrics", {})
    assessed = metrics.get("assessed_pairs", metrics.get("raw_supported_pairs", 0))
    candidates = metrics.get("candidate_pairs", assessed + metrics.get("excluded_pairs", 0))
    fraction = metrics.get("assessed_pair_fraction")
    fraction_text = f" ({fraction:.1%})" if fraction is not None else ""
    body = "<h2>Illumination review</h2>"
    body += (
        f"<p>Overlap pairs assessed: <strong>{assessed} / {candidates}</strong>{fraction_text}. "
        "Excluded overlap pairs are unknown. This is overlap-pair assessment coverage, "
        "not a tissue-area measurement.</p>"
    )
    reasons = metrics.get("exclusions_by_reason", {})
    if reasons:
        body += (
            "<p>Excluded: "
            + "; ".join(
                f"{escape(reason.replace('_', ' '))}: {count}" for reason, count in reasons.items()
            )
            + ".</p>"
        )
    counts = metrics.get("relative_difference_assessed_pairs")
    if counts:
        body += (
            "<p>Relative mean mismatch measured: "
            f"raw {counts['raw']}, corrected {counts['corrected']}, "
            f"paired change {counts['paired']}. Two zero means have no relative scale.</p>"
        )
    signal = metrics.get("signal_diagnostics", {})

    def numbers(values):
        return " / ".join(f"{v:.4g}" for v in values) if values is not None else "not assessed"

    gain = metrics.get("model_gain_p01_p50_p99")
    if gain is None:
        gain = stages.get("illumination", {}).get("metrics", {}).get("gain_p01_p50_p99")
    if gain is not None:
        body += f"<p>Model gain p01 / p50 / p99: {numbers(gain)}.</p>"
    ratios = signal.get("span_ratio_minimum_median_maximum")
    if signal:
        body += (
            "<p>Corrected / raw signal-span ratio, minimum / median / maximum across FOVs: "
            f"<strong>{numbers(ratios)}</strong>. "
            "Span is p99 minus p01 at identical sampled coordinates. "
            "These measurements describe signal scale; biological intensity preservation remains unvalidated.</p>"
        )
    collapsed = signal.get("collapsed_fov_ids", [])
    if collapsed:
        body += (
            '<p class="notice">Review flag: exactly constant corrected output from varying raw input in FOVs '
            + escape(", ".join(collapsed))
            + ".</p>"
        )
    if signal.get("fovs"):
        body += "<details><summary>Per-FOV signal measurements</summary><table><thead><tr><th>FOV</th><th>Raw p01 / p50 / p99 counts</th><th>Corrected p01 / p50 / p99 counts</th><th>Span ratio</th></tr></thead><tbody>"
        for row in signal["fovs"]:
            ratio = row["span_ratio_corrected_over_raw"]
            ratio_text = f"{ratio:.4g}" if ratio is not None else "not assessed"
            body += (
                f"<tr><td>{escape(row['fov'])}</td>"
                f"<td>{numbers(row['raw_percentiles_p01_p50_p99_counts'])}</td>"
                f"<td>{numbers(row['corrected_percentiles_p01_p50_p99_counts'])}</td>"
                f"<td>{ratio_text}</td></tr>"
            )
        body += "</tbody></table><p>Percentiles use matched grid samples. Constant-output flags use every native pixel. No acceptance thresholds are applied.</p></details>"
    return body


def mosaic_summary(rows):
    """Expose local/tail evidence without turning its magnitude into acceptance."""
    stage = next((row for row in rows if row["stage"] == "mosaic_qc"), {})
    if stage.get("execution") != "complete":
        return ""
    metrics = stage.get("metrics", {})
    if not metrics.get("worst_seams") and not metrics.get("strongest_local_patterns"):
        return ""
    body = "<h2>Mosaic review measurements</h2><p>Seam medians, tail values and local patterns describe different parts of the image. Larger values identify evidence to inspect; no acceptance threshold is applied.</p>"
    body += "<table><thead><tr><th>Signal subset</th><th>Pair</th><th>Corrected seam p95 / maximum</th><th>Native location</th></tr></thead><tbody>"
    for stratum, images in metrics.get("worst_seams", {}).items():
        for seam in images.get("corrected", [])[:2]:
            body += (
                f"<tr><td>{escape(stratum)}</td><td>{escape(seam['a'])} / {escape(seam['b'])}</td>"
                f"<td>{seam['p95_absolute_step_over_raw_range']:.4g} / "
                f"{seam['maximum_absolute_step_over_raw_range']:.4g}</td>"
                f"<td>{escape(seam['axis'])} cut {seam['cut_native_pixel']}; "
                f"worst line {seam['maximum_step_line_native_pixel']}</td></tr>"
            )
    body += "</tbody></table><p>Steps use the same raw normalization range (p99 minus p01, with a one-count minimum). Locations refer to known overlap midlines and measured line samples, not confirmed artifact masks.</p>"
    patterns = [
        row for row in metrics.get("strongest_local_patterns", []) if row["image"] == "corrected"
    ]
    if patterns:
        body += "<table><thead><tr><th>Axis / signal subset</th><th>Native XY region</th><th>Largest fitted harmonic amplitude, counts</th></tr></thead><tbody>"
        for row in patterns:
            amplitude = max(row["fit"]["harmonic_amplitudes_counts"])
            body += (
                f"<tr><td>{escape(row['axis'])} / {escape(row['stratum'])}</td>"
                f"<td>{escape(row['region_bbox_xyxy'])}</td><td>{amplitude:.4g}</td></tr>"
            )
        body += "</tbody></table><p>Native profiles are fitted before display averaging. Local strips help expose patterns that cancel globally; remaining variation can also be anatomy.</p>"
    return body


def build_core(root, core, rows, pairs, observation=None):
    page = root / "reports" / "cores" / (core.uid + ".html")
    page.parent.mkdir(parents=True, exist_ok=True)
    body = '<p><a href="../index.html">Batch report</a></p>'
    body += '<p class="notice">Experimental review output. Processing completion is separate from QC acceptance. Focus/fold segmentation and absolute intensity preservation are unvalidated.</p>'
    body += f"<p>Acquisition: {escape(core.acquisition)} · Core: {escape(core.core)} · Channel: {escape(core.channel)}</p>"
    if observation:
        body += f'<p class="notice">Observation: {escape(observation["observation"])} · Last processed: {escape(observation.get("updated_utc"))}. Historical raw files and artifact contents are not reverified by reporting.</p>'
        if observation.get("reason"):
            body += '<p class="notice">' + escape(observation["reason"]) + "</p>"
    if core.blocked_reasons:
        body += (
            '<p class="notice">Blocked input: ' + escape("; ".join(core.blocked_reasons)) + "</p>"
        )
    by_stage = {r["stage"]: r for r in rows}
    integrity = input_assessment(rows)
    body += (
        f"<p>Input verification: <strong>{escape(integrity['assessment'])}</strong>. "
        "This checks manifest-listed files; an external expected-core/FOV inventory is "
        "needed to establish physical acquisition completeness.</p>"
    )
    body += f"<p>Image quality: <strong>{escape(quality_status(rows))}</strong> · Expert review: pending.</p>"
    reconstruction = by_stage.get("mosaic", {})
    if (
        reconstruction.get("execution") == "complete"
        and {"mosaic", "coverage"} <= reconstruction.get("outputs", {}).keys()
    ):
        body += "<h2>Reconstructed output</h2><p>Reconstruction recorded complete; diagnostic errors do not remove the image. These links do not imply scientific acceptance.</p><p>"
        for key, label in (
            ("mosaic", "Native corrected core TIFF"),
            ("coverage", "Acquisition coverage TIFF"),
        ):
            item = reconstruction["outputs"][key]
            body += f'<a href="{href(page, root / item["path"])}">{label}</a> · '
        body += "</p>"
    else:
        body += '<p class="notice">No completed reconstruction is recorded for this observation. Check the execution stages below.</p>'
    body += artifact_table(root, page, rows)
    body += detail_summary(rows)
    body += illumination_summary(rows)
    body += mosaic_summary(rows)
    for stage, key, caption in [
        (
            "mosaic_qc",
            "comparison",
            "Raw and selected output, identical positions and shared raw count window",
        ),
        ("tile_qc", "tile_grid", "Before-stitch FOVs, shared count window"),
        ("geometry_qc", "evidence", "Largest measured overlap residuals: native centre crops"),
        (
            "illumination_qc",
            "evidence",
            "Largest corrected photometric mismatch: paired native centre crops",
        ),
        (
            "mosaic_qc",
            "boundary_evidence",
            "Known tile-overlap midlines; layout, not an artifact mask",
        ),
        (
            "mosaic_qc",
            "projection_evidence",
            "Raw-supported and low-signal projections; unknown bins remain gaps",
        ),
        (
            "mosaic_qc",
            "local_projection_evidence",
            "Strongest local strip projections, with native region coordinates",
        ),
    ]:
        item = by_stage.get(stage, {}).get("outputs", {}).get(key)
        if item:
            body += f'<h2>{escape(caption)}</h2><a href="{href(page, root / item["path"])}"><img src="{href(page, root / item["path"])}" alt="{escape(caption)}"></a>'
    body += stage_table(root, page, rows)
    if pairs:
        body += "<h2>AF–H&amp;E section pairs</h2>"
        for pair, pair_rows in pairs:
            body += f"<h3>{escape(pair.uid)} — {escape(pair.relationship)}</h3>"
            for row in pair_rows:
                overlay = row["outputs"].get("overlay")
                if overlay:
                    body += f'<img src="{href(page, root / overlay["path"])}" alt="AF green, H&E structural proxy magenta">'
            body += stage_table(root, page, pair_rows)
    else:
        body += '<p class="notice">No explicit H&amp;E section pair processed for this core. Registration QC is NOT ASSESSED.</p>'
    body += "<h2>Original FOV records</h2><table><tr><th>FOV</th><th>Source path</th><th>Expected SHA256</th></tr>"
    for fov in core.fovs:
        body += f"<tr><td>{escape(fov.id)}</td><td><code>{escape(fov.path)}</code></td><td><code>{escape(fov.sha256)}</code></td></tr>"
    body += "</table>"
    page.write_text(document(core.slide + " / " + core.core, body), encoding="utf-8")
    return page
