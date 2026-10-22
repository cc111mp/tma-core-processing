"""Small clearly labelled synthetic fixture, not a biological performance demo."""

from pathlib import Path

import numpy as np
from scipy import ndimage

from .io.coordinates import position_document
from .io.images import write_tiff
from .provenance import sha256, write_json


def create_demo(directory):
    root = Path(directory).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Demo directory must be new or empty")
    root.mkdir(parents=True, exist_ok=True)
    raw = root / "inputs"
    raw.mkdir()
    assets = root / "assets"
    assets.mkdir()
    n = 160
    yy, xx = np.mgrid[:n, :n]
    flat = (0.65 + 0.35 * np.exp(-((xx - 80) ** 2 + (yy - 80) ** 2) / (2 * 65**2))).astype(
        np.float32
    )
    dark = np.full((n, n), 120.0, np.float32)
    write_tiff(assets / "flatfield.tif", flat)
    write_tiff(assets / "darkfield.tif", dark)
    write_json(
        assets / "model.json",
        {
            "quality_controls": "synthetic_truth_only",
            "flatfield_sha256": sha256(assets / "flatfield.tif"),
            "darkfield_sha256": sha256(assets / "darkfield.tif"),
        },
    )
    cores = []
    for k, core in enumerate(["A1", "B3"]):
        rng = np.random.default_rng(10 + k)
        base = (
            2000 + ndimage.gaussian_filter(rng.random((256, 256)).astype(np.float32), 0.7) * 10000
        )
        y, x = np.mgrid[:256, :256]
        base += 2500 * np.sin(x / 9) * np.cos(y / 17)
        frames = []
        positions = []
        for ident, (px, py) in enumerate([(0, 0), (96, 0), (0, 96), (96, 96)], 1):
            image = np.rint(base[py : py + n, px : px + n] * flat + dark).astype(np.uint16)
            path = raw / core / f"FOV_{ident}.tif"
            write_tiff(path, image)
            frames.append(
                {"id": str(ident), "path": path.relative_to(raw).as_posix(), "sha256": sha256(path)}
            )
            positions.append({"id": str(ident), "x": px, "y": py})
        write_json(assets / f"{core}-positions.json", position_document(positions))
        cores.append(
            {
                "uid": "demo-" + core,
                "acquisition": "SYNTHETIC-original",
                "slide": "SYNTHETIC",
                "core": core,
                "channel": "AF",
                "group": "synthetic-group",
                "fovs": frames,
                "positions": f"assets/{core}-positions.json",
                "geometry_reviewed": True,
                "metadata": {"synthetic": True, "coordinate_units": "pixels"},
            }
        )
        if k == 0:
            tissue = np.clip(base / 45, 0, 200)
            he = np.stack([255 - 0.7 * tissue, 255 - tissue, 255 - 0.5 * tissue], axis=2).astype(
                np.uint8
            )
            write_tiff(assets / "he.tif", he, photometric="rgb")
    write_json(
        assets / "transform.json",
        {
            "direction": "he_to_af",
            "coordinate_space": "native_pixels",
            "matrix": np.eye(3).tolist(),
        },
    )
    points = [[40, 40], [210, 40], [40, 210], [210, 210], [128, 128]]
    write_json(
        assets / "landmarks.json",
        {
            "role": "evaluation_only",
            "coordinate_space": "native_pixels",
            "points": [{"af": p, "he": p} for p in points],
        },
    )
    manifest = {
        "schema_version": 1,
        "dataset_id": "SYNTHETIC-software-demo",
        "raw_root": "inputs",
        "groups": [
            {
                "id": "synthetic-group",
                "flatfield": "assets/flatfield.tif",
                "darkfield": "assets/darkfield.tif",
                "model_metadata": "assets/model.json",
            }
        ],
        "cores": cores,
        "pairs": [
            {
                "uid": "demo-pair",
                "af_core": "demo-A1",
                "he_path": "assets/he.tif",
                "he_sha256": sha256(assets / "he.tif"),
                "relationship": "neighboring_sections",
                "transform": "assets/transform.json",
                "landmarks": "assets/landmarks.json",
                "metadata": {"synthetic": True},
            }
        ],
        "metadata": {
            "synthetic": True,
            "scope": "Software exercise only; not real adjacent-section validation",
        },
    }
    write_json(root / "manifest.json", manifest)
    (root / "demo.toml").write_text(
        f'[profile]\nshape = [160, 160]\ndtype = "uint16"\n\n[tile_qc]\nwindow = 80\nstride = 80\n\n[runtime]\ncache_root = "{(root / "cache").as_posix()}"\n',
        encoding="utf-8",
    )
    return root / "manifest.json", root / "demo.toml"
