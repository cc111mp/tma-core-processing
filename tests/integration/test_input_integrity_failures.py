"""Common damaged inputs fail locally without modifying original sources."""

import hashlib
import shutil
import struct
from pathlib import Path

import numpy as np
import pytest
import tifffile

from tests.integration.test_pipeline import fixture, rows
from tma_qc.config import load_config
from tma_qc.io.manifest import Manifest
from tma_qc.pipeline import run_pipeline
from tma_qc.preflight import check
from tma_qc.provenance import read_json, sha256, write_json


def change_source(path, mode):
    if mode == "missing":
        path.unlink()
    elif mode == "empty":
        path.write_bytes(b"II" + struct.pack("<HI", 42, 0))
    elif mode == "wrong-format":
        path.write_bytes(b"This is not a TIFF file")
    elif mode == "wrong-shape":
        tifffile.imwrite(path, np.ones((32, 48), np.uint16))
    elif mode == "wrong-dtype":
        tifffile.imwrite(path, np.ones((160, 160), np.uint8))
    elif mode == "multiple-planes":
        with tifffile.TiffWriter(path) as writer:
            writer.write(np.ones((160, 160), np.uint16))
            writer.write(np.ones((160, 160), np.uint16))
    elif mode == "truncated":
        tifffile.imwrite(path, np.ones((160, 160), np.uint16))
        path.write_bytes(path.read_bytes()[:-16])
    elif mode == "missing-strip":
        tifffile.imwrite(path, np.ones((160, 160), np.uint16), rowsperstrip=40)
        with tifffile.TiffFile(path, mode="r+b") as tif:
            tag = tif.pages[0].tags["StripOffsets"]
            values = list(tag.value)
            values[0] = 0
            tag.overwrite(tuple(values))
    elif mode == "compressed-payload":
        tifffile.imwrite(path, np.ones((160, 160), np.uint16), compression="deflate")
        with tifffile.TiffFile(path) as tif:
            offset, count = tif.pages[0].dataoffsets[0], tif.pages[0].databytecounts[0]
        with path.open("r+b") as stream:
            stream.seek(offset)
            stream.write(b"\x00" * count)


def raw_snapshot(raw_root):
    return {
        str(path.relative_to(raw_root)): (sha256(path), path.stat().st_mtime_ns)
        for path in raw_root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "empty",
        "wrong-format",
        "wrong-shape",
        "wrong-dtype",
        "multiple-planes",
        "truncated",
        "missing-strip",
        "compressed-payload",
    ],
)
def test_malformed_raw_with_frozen_hash_has_local_failure_and_preserves_archive(tmp_path, mode):
    manifest_path, config_path, output = fixture(tmp_path)
    source = manifest_path.parent / "inputs/A1/FOV_1.tif"
    change_source(source, mode)
    data = read_json(manifest_path)
    if source.exists():
        # Exercise structural/decode validation, beyond the transfer hash gate.
        data["cores"][0]["fovs"][0]["sha256"] = sha256(source)
        write_json(manifest_path, data)
    manifest = Manifest(manifest_path)
    before = raw_snapshot(manifest.raw_root)
    plan = check(manifest, load_config(config_path), output)
    if mode == "compressed-payload":
        assert plan["ready_for_execution"]  # Preflight intentionally does not decode.
    else:
        assert not plan["ready_for_execution"]
        assert any(issue["scope"] == "demo-A1" for issue in plan["issues"])
    result = rows(run_pipeline(manifest_path, output, config_path))
    assert result["demo-A1", "index"]["execution"] == "error"
    assert result["demo-A1", "mosaic"]["execution"] == "blocked"
    assert result["demo-B3", "mosaic"]["execution"] == "complete"
    assert raw_snapshot(manifest.raw_root) == before
    assert (output / "reports/index.html").is_file()


@pytest.mark.parametrize(
    "mode", ["empty", "wrong-format", "multiple-planes", "missing-strip", "truncated"]
)
def test_malformed_he_is_pair_local_in_preflight_and_runtime(tmp_path, mode):
    manifest_path, config_path, output = fixture(tmp_path)
    data = read_json(manifest_path)
    source = Path(data["pairs"][0]["he_path"])
    if not source.is_absolute():
        source = manifest_path.parent / source
    change_source(source, mode)
    data["pairs"][0]["he_sha256"] = sha256(source)
    write_json(manifest_path, data)
    before = (sha256(source), source.stat().st_mtime_ns)
    plan = check(Manifest(manifest_path), load_config(config_path), output)
    assert not plan["ready_for_execution"]
    assert any(issue["scope"] == "demo-pair" for issue in plan["issues"])
    result = rows(run_pipeline(manifest_path, output, config_path))
    assert result["demo-pair", "he_qc"]["execution"] == "error"
    assert result["demo-pair", "registration"]["execution"] == "blocked"
    assert result["demo-A1", "mosaic"]["execution"] == "complete"
    assert result["demo-B3", "mosaic"]["execution"] == "complete"
    assert (sha256(source), source.stat().st_mtime_ns) == before


def test_empty_blocked_acquisition_is_retained_without_guessing_missing_fovs(tmp_path):
    manifest_path, config_path, output = fixture(tmp_path)
    data = read_json(manifest_path)
    core = data["cores"][0]
    core["fovs"] = []
    with pytest.raises(ValueError, match="empty or duplicate FOV"):
        Manifest(manifest_path, data=data)
    core["blocked_reasons"] = ["No supported observed FOV set; acquisition requires review"]
    write_json(manifest_path, data)
    result = rows(run_pipeline(manifest_path, output, config_path))
    assert result["demo-A1", "index"]["execution"] == "blocked"
    assert result["demo-B3", "index"]["execution"] == "complete"
    validated = output / result["demo-B3", "index"]["outputs"]["inputs"]["path"]
    assert read_json(validated)["validation"]["physical_acquisition_completeness"] == "not_assessed"


@pytest.mark.parametrize("channels", [3, 4])
def test_planar_he_with_all_channels_is_valid_in_plan_and_runtime(tmp_path, channels):
    manifest_path, config_path, output = fixture(tmp_path)
    data = read_json(manifest_path)
    source = manifest_path.parent / data["pairs"][0]["he_path"]
    image = np.arange(256 * 256 * channels, dtype=np.uint16).reshape(channels, 256, 256)
    tifffile.imwrite(
        source, image, photometric="rgb", planarconfig="separate", compression="deflate"
    )
    data["pairs"][0]["he_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    write_json(manifest_path, data)
    assert check(Manifest(manifest_path), load_config(config_path), output)["ready_for_execution"]
    result = rows(run_pipeline(manifest_path, output, config_path))
    assert result["demo-pair", "he_qc"]["execution"] == "complete"
    assert result["demo-pair", "registration"]["execution"] == "complete"


def test_he_changed_after_header_read_does_not_pass_preflight(tmp_path, monkeypatch):
    from tma_qc import preflight

    manifest_path, config_path, output = fixture(tmp_path)
    source = Manifest(manifest_path).pairs[0].he_path
    original = preflight.validate_tiff_plane

    def changed(tif, **kwargs):
        result = original(tif, **kwargs)
        with Path(source).open("ab") as stream:
            stream.write(b"changed after checked header")
        return result

    monkeypatch.setattr(preflight, "validate_tiff_plane", changed)
    plan = check(Manifest(manifest_path), load_config(config_path), output)
    assert not plan["ready_for_execution"]
    assert any(
        issue["scope"] == "demo-pair" and "Verified source changed" in issue["reason"]
        for issue in plan["issues"]
    )


def test_external_flatfield_pixels_are_not_trusted_or_used_for_correction(tmp_path):
    manifest_path, config_path, output = fixture(tmp_path)
    data = read_json(manifest_path)
    assets = manifest_path.parent / "assets"
    shutil.copyfile(assets / "flatfield.tif", assets / "healthy-flatfield.tif")
    data["groups"].append(
        {
            **data["groups"][0],
            "id": "healthy-group",
            "flatfield": "assets/healthy-flatfield.tif",
        }
    )
    data["cores"][1]["group"] = "healthy-group"
    description = (
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06" UUID="urn:uuid:main">'
        '<Image ID="Image:0"><Pixels ID="Pixels:0" DimensionOrder="XYZCT" Type="float" '
        'SizeX="160" SizeY="160" SizeZ="1" SizeC="1" SizeT="1">'
        '<Channel ID="Channel:0:0" SamplesPerPixel="1"/><TiffData IFD="0" PlaneCount="1">'
        '<UUID FileName="external-flatfield.tif">urn:uuid:external</UUID>'
        "</TiffData></Pixels></Image></OME>"
    )
    tifffile.imwrite(
        assets / "flatfield.tif",
        np.ones((160, 160), np.float32),
        description=description,
        metadata=None,
    )
    tifffile.imwrite(assets / "external-flatfield.tif", np.full((160, 160), 999, np.float32))
    write_json(manifest_path, data)
    raw_before = raw_snapshot(Manifest(manifest_path).raw_root)
    plan = check(Manifest(manifest_path), load_config(config_path), output)
    assert any(
        issue["scope"] == "synthetic-group" and "external OME-TIFF" in issue["reason"]
        for issue in plan["issues"]
    )
    result = rows(run_pipeline(manifest_path, output, config_path))
    assert result["demo-A1", "index"]["execution"] == "complete"
    assert result["demo-A1", "illumination"]["execution"] == "error"
    assert "external OME-TIFF" in result["demo-A1", "illumination"]["findings"][0]
    assert result["demo-A1", "mosaic"]["execution"] == "blocked"
    assert result["demo-B3", "mosaic"]["execution"] == "complete"
    assert raw_snapshot(Manifest(manifest_path).raw_root) == raw_before
