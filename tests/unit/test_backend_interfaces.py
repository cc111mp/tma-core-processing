import numpy as np
import pytest
import tifffile

from tma_qc.backends.mist import estimate_positions
from tma_qc.demo import create_demo
from tma_qc.io.manifest import Manifest
from tma_qc.provenance import read_json, write_json
from tma_qc.schema import NeedsInput


def test_mist_inputs_use_imagej_readable_classic_tiff(tmp_path, monkeypatch):
    manifest_path, _ = create_demo(tmp_path / "demo")
    data = read_json(manifest_path)
    data["cores"][0]["metadata"].update(
        grid_reviewed=True,
        grid={
            "rows": 2,
            "columns": 2,
            "numbering_pattern": "HORIZONTALCOMBING",
            "origin": "UL",
            "horizontal_overlap": 40,
            "vertical_overlap": 40,
        },
    )
    write_json(manifest_path, data)
    manifest = Manifest(manifest_path)
    output = tmp_path / "geometry"
    output.mkdir()

    def fake_java(command, **kwargs):
        images = list((output / "mist-inputs").glob("*.tif"))
        assert len(images) == 4
        for image in images:
            with tifffile.TiffFile(image) as f:
                assert not f.is_bigtiff
                assert f.asarray().dtype == np.uint16
        (output / "img-global-positions-1.txt").write_text(
            "\n".join(
                f"file: FOV_{n:05d}.tif; corr: 1.0; position: ({x}, {y}); grid: ({n % 2}, {n // 2});"
                for n, (x, y) in enumerate([(0, 0), (24, 0), (0, 24), (24, 24)], 1)
            )
        )

    monkeypatch.setattr("tma_qc.backends.mist.subprocess.run", fake_java)
    positions = estimate_positions(
        manifest.cores[0],
        manifest,
        output,
        {"mist_classpath": "example.jar", "downsample": 4, "timeout_seconds": 10},
    )
    assert positions[-1] == {"id": "4", "x": 96, "y": 96}


def test_mist_requires_review_of_an_imported_unknown_sequence(tmp_path):
    manifest_path, _ = create_demo(tmp_path / "demo")
    manifest = Manifest(manifest_path)
    core = manifest.cores[0]
    core.metadata.update(
        grid_reviewed=True,
        sequence_reviewed=False,
        grid={
            "rows": 2,
            "columns": 2,
            "numbering_pattern": "HORIZONTALCOMBING",
            "origin": "UL",
            "horizontal_overlap": 40,
            "vertical_overlap": 40,
        },
    )
    with pytest.raises(NeedsInput, match="acquisition sequence"):
        estimate_positions(core, manifest, tmp_path / "out", {"mist_classpath": "stub.jar"})
