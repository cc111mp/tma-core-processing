import cv2
import numpy as np
import pytest
import tifffile

from tma_qc.config import load_config
from tma_qc.io.images import read_image
from tma_qc.io.tile_cache import TileCache
from tma_qc.qc.illumination import evaluate_correction
from tma_qc.qc.stitching import OverlapMeasurements, evaluate_overlaps


def tile(path, value):
    image = np.full((64, 64), value, np.uint16)
    tifffile.imwrite(path, image, compression="deflate")
    return image


def test_cache_preserves_pixels_and_enforces_its_byte_budget(tmp_path):
    paths = [tmp_path / f"{n}.tif" for n in range(3)]
    originals = [tile(path, n + 100) for n, path in enumerate(paths)]
    size = originals[0].nbytes
    cache = TileCache(paths, 2 * size)
    first = cache.read(paths[0])
    assert not first.flags.writeable
    with pytest.raises(ValueError):
        first[0, 0] = 0
    cache.read(paths[1])
    assert cache.read(paths[0]) is first
    cache.read(paths[2])  # Least recently used tile is evicted.
    assert paths[1] not in cache.entries
    for path, original in zip(paths, originals):
        np.testing.assert_array_equal(cache.read(path), original)
        assert cache.bytes <= 2 * size
    assert cache.peak_bytes == 2 * size
    assert cache.evictions > 0
    cache.clear()
    assert cache.bytes == 0 and not cache.entries


def test_disabled_oversized_and_unlisted_tiles_are_not_retained(tmp_path):
    source, outside = tmp_path / "source.tif", tmp_path / "outside.tif"
    original = tile(source, 500)
    tile(outside, 600)
    for budget in (0, original.nbytes - 1):
        cache = TileCache([source], budget)
        np.testing.assert_array_equal(cache.read(source), read_image(source))
        cache.read(outside)
        assert not cache.entries and cache.bytes == 0


def test_replaced_or_changed_raw_file_invalidates_decoded_pixels(tmp_path):
    path = tmp_path / "raw.tif"
    tile(path, 400)
    cache = TileCache([path], 16384)
    cache.read(path)
    replacement = tmp_path / "replacement.tif"
    expected = tile(replacement, 800)
    replacement.replace(path)
    np.testing.assert_array_equal(cache.read(path), expected)
    expected = tile(path, 1200)
    np.testing.assert_array_equal(cache.read(path), expected)
    assert cache.misses == 3 and cache.bytes == expected.nbytes


@pytest.mark.parametrize("budget", [-1, True, 1.5])
def test_invalid_cache_budget_rejected(budget):
    with pytest.raises(ValueError, match="decoded_cache_bytes"):
        load_config(base={"runtime": {"decoded_cache_bytes": budget}})


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_invalid_tile_worker_count_rejected(workers):
    with pytest.raises(ValueError, match="tile_workers"):
        load_config(base={"runtime": {"tile_workers": workers}})


def test_parallel_tile_reads_are_bounded_and_results_ordered():
    from types import SimpleNamespace

    from tma_qc.stages.tile_qc import _measure_tiles

    fovs = [SimpleNamespace(id=str(n)) for n in range(9)]
    loads, consumed = [], []

    def read(path):
        loads.append(path)
        assert len(loads) - len(consumed) <= 2
        return np.full((32, 32), int(path) + 100, np.uint16)

    ctx = SimpleNamespace(
        config=load_config(
            base={"runtime": {"tile_workers": 2}, "tile_qc": {"window": 16, "stride": 16}}
        ),
        core=SimpleNamespace(fovs=fovs),
        manifest=SimpleNamespace(raw_path=lambda f: f.id),
        tiles=SimpleNamespace(read=read),
    )
    for rows, summary, sample in _measure_tiles(ctx):
        consumed.append(summary["fov"])
        assert all(row["fov"] == summary["fov"] for row in rows)
    assert consumed == [f.id for f in fovs]


def test_overlap_reuse_is_identical_and_invalidates_on_inputs(tmp_path, monkeypatch):
    scene = np.random.default_rng(7).integers(500, 10000, (96, 96), dtype=np.uint16)
    positions = [
        {"id": "a", "x": 0, "y": 0},
        {"id": "b", "x": 32, "y": 0},
        {"id": "c", "x": 0, "y": 32},
    ]
    raw, corrected = {}, {}
    for p in positions:
        a = scene[p["y"] : p["y"] + 64, p["x"] : p["x"] + 64]
        raw[p["id"]] = tmp_path / (p["id"] + ".tif")
        corrected[p["id"]] = tmp_path / (p["id"] + "-corrected.tif")
        tifffile.imwrite(raw[p["id"]], a, compression="deflate")
        tifffile.imwrite(corrected[p["id"]], a.astype(np.float32) * 0.8 - 200)
    expected = evaluate_correction(raw, corrected, positions)
    measurements = OverlapMeasurements()
    geometry = evaluate_overlaps(raw, positions, measurements=measurements)
    # Returned reports must not be able to mutate the retained measurements.
    geometry["edges"][0]["phase_residual_native_pixels"][0] += 100
    calls = []
    phase = cv2.phaseCorrelate

    def counted(*args):
        calls.append(1)
        return phase(*args)

    monkeypatch.setattr(cv2, "phaseCorrelate", counted)
    actual = evaluate_correction(raw, corrected, positions, raw_measurements=measurements)
    assert actual == expected
    assert len(calls) == actual["corrected"]["overlapping_pairs"]
    assert measurements.hits == actual["raw"]["overlapping_pairs"]
    # Geometry, sampling, and source changes must each invalidate reuse.
    changed = [dict(p) for p in positions]
    changed[1]["x"] += 1
    for current, sample in ((changed, 512), (changed, 32)):
        assert evaluate_overlaps(
            raw, current, max_sample=sample, measurements=measurements
        ) == evaluate_overlaps(raw, current, max_sample=sample)
    tile(raw["a"], 1500)
    assert evaluate_overlaps(
        raw, changed, max_sample=32, measurements=measurements
    ) == evaluate_overlaps(raw, changed, max_sample=32)
