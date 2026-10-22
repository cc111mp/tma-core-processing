"""The help interface remains usable before image dependencies are installed."""

import json
import subprocess
import sys
from pathlib import Path


def test_module_help_without_site_packages():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-S", "-m", "tma_qc", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "run" in result.stdout and "fit-cidre" in result.stdout


def test_import_folder_generates_a_manifest_without_image_dependencies(tmp_path):
    root = Path(__file__).resolve().parents[2]
    raw = tmp_path / "raw"
    tile = raw / "project/slide/slide/A1/middle lamp 275/stitch/FOV_1.tif"
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b"source identity; TIFF header validation happens in plan")
    output = tmp_path / "manifest.json"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "tma_qc",
            "import-folder",
            "--raw-root",
            str(raw),
            "--out",
            str(output),
            "--dataset-id",
            "example-study",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert data["dataset_id"] == "example-study"
    assert len(data["cores"]) == 1 and data["pairs"] == []
    assert "candidate FOV sets" in result.stdout
