"""One public workflow: explicit pairing table through native export and review."""

import csv

from tma_qc.api import import_pairs, run
from tma_qc.cli import main
from tma_qc.demo import create_demo
from tma_qc.provenance import read_json, sha256, write_json


def paired_study(tmp_path):
    manifest, config = create_demo(tmp_path / "study")
    data = read_json(manifest)
    data["pairs"] = []
    write_json(manifest, data)
    table = tmp_path / "pairing" / "sections.csv"
    table.parent.mkdir()
    with table.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "uid",
                "af_core",
                "he_path",
                "relationship",
                "he_slide_label",
                "transform",
                "landmarks",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "uid": "AF-01-HE-27-A1",
                "af_core": "demo-A1",
                "he_path": "../study/assets/he.tif",
                "relationship": "neighboring_sections",
                "he_slide_label": "HE-27",
                "transform": "../study/assets/transform.json",
                "landmarks": "../study/assets/landmarks.json",
            }
        )
    paired = tmp_path / "manifests" / "paired.json"
    return manifest, config, table, paired


def test_public_pair_workflow_retains_source_identity_and_runs_all_twelve_stages(tmp_path):
    manifest, config, table, paired = paired_study(tmp_path)
    originals = {path: sha256(path) for path in (manifest.parent / "inputs").rglob("*.tif")}
    originals[manifest] = sha256(manifest)
    data = import_pairs(manifest, table, paired)
    output = tmp_path / "results"
    assert (
        main(
            [
                "plan",
                "--manifest",
                str(paired),
                "--config",
                str(config),
                "--out",
                str(output),
                "--core",
                "demo-A1",
            ]
        )
        == 0
    )
    summary = run(paired, output, config, selected=["demo-A1"])
    assert summary["execution_errors"] == summary["blocked_stages"] == 0
    assert len(summary["stage_results"]) == 12
    pair = data["pairs"][0]
    assert pair["metadata"]["he_slide_label"] == "HE-27"
    stages = {r["stage"]: r for r in summary["stage_results"]}
    assert stages["registration"]["outputs"]["aligned_he"]
    assert stages["registration_qc"]["metrics"]["independent_evaluation_available"]
    assert (output / "reports/index.html").is_file()
    assert stages["mosaic_qc"]["outputs"]["local_projection_evidence"]
    assert all(sha256(path) == digest for path, digest in originals.items())
    resumed = run(paired, output, config, selected=["demo-A1"])
    assert all(row["cached"] for row in resumed["stage_results"])


def test_changed_he_blocks_registration_while_af_reconstruction_remains_available(tmp_path):
    manifest, config, table, paired = paired_study(tmp_path)
    assert (
        main(
            [
                "import-pairs",
                "--manifest",
                str(manifest),
                "--pairs",
                str(table),
                "--out",
                str(paired),
            ]
        )
        == 0
    )
    (manifest.parent / "assets/he.tif").write_bytes(b"changed after explicit pairing")
    summary = run(paired, tmp_path / "results", config, selected=["demo-A1"])
    stages = {row["stage"]: row for row in summary["stage_results"]}
    assert stages["mosaic"]["execution"] == "complete"
    assert stages["he_qc"]["execution"] == "error"
    assert stages["registration"]["execution"] == "blocked"
    assert "aligned_he" not in stages["registration"]["outputs"]
