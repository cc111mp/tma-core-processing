"""Native artifact integration for content missed by the old percentile grid."""

from types import SimpleNamespace

import numpy as np
from PIL import Image

from tma_qc.config import load_config
from tma_qc.io.images import write_tiff
from tma_qc.io.tile_cache import TileCache
from tma_qc.provenance import read_json, sha256
from tma_qc.stages.tile_qc import run


def test_tile_artifact_uses_native_percentiles_and_discloses_geometric_sampling(tmp_path):
    source = tmp_path / "source.tif"
    raw = np.zeros((512, 512), np.uint16)
    raw[:, 1::2] = 2000  # Both old percentile grids and the preview see only zero.
    write_tiff(source, raw)
    before = sha256(source)
    out = tmp_path / "stage"
    out.mkdir()
    fov = SimpleNamespace(id="1")
    ctx = SimpleNamespace(
        core=SimpleNamespace(fovs=[fov]),
        config=load_config(base={"tile_qc": {"window": 64, "stride": 96}}),
        manifest=SimpleNamespace(raw_path=lambda _: source),
        tiles=TileCache([source], raw.nbytes),
    )
    payload = run(ctx, out)
    artifact = read_json(payload.outputs["measurements"])
    assert artifact["display_window"] == [0, 2000]
    assert artifact["tiles"][0]["p99"] == 2000
    assert artifact["tiles"][0]["zero_fraction"] == 0.5
    assert artifact["tile_grid_sampling"] == "area_averaged_preview"
    assert artifact["detail_sampling"] == payload.metrics["detail_sampling"]
    assert 0 < artifact["detail_sampling"]["fraction"] < 1
    assert (
        artifact["detail_sampling"]["measurement_interior_fraction"]
        < (artifact["detail_sampling"]["fraction"])
    )
    assert not artifact["blur_segmentation_validated"]
    assert artifact["blurred_area_ratio"] is None
    assert payload.outputs["tile_grid"].is_file()
    with Image.open(payload.outputs["tile_grid"]) as grid:
        # The visible FOV interior represents the half-bright acquired pattern,
        # rather than the all-zero phase of the previous stride-only preview.
        assert np.asarray(grid.crop((8, 24, 120, 136))).mean() == 127
    assert sha256(source) == before
