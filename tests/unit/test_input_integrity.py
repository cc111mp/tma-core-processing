"""Container integrity cannot silently substitute missing pixels or planes."""

import struct
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile

from tma_qc.io.images import mapped_image, read_image
from tma_qc.provenance import sha256
from tma_qc.qc.integrity import validate_tiff_plane, verify_core
from tma_qc.schema import FOV, Core

PROFILE = {"shape": [20, 24], "dtype": "uint16"}


def verify(path, *, decode=True, reader=None):
    core = Core("core", "acq", "slide", "A1", "AF", "group", [FOV("1", path.name, sha256(path))])
    manifest = SimpleNamespace(raw_path=lambda fov: path)
    return verify_core(core, manifest, PROFILE, decode=decode, reader=reader)


@pytest.mark.parametrize("decode", [False, True])
def test_empty_tiff_is_a_controlled_input_error(tmp_path, decode):
    path = tmp_path / "empty.tif"
    path.write_bytes(b"II" + struct.pack("<HI", 42, 0))
    with pytest.raises(ValueError, match="no image plane"):
        verify(path, decode=decode)


@pytest.mark.parametrize("second_shape", [(20, 24), (8, 8)])
@pytest.mark.parametrize("decode", [False, True])
def test_multiple_native_planes_are_not_implicitly_selected(tmp_path, second_shape, decode):
    path = tmp_path / "planes.tif"
    with tifffile.TiffWriter(path) as writer:
        writer.write(np.full(PROFILE["shape"], 123, np.uint16))
        writer.write(np.full(second_shape, 999, np.uint16))
    with pytest.raises(ValueError, match="multiple pages/series"):
        verify(path, decode=decode)


@pytest.mark.parametrize("mode", ["zero-offset", "zero-count", "truncated", "short-count", "table"])
@pytest.mark.parametrize("tiled", [False, True])
@pytest.mark.parametrize("decode", [False, True])
def test_missing_or_truncated_pixel_segments_fail_before_decoder(tmp_path, mode, tiled, decode):
    path = tmp_path / "segments.tif"
    tifffile.imwrite(
        path,
        np.arange(480, dtype=np.uint16).reshape(20, 24),
        **({"tile": (16, 16)} if tiled else {"rowsperstrip": 7}),
    )
    if mode == "truncated":
        path.write_bytes(path.read_bytes()[:-2])
    else:
        with tifffile.TiffFile(path, mode="r+b") as tif:
            prefix = "Tile" if tiled else "Strip"
            tag = tif.pages[0].tags[prefix + ("Offsets" if mode == "zero-offset" else "ByteCounts")]
            values = list(tag.value)
            if mode in {"zero-offset", "zero-count"}:
                values[0] = 0
            elif mode == "short-count":
                values[0] -= 2
            else:
                values = values[:-1]
            tag.overwrite(tuple(values))

    def forbidden(_):
        pytest.fail("Malformed segment table reached the decoder")

    with pytest.raises(ValueError, match="TIFF.*segment"):
        verify(path, decode=decode, reader=forbidden)


@pytest.mark.parametrize("compression", [None, "deflate"])
@pytest.mark.parametrize("tiled", [False, True])
@pytest.mark.parametrize("byteorder", ["<", ">"])
def test_valid_uint16_segments_and_byte_order_preserve_all_counts(
    tmp_path, compression, tiled, byteorder
):
    path = tmp_path / "valid.tif"
    image = np.arange(480, dtype=np.uint16).reshape(20, 24)
    tifffile.imwrite(
        path,
        image,
        compression=compression,
        byteorder=byteorder,
        **({"tile": (16, 16)} if tiled else {"rowsperstrip": 7}),
    )
    header, sources = verify(path, decode=False)
    decoded, _ = verify(path)
    assert header["decoded_files"] == 0 and decoded["decoded_files"] == 1
    assert decoded["physical_acquisition_completeness"] == "not_assessed"
    assert sources == {str(path): sha256(path)}
    np.testing.assert_array_equal(read_image(path), image)


@pytest.mark.parametrize("channels", [3, 4])
@pytest.mark.parametrize("planar", [False, True])
@pytest.mark.parametrize("compression", [None, "deflate"])
def test_he_planar_and_contiguous_color_segment_contract(tmp_path, channels, planar, compression):
    path = tmp_path / "he.tif"
    image = np.arange(20 * 24 * channels, dtype=np.uint16).reshape(20, 24, channels)
    tifffile.imwrite(
        path,
        np.moveaxis(image, -1, 0) if planar else image,
        photometric="rgb",
        planarconfig="separate" if planar else "contig",
        compression=compression,
        rowsperstrip=7,
    )
    with tifffile.TiffFile(path) as tif:
        series = validate_tiff_plane(tif, label="H&E", allow_pyramid=True)
        assert series.axes == ("SYX" if planar else "YXS")
    np.testing.assert_array_equal(read_image(path, rgb=True), image[..., :3])


def test_raw_pyramid_needs_explicit_single_plane_but_he_retains_native_level(tmp_path):
    path = tmp_path / "pyramid.tif"
    image = np.arange(480, dtype=np.uint16).reshape(20, 24)
    with tifffile.TiffWriter(path) as writer:
        writer.write(image, subifds=1)
        writer.write(image[::2, ::2], subfiletype=1)
    with pytest.raises(ValueError, match="pyramid levels"):
        verify(path)
    with tifffile.TiffFile(path) as tif:
        assert validate_tiff_plane(tif, label="H&E", allow_pyramid=True).shape == image.shape


def test_empty_blocked_core_has_controlled_verification_error():
    core = Core("core", "acq", "slide", "A1", "AF", "group", [], blocked_reasons=["No FOV set"])
    with pytest.raises(ValueError, match="no raw FOVs"):
        verify_core(core, None, PROFILE)


def test_header_and_decoded_shape_mismatch_is_rejected(tmp_path):
    path = tmp_path / "valid.tif"
    tifffile.imwrite(path, np.ones(PROFILE["shape"], np.uint16))
    with pytest.raises(ValueError, match="found.*uint16"):
        verify(path, reader=lambda _: np.ones((8, 8), np.uint16))


def ome_description(filename, *, root_uuid=True):
    own = ' UUID="urn:uuid:00000000-0000-0000-0000-000000000001"' if root_uuid else ""
    reference_uuid = (
        "00000000-0000-0000-0000-000000000002"
        if filename == "external.tif"
        else "00000000-0000-0000-0000-000000000001"
    )
    return (
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06"' + own + ">"
        '<Image ID="Image:0"><Pixels ID="Pixels:0" DimensionOrder="XYZCT" Type="uint16" '
        'SizeX="24" SizeY="20" SizeZ="1" SizeC="1" SizeT="1">'
        '<Channel ID="Channel:0:0" SamplesPerPixel="1"/><TiffData IFD="0" PlaneCount="1">'
        '<UUID FileName="' + filename + '">urn:uuid:' + reference_uuid + "</UUID>"
        "</TiffData></Pixels></Image></OME>"
    )


@pytest.mark.parametrize("decode", [False, True])
def test_ome_cannot_decode_pixels_from_an_unverified_companion(tmp_path, monkeypatch, decode):
    path, external = tmp_path / "main.tif", tmp_path / "external.tif"
    tifffile.imwrite(
        path,
        np.full((20, 24), 3, np.uint16),
        metadata=None,
        description=ome_description(external.name),
    )
    tifffile.imwrite(external, np.full((20, 24), 999, np.uint16), metadata=None)
    original = tifffile.TiffFile

    def guarded(path, *args, **kwargs):
        if str(path) == str(external):
            pytest.fail("Plane inspection opened an unverified OME companion")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(tifffile, "TiffFile", guarded)
    with pytest.raises(ValueError, match="external OME-TIFF pixel"):
        verify(path, decode=decode)


@pytest.mark.parametrize("reader", ["read_image", "mapped_image"])
@pytest.mark.parametrize("encoding", ["uint16", "float32"])
def test_every_native_reader_rejects_external_pixel_references(tmp_path, reader, encoding):
    path = tmp_path / "main.tif"
    description = ome_description("external.tif")
    if encoding == "float32":
        description = description.replace('Type="uint16"', 'Type="float"')
    tifffile.imwrite(path, np.full((20, 24), 3, encoding), metadata=None, description=description)
    tifffile.imwrite(tmp_path / "external.tif", np.full((20, 24), 999, encoding), metadata=None)
    with pytest.raises(ValueError, match="external OME-TIFF pixel"):
        if reader == "read_image":
            read_image(path)
        else:
            with mapped_image(path, tmp_path / "scratch"):
                pytest.fail("An unverified image reached the caller")


def test_generic_native_readers_preserve_valid_pyramid_level_zero(tmp_path):
    path = tmp_path / "pyramid.tif"
    image = np.arange(480, dtype=np.float32).reshape(20, 24)
    with tifffile.TiffWriter(path) as writer:
        writer.write(image, subifds=1)
        writer.write(image[::2, ::2], subfiletype=1)
    np.testing.assert_array_equal(read_image(path), image)
    with mapped_image(path, tmp_path / "scratch") as mapped:
        np.testing.assert_array_equal(mapped, image)


@pytest.mark.parametrize("kind", ["implicit", "self-uuid", "self-filename"])
def test_self_contained_single_plane_ome_retains_native_counts(tmp_path, kind):
    path = tmp_path / "main.ome.tif"
    image = np.arange(480, dtype=np.uint16).reshape(20, 24)
    if kind == "implicit":
        tifffile.imwrite(path, image, ome=True, metadata={"axes": "YX"})
    else:
        tifffile.imwrite(
            path,
            image,
            metadata=None,
            description=ome_description(path.name, root_uuid=kind == "self-uuid"),
        )
    metrics, _ = verify(path)
    assert metrics["decoded_files"] == 1
    np.testing.assert_array_equal(read_image(path), image)
