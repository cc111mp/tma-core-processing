import pytest

from tma_qc.demo import create_demo
from tma_qc.pipeline import run_pipeline
from tma_qc.provenance import read_json


def keyed(summary):
    return {(r["unit"], r["stage"]): r for r in summary["stage_results"]}


def test_disabled_cache_has_identical_scientific_outputs(tmp_path):
    manifest, config = create_demo(tmp_path / "demo")
    cached = run_pipeline(manifest, tmp_path / "cached", config)
    config.write_text(config.read_text().replace("[runtime]", "[runtime]\ndecoded_cache_bytes = 0"))
    uncached = run_pipeline(manifest, tmp_path / "uncached", config)
    assert all(r["budget_bytes"] == 0 for r in uncached["decoded_tile_cache"])
    assert all(r["hits"] > 0 and r["retained_bytes"] == 0 for r in cached["decoded_tile_cache"])
    first, second = keyed(cached), keyed(uncached)
    assert cached["execution_errors"] == uncached["execution_errors"] == 0
    for key, row in first.items():
        other = second[key]
        assert (row["execution"], row["qc"], row["metrics"], row["findings"]) == (
            other["execution"],
            other["qc"],
            other["metrics"],
            other["findings"],
        )
        # Stage outputs contain identical image bytes; path-bearing JSON depends
        # on the output root and is compared through numerical measurements.
        for name, output in row["outputs"].items():
            if output["path"].endswith((".tif", ".png")):
                assert output["sha256"] == other["outputs"][name]["sha256"]


def test_interruption_clears_core_memory(tmp_path, monkeypatch):
    manifest, config = create_demo(tmp_path / "demo")
    contexts = []

    def interrupted(ctx, out):
        contexts.append(ctx)
        assert ctx.tiles.bytes > 0 and ctx.raw_overlaps.edges
        raise KeyboardInterrupt("interrupted after raw geometry measurements")

    monkeypatch.setattr("tma_qc.stages.illumination.run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_pipeline(manifest, tmp_path / "results", config)
    assert contexts and all(
        ctx.tiles.bytes == 0 and not ctx.tiles.entries and not ctx.raw_overlaps.edges
        for ctx in contexts
    )


def test_cached_geometry_needs_no_in_memory_measurements(tmp_path):
    manifest, config = create_demo(tmp_path / "demo")
    output = tmp_path / "results"
    first = run_pipeline(manifest, output, config)
    before = keyed(first)["demo-A1", "illumination_qc"]
    measurements = output / before["outputs"]["overlaps"]["path"]
    expected = read_json(measurements)
    measurements.unlink()
    second = keyed(run_pipeline(manifest, output, config, resume=True))
    assert second["demo-A1", "geometry_qc"]["cached"]
    assert not second["demo-A1", "illumination_qc"]["cached"]
    assert (
        read_json(output / second["demo-A1", "illumination_qc"]["outputs"]["overlaps"]["path"])
        == expected
    )


def test_serial_and_parallel_tile_qc_have_identical_ordered_outputs(tmp_path):
    manifest, config = create_demo(tmp_path / "demo")
    parallel = keyed(run_pipeline(manifest, tmp_path / "parallel", config))
    config.write_text(config.read_text().replace("[runtime]", "[runtime]\ntile_workers = 1"))
    serial = keyed(run_pipeline(manifest, tmp_path / "serial", config))
    for key, before in parallel.items():
        after = serial[key]
        assert (before["execution"], before["qc"], before["metrics"], before["findings"]) == (
            after["execution"],
            after["qc"],
            after["metrics"],
            after["findings"],
        )
        for name, output in before["outputs"].items():
            assert output["sha256"] == after["outputs"][name]["sha256"]


def test_parallel_worker_error_is_isolated_and_workers_finish(tmp_path, monkeypatch):
    import threading

    from tma_qc.stages import tile_qc

    manifest, config = create_demo(tmp_path / "demo")
    original = tile_qc._measure

    def broken(fov_id, image, settings):
        if image[0, 0] == sentinel:
            raise ValueError("controlled worker failure")
        return original(fov_id, image, settings)

    from tma_qc.io.images import read_image
    from tma_qc.io.manifest import Manifest

    m = Manifest(manifest)
    sentinel = read_image(m.raw_path(m.cores[0].fovs[0]))[0, 0]
    monkeypatch.setattr(tile_qc, "_measure", broken)
    result = keyed(run_pipeline(manifest, tmp_path / "results", config))
    assert result["demo-A1", "tile_qc"]["execution"] == "error"
    assert "controlled worker failure" in result["demo-A1", "tile_qc"]["findings"][0]
    assert result["demo-B3", "tile_qc"]["execution"] == "complete"
    assert not any(t.name.startswith("tile-qc") for t in threading.enumerate())
