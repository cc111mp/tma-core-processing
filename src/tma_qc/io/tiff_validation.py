"""Self-contained native TIFF planes and stored-pixel segment validation."""

import xml.etree.ElementTree as ET

import tifffile


def validate_tiff_plane(tif, *, label, allow_pyramid=False):
    """Reject implicit plane selection and absent/truncated stored segments.

    This inspects the container, not compressed pixel contents. Sparse TIFF
    segments are unsupported: a decoder's replacement zeros are not acquired
    camera counts. H&E may retain a pyramid beneath its single native plane.
    """
    # OME can redirect a nominally single-plane series into another file. Do
    # this before accessing lazy series metadata, which otherwise opens that
    # unlisted file. Matching tifffile's UUID rules permits self-contained OME.
    if tif.is_ome:
        try:
            metadata = ET.fromstring(tif.ome_metadata)
        except (ET.ParseError, TypeError) as exc:
            raise ValueError(f"{label}: invalid OME-TIFF plane metadata") from exc
        own_uuid = metadata.attrib.get("UUID")
        for item in metadata.iter():
            if item.tag.rsplit("}", 1)[-1] != "TiffData":
                continue
            for reference in item:
                if reference.tag.rsplit("}", 1)[-1] != "UUID":
                    continue
                if own_uuid is not None and reference.text == own_uuid:
                    continue
                if (
                    own_uuid is None
                    and reference.text is not None
                    and reference.attrib.get("FileName", "").lower() == tif.filename.lower()
                ):
                    own_uuid = reference.text
                    continue
                raise ValueError(f"{label}: external OME-TIFF pixel references are unsupported")
    if not tif.pages:
        raise ValueError(f"{label}: TIFF contains no image plane")
    # Reject stacks before constructing all their series metadata.
    if len(tif.pages) != 1:
        raise ValueError(
            f"{label}: explicit single-plane TIFF required; multiple pages/series found"
        )
    if not tif.series:
        raise ValueError(f"{label}: TIFF contains no image plane")
    if len(tif.series) != 1 or len(tif.series[0].pages) != 1:
        raise ValueError(
            f"{label}: explicit single-plane TIFF required; multiple pages/series found"
        )
    series = tif.series[0]
    page = series.pages[0]
    if not allow_pyramid and (page.subifds or len(series.levels) != 1):
        raise ValueError(f"{label}: explicit single-plane TIFF required; pyramid levels found")
    if page.imagedepth != 1:
        raise ValueError(f"{label}: explicit two-dimensional image plane required")
    height, width = page.imagelength, page.imagewidth
    if height <= 0 or width <= 0:
        raise ValueError(f"{label}: TIFF image dimensions must be positive")
    separate = page.planarconfig == tifffile.PLANARCONFIG.SEPARATE
    planes = page.samplesperpixel if separate else 1
    if page.is_tiled:
        rows, columns = page.tilelength, page.tilewidth
        if rows <= 0 or columns <= 0:
            raise ValueError(f"{label}: invalid TIFF tile dimensions")
        per_plane = ((height + rows - 1) // rows) * ((width + columns - 1) // columns)
    else:
        rows, columns = page.rowsperstrip, width
        if rows <= 0:
            raise ValueError(f"{label}: invalid TIFF strip dimensions")
        per_plane = (height + rows - 1) // rows
    offsets, counts = page.dataoffsets, page.databytecounts
    expected = per_plane * planes
    if len(offsets) != expected or len(counts) != expected:
        raise ValueError(f"{label}: incomplete TIFF segment table; expected {expected} segments")
    size = tif.filehandle.size
    bits = page.bitspersample
    for index, (offset, count) in enumerate(zip(offsets, counts)):
        if offset <= 0 or count <= 0:
            raise ValueError(
                f"{label}: missing TIFF pixel segment {index}; sparse data unsupported"
            )
        if offset >= size or count > size - offset:
            raise ValueError(
                f"{label}: TIFF pixel segment {index} exceeds file size (truncated data)"
            )
        if page.compression == tifffile.COMPRESSION.NONE:
            segment_rows = rows if page.is_tiled else min(rows, height - index % per_plane * rows)
            if isinstance(bits, tuple):
                row_bits = columns * (bits[index // per_plane] if separate else sum(bits))
            else:
                row_bits = columns * bits * (1 if separate else page.samplesperpixel)
            required = ((row_bits + 7) // 8) * segment_rows
            if count < required:
                raise ValueError(
                    f"{label}: TIFF pixel segment {index} is shorter than its image rows"
                )
    return series
