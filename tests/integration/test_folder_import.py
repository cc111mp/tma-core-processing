"""Generated identities must remain usable through stitching and AF/H&E pairing."""

import shutil

from tma_qc.config import load_config
from tma_qc.demo import create_demo
from tma_qc.io.folder import import_raw_folder
from tma_qc.io.manifest import Manifest
from tma_qc.pipeline import run_pipeline
from tma_qc.preflight import check
from tma_qc.provenance import write_json


def test_imported_nas_tree_runs_with_explicit_geometry_models_and_pairing(tmp_path):
    path, config = create_demo(tmp_path / "synthetic")
    original = Manifest(path)
    raw = tmp_path / "raw"
    for core in original.cores:
        for fov in core.fovs:
            target = (
                raw / f"original/slide/slide/{core.core}/middle lamp 275/stitch/FOV_{fov.id}.tif"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original.raw_path(fov), target)
    manifest = tmp_path / "manifest.json"
    data = import_raw_folder(raw, manifest, dataset_id="synthetic-folder-import")
    assert data["pairs"] == []
    models = original.snapshot()["groups"][0]
    for group in data["groups"]:
        group.update({key: models[key] for key in ["flatfield", "darkfield", "model_metadata"]})
    by_core = {c.core: c for c in original.cores}
    for core in data["cores"]:
        core["positions"] = by_core[core["core"]].positions
        core["geometry_reviewed"] = True
    pair = original.snapshot()["pairs"][0]
    pair["af_core"] = next(c["uid"] for c in data["cores"] if c["core"] == "A1")
    data["pairs"] = [pair]
    write_json(manifest, data)
    output = tmp_path / "results"
    assert check(Manifest(manifest), load_config(config), output)["ready_for_execution"]
    summary = run_pipeline(manifest, output, config)
    assert summary["execution_errors"] == summary["blocked_stages"] == 0
    rows = {(r["unit"], r["stage"]): r for r in summary["stage_results"]}
    for core in data["cores"]:
        mosaic = rows[core["uid"], "mosaic"]
        assert (output / mosaic["outputs"]["mosaic"]["path"]).is_file()
        assert core["acquisition"] in mosaic["outputs"]["mosaic"]["path"]
    assert rows[pair["uid"], "registration"]["execution"] == "complete"
    assert rows[pair["uid"], "registration_qc"]["execution"] == "complete"
