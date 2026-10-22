"""Source-first processing and publication controls using synthetic AF inputs."""

import csv
import importlib.util
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tma_qc.demo import create_demo
from tma_qc.io.images import read_image, write_tiff
from tma_qc.io.manifest import Manifest
from tma_qc.provenance import read_json, sha256, write_json
from tma_qc.schema import NeedsInput
from tma_qc.stages import geometry, illumination, mosaic, tile_qc, tissue

SPEC = importlib.util.spec_from_file_location(
    "source_recipe", Path(__file__).resolve().parents[2] / "examples/process_source_core.py"
)
recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe)


def source_fixture(tmp_path):
    manifest, config = create_demo(tmp_path / "source")
    data = read_json(manifest)
    data["cores"][0]["metadata"]["source_FOV_set_verified"] = True
    data["pairs"] = []
    write_json(manifest, data)
    return SimpleNamespace(
        manifest=manifest,
        config=config,
        core="demo-A1",
        workspace=tmp_path / "work",
        out=tmp_path / "home",
    )


def test_verified_copy_checks_bytes_and_preserves_pinned_staging(tmp_path):
    source, target = tmp_path / "raw.tif", tmp_path / "stage/raw.tif"
    source.write_bytes(b"original camera bytes")
    expected = sha256(source)
    assert not recipe.verified_copy(source, target, expected)["reused"]
    assert target.read_bytes() == source.read_bytes()
    source.unlink()  # A frozen staged input can outlive a NAS connection.
    assert recipe.verified_copy(source, target, expected)["reused"]
    target.write_bytes(b"corruption")
    with pytest.raises(ValueError, match="Existing staged/published file differs"):
        recipe.verified_copy(source, target, expected)


def test_copy_with_wrong_source_hash_leaves_no_final_or_partial_file(tmp_path):
    source, target = tmp_path / "raw", tmp_path / "stage/raw"
    source.write_bytes(b"unexpected source bytes")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        recipe.verified_copy(source, target, "0" * 64)
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


@pytest.mark.parametrize("complete", [False, "true"])
def test_source_completeness_requires_explicit_verified_boolean(tmp_path, complete):
    args = source_fixture(tmp_path)
    data = read_json(args.manifest)
    data["cores"][0]["metadata"]["source_FOV_set_verified"] = complete
    write_json(args.manifest, data)
    with pytest.raises(ValueError, match="completeness"):
        recipe.select_core(Manifest(args.manifest), args.core)


def test_af_recipe_does_not_silently_drop_section_pairs(tmp_path):
    manifest, _ = create_demo(tmp_path / "source")
    data = read_json(manifest)
    data["cores"][0]["metadata"]["source_FOV_set_verified"] = True
    write_json(manifest, data)
    with pytest.raises(ValueError, match="section pairs"):
        recipe.select_core(Manifest(manifest), "demo-A1")


@pytest.mark.parametrize("dtype", ["float32", "uint8"])
def test_lossless_native_export_preserves_negative_high_values_and_masks(tmp_path, dtype):
    image = np.array([[-125.25, 0, 0.125], [65536.5, 100000.0, 1.0]], dtype=np.float32)
    if dtype == "uint8":
        image = np.array([[0, 1, 255], [1, 0, 1]], dtype=np.uint8)
    source, target = tmp_path / "native.tif", tmp_path / "compressed.tif"
    write_tiff(source, image)
    record = recipe.export_image(source, target)
    restored = read_image(target)
    assert restored.dtype == image.dtype
    assert restored.tobytes() == image.tobytes()
    assert record["sha256"] == sha256(target)


def test_source_core_publishes_replayable_native_results_without_full_raw_copy(
    tmp_path, monkeypatch
):
    args = source_fixture(tmp_path)
    publication = recipe.run(args)
    assert publication["complete"] and publication["destination_archive_SHA256_verified"]
    assert not publication["automatic_image_quality_release"]
    assert publication["core_status"]["final_image_available"]
    assert publication["core_status"]["qc_execution_complete"]
    assert (args.out / "qc/index.html").is_file()
    assert 'href="qc/index.html"' in (args.out / "index.html").read_text()
    with (args.out / "qc/stages.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 9
    assert sha256(args.out / "results.zip") == publication["archive_sha256"]
    assert 'href="results.zip"' in (args.out / "index.html").read_text()
    with zipfile.ZipFile(args.out / "results.zip") as archive:
        assert archive.testzip() is None
        assert {"corrected.tif", "coverage.tif", "possible-tissue.tif", "unknown.tif"} <= set(
            archive.namelist()
        )
        assert not any(name.startswith("raw/") for name in archive.namelist())
        archive.extractall(tmp_path / "replay")
    replay = Manifest(tmp_path / "replay/replay-manifest.json")
    assert len(replay.cores) == 1
    assert Path(replay.groups[0].flatfield).is_file()
    assert Path(replay.cores[0].positions).is_file()
    assert replay.raw_path(replay.cores[0].fovs[0]).is_file()
    exports = read_json(tmp_path / "replay/exports.json")
    for record in exports["products"]:
        image = read_image(tmp_path / "replay" / record["file"])
        assert recipe.pixel_hash(image) == record["pixel_sha256"]
    qc = read_json(tmp_path / "replay/qc.json")
    assert len(qc["stages"]) == 9
    assert all(row["execution"] == "complete" for row in qc["stages"])

    def no_reprocessing(*args, **kwargs):
        raise AssertionError("A verified package should publish without reconstructing again")

    monkeypatch.setattr(recipe, "stage_inputs", no_reprocessing)
    assert recipe.run(args)["archive_sha256"] == publication["archive_sha256"]
    (args.out / "results.zip").write_bytes(b"damaged NAS archive")
    with pytest.raises(ValueError, match="Existing staged/published file differs"):
        recipe.run(args)
    assert not read_json(args.out / "publication.json")["complete"]


def test_conflicting_workspace_and_publication_jobs_are_rejected(tmp_path):
    args = source_fixture(tmp_path)
    recipe.run(args)
    args.config.write_text(args.config.read_text() + "\n[qc]\nlandmark_tolerance_pixels = 2\n")
    with pytest.raises(ValueError, match="Workspace belongs"):
        recipe.run(args)
    args.workspace = tmp_path / "different-work"
    with pytest.raises(FileExistsError, match="different frozen job"):
        recipe.run(args)


def test_workspace_and_output_cannot_overlap(tmp_path):
    args = source_fixture(tmp_path)
    args.out = args.workspace / "publication"
    with pytest.raises(ValueError, match="must be separate"):
        recipe.run(args)


@pytest.mark.parametrize(
    "module,function,stage",
    [
        (tile_qc, "run", "tile_qc"),
        (geometry, "evaluate", "geometry_qc"),
        (illumination, "evaluate", "illumination_qc"),
        (mosaic, "evaluate", "mosaic_qc"),
        (tissue, "run", "tissue"),
    ],
)
def test_diagnostic_crashes_preserve_final_image_and_identify_stage(
    tmp_path, monkeypatch, module, function, stage
):
    args = source_fixture(tmp_path)

    def crash(*args, **kwargs):
        raise RuntimeError("Injected diagnostic failure")

    monkeypatch.setattr(module, function, crash)
    publication = recipe.run(args)
    assert publication["complete"] and publication["destination_files_SHA256_verified"]
    assert publication["core_status"]["final_image_available"]
    assert publication["core_status"]["pipeline_execution"] == "error"
    assert not publication["core_status"]["qc_execution_complete"]
    assert publication["core_status"]["quality_status"] == "review"
    assert sha256(args.out / "results.zip") == publication["archive_sha256"]
    record = read_json(args.out / "qc/qc.json")
    failed = next(r for r in record["stages"] if r["stage"] == stage)
    assert failed["execution"] == "error"
    assert "Injected diagnostic failure" in " ".join(failed["findings"])
    with zipfile.ZipFile(args.out / "results.zip") as archive:
        assert {"corrected.tif", "coverage.tif"} <= set(archive.namelist())
        if stage == "tissue":
            assert "possible-tissue.tif" not in archive.namelist()
    mosaic_row = next(
        r
        for r in read_json(args.workspace / "run/stage-results.json")["stage_results"]
        if r["stage"] == "mosaic"
    )
    original = read_image(args.workspace / "run" / mosaic_row["outputs"]["mosaic"]["path"])
    exported = read_image(args.workspace / "products/corrected.tif")
    assert recipe.pixel_hash(exported) == recipe.pixel_hash(original)


def test_image_quality_failure_does_not_prevent_native_image_publication(tmp_path, monkeypatch):
    args = source_fixture(tmp_path)
    original = tile_qc.run

    def failed_quality(*args, **kwargs):
        payload = original(*args, **kwargs)
        payload.qc = "fail"
        payload.findings.append("Synthetic image-quality rejection")
        return payload

    monkeypatch.setattr(tile_qc, "run", failed_quality)
    publication = recipe.run(args)
    assert publication["core_status"]["final_image_available"]
    assert publication["core_status"]["pipeline_execution"] == "complete"
    assert publication["core_status"]["quality_status"] == "fail"
    assert not publication["automatic_image_quality_release"]
    with (args.out / "qc/review-needed.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert any(r["stage"] == "tile_qc" and r["qc"] == "fail" for r in rows)


def test_failed_reconstruction_still_publishes_report_without_fake_image(tmp_path, monkeypatch):
    args = source_fixture(tmp_path)

    def crash(*args, **kwargs):
        raise RuntimeError("Injected reconstruction failure")

    monkeypatch.setattr(mosaic, "run", crash)
    publication = recipe.run(args)
    assert publication["complete"] and publication["destination_files_SHA256_verified"]
    assert publication["archive"] is None
    assert not publication["core_status"]["final_image_available"]
    assert not publication["destination_archive_SHA256_verified"]
    assert not (args.out / "results.zip").exists()
    assert (args.out / "qc/index.html").exists()
    stages = {r["stage"]: r for r in read_json(args.out / "qc/qc.json")["stages"]}
    assert stages["mosaic"]["execution"] == "error"
    assert stages["mosaic_qc"]["execution"] == "blocked"


def test_failed_source_staging_has_report_and_no_unverified_image(tmp_path):
    args = source_fixture(tmp_path)
    manifest = Manifest(args.manifest)
    manifest.raw_path(manifest.cores[0].fovs[0]).write_bytes(b"changed acquisition")
    publication = recipe.run(args)
    assert not publication["core_status"]["final_image_available"]
    assert not (args.out / "results.zip").exists()
    stages = {r["stage"]: r for r in read_json(args.out / "qc/qc.json")["stages"]}
    assert stages["staging"]["execution"] == "error"
    assert stages["index"]["execution"] == "not_run"
    assert stages["index"]["qc"] == "not_assessed"


def test_corrupt_reconstruction_cannot_be_delivered_as_final(tmp_path, monkeypatch):
    args = source_fixture(tmp_path)
    original = recipe.package_results

    def corrupt_then_package(work, summary, job):
        row = next(r for r in summary["stage_results"] if r["stage"] == "mosaic")
        (work / "run" / row["outputs"]["mosaic"]["path"]).write_bytes(b"corrupt image")
        return original(work, summary, job)

    monkeypatch.setattr(recipe, "package_results", corrupt_then_package)
    publication = recipe.run(args)
    assert not publication["core_status"]["final_image_available"]
    assert not (args.out / "results.zip").exists()
    record = read_json(args.out / "qc/qc.json")
    assert any("Local stage artifact changed" in v for v in record["delivery_findings"])


def test_published_qc_records_are_verified_on_resume(tmp_path):
    args = source_fixture(tmp_path)
    recipe.run(args)
    (args.out / "qc/stages.csv").write_text("corrupted QC record")
    with pytest.raises(ValueError, match="Existing staged/published file differs"):
        recipe.run(args)
    assert not read_json(args.out / "publication.json")["complete"]


def test_qc_delivery_error_does_not_block_native_archive_and_can_resume(tmp_path, monkeypatch):
    args = source_fixture(tmp_path)
    original = recipe.verified_copy

    def fail_report_only(source, target, expected, progress=None):
        if Path(target) == args.out / "qc/index.html":
            raise OSError("Injected QC report write failure")
        return original(source, target, expected, progress)

    monkeypatch.setattr(recipe, "verified_copy", fail_report_only)
    publication = recipe.run(args)
    assert not publication["complete"]
    assert publication["final_output_published"]
    assert not publication["qc_report_published"]
    assert publication["destination_archive_SHA256_verified"]
    assert sha256(args.out / "results.zip") == publication["archive_sha256"]
    monkeypatch.setattr(recipe, "verified_copy", original)

    def no_reprocessing(*args, **kwargs):
        raise AssertionError("Only delivery should resume")

    monkeypatch.setattr(recipe, "stage_inputs", no_reprocessing)
    resumed = recipe.run(args)
    assert resumed["complete"] and resumed["qc_report_published"]
    assert resumed["archive_sha256"] == publication["archive_sha256"]


def test_batch_review_includes_unrun_cores_without_mutating_processing(tmp_path):
    args = source_fixture(tmp_path)
    recipe.run(args)
    source_hash = sha256(args.workspace / "run/stage-results.json")
    data = read_json(args.manifest)
    pending = {**data["cores"][0], "uid": "pending-core", "core": "A2"}
    data["cores"].append(pending)
    batch_manifest = tmp_path / "batch-manifest.json"
    write_json(batch_manifest, data)
    plan_path = tmp_path / "plan.json"
    write_json(
        plan_path,
        {
            "manifest": str(batch_manifest),
            "manifest_sha256": sha256(batch_manifest),
            "cores": [
                {"uid": args.core, "workspace": str(args.workspace)},
                {"uid": "pending-core", "workspace": str(tmp_path / "pending-work")},
            ],
        },
    )
    spec = importlib.util.spec_from_file_location(
        "batch_review", Path(recipe.__file__).with_name("review_source_batch.py")
    )
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    output = tmp_path / "snapshot"
    assert review.build_review(plan_path, output) == output / "index.html"
    assert sha256(args.workspace / "run/stage-results.json") == source_hash
    assert not (tmp_path / "pending-work").exists()
    with (output / "cores.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = {r["uid"]: r for r in csv.DictReader(stream)}
    assert rows[args.core]["final_image_recorded"] == "True"
    assert rows["pending-core"]["pipeline_execution"] == "partial"
    assert rows["pending-core"]["quality_status"] == "not_assessed"
    assert (output / "pending-core/qc/index.html").exists()
    with pytest.raises(FileExistsError):
        review.build_review(plan_path, output)


@pytest.mark.parametrize("failure", ["capacity", "write"])
def test_archive_creation_failure_preserves_native_exports_and_delivers_qc(
    tmp_path, monkeypatch, failure
):
    args = source_fixture(tmp_path)
    if failure == "capacity":
        original = recipe.require_space

        def capacity(requests, settings=None):
            if any(label == "Final native archive" for _, _, label in requests):
                raise NeedsInput("Injected archive capacity failure")
            return original(requests, settings)

        monkeypatch.setattr(recipe, "require_space", capacity)
    else:
        original = recipe.zipfile.ZipFile

        def archive(path, mode="r", *args, **kwargs):
            if mode == "w":
                raise OSError("Injected archive write failure")
            return original(path, mode, *args, **kwargs)

        monkeypatch.setattr(recipe.zipfile, "ZipFile", archive)
    publication = recipe.run(args)
    assert publication["core_status"]["final_image_available"]
    assert not publication["core_status"]["native_archive_available"]
    assert publication["qc_report_published"]
    assert not publication["final_output_published"]
    assert publication["archive"] is None
    assert (args.workspace / "products/corrected.tif").exists()
    assert not (args.out / "results.zip").exists()
    assert 'href="results.zip"' not in (args.out / "index.html").read_text()
    record = read_json(args.out / "qc/qc.json")
    assert record["pipeline_execution"] == "complete"
    assert record["quality_status"] == "review"
    assert any("archive" in note.lower() for note in record["delivery_findings"])
    assert (
        next(r for r in record["stages"] if r["stage"] == "archive_export")["execution"] == "error"
    )
    plan = tmp_path / "plan.json"
    write_json(
        plan,
        {
            "manifest": str(args.manifest),
            "manifest_sha256": sha256(args.manifest),
            "cores": [{"uid": args.core, "workspace": str(args.workspace)}],
        },
    )
    spec = importlib.util.spec_from_file_location(
        "archive_failure_review", Path(recipe.__file__).with_name("review_source_batch.py")
    )
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    overview = tmp_path / "overview"
    review.build_review(plan, overview)
    with (overview / "cores.csv").open(encoding="utf-8-sig", newline="") as stream:
        summary = next(csv.DictReader(stream))
    assert summary["pipeline_execution"] == "complete"
    assert summary["native_archive_recorded"] == "False"
    assert "archive_export" in summary["execution_problem_stages"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            recipe.__file__,
            "--manifest",
            str(args.manifest),
            "--core",
            args.core,
            "--workspace",
            str(args.workspace),
            "--out",
            str(args.out),
            "--config",
            str(args.config),
        ],
    )
    with pytest.raises(SystemExit) as status:
        recipe.main()
    assert status.value.code == 1


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_batch_review_records_unavailable_preview_and_keeps_other_cores(tmp_path, damage):
    args = source_fixture(tmp_path)
    recipe.run(args)
    summary = read_json(args.workspace / "run/stage-results.json")
    mosaic_qc = next(r for r in summary["stage_results"] if r["stage"] == "mosaic_qc")
    preview = args.workspace / "run" / mosaic_qc["outputs"]["comparison"]["path"]
    if damage == "missing":
        preview.unlink()
    else:
        preview.write_bytes(b"Changed QC preview")
    source_hash = sha256(args.workspace / "run/stage-results.json")
    data = read_json(args.manifest)
    data["cores"].append({**data["cores"][0], "uid": "pending-core", "core": "A2"})
    manifest = tmp_path / "batch-manifest.json"
    write_json(manifest, data)
    plan = tmp_path / "plan.json"
    write_json(
        plan,
        {
            "manifest": str(manifest),
            "manifest_sha256": sha256(manifest),
            "cores": [
                {"uid": args.core, "workspace": str(args.workspace)},
                {"uid": "pending-core", "workspace": str(tmp_path / "pending-work")},
            ],
        },
    )
    spec = importlib.util.spec_from_file_location(
        "batch_preview_review", Path(recipe.__file__).with_name("review_source_batch.py")
    )
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    output = tmp_path / "snapshot"
    review.build_review(plan, output)
    record = read_json(output / args.core / "qc/qc.json")
    assert any("Preview unavailable" in note for note in record["delivery_findings"])
    assert (
        "Raw and corrected reconstruction" not in (output / args.core / "qc/index.html").read_text()
    )
    assert (output / "pending-core/qc/index.html").exists()
    assert sha256(args.workspace / "run/stage-results.json") == source_hash
