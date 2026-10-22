from __future__ import annotations

import tempfile
import tomllib
from copy import deepcopy
from pathlib import Path

from .registration_config import inline_schema, is_finite_number, validate_inline_settings
from .storage import DEFAULT_STORAGE

DEFAULTS = {
    "profile": {"shape": [2048, 2048], "dtype": "uint16"},
    "runtime": {
        "cache_root": str(Path(tempfile.gettempdir()) / "tma-qc"),
        "threads": 2,
        "decoded_cache_bytes": 512 * 1024 * 1024,
        "tile_workers": 4,
    },
    "storage": DEFAULT_STORAGE,
    "tile_qc": {"window": 256, "stride": 256, "prefilter_sigma": 1.0},
    "geometry": {
        "backend": "provided",
        "downsample": 4,
        "mist_classpath": "",
        "timeout_seconds": 1800,
    },
    "illumination": {"mode": "cidre"},
    "mosaic": {"alpha": 1.5, "chunk_rows": 256, "max_pixels": 500000000},
    "tissue": {"quantile": 0.2, "mad_multiplier": 6.0},
    "registration": {
        "backend": "provided",
        "core_method": "geometry_sift",
        "core_config": "",
        "matching": {},
        "refinement": {},
        "roma": {},
        "max_dimension": 1536,
        "min_matches": 12,
        "ransac_pixels": 3.0,
        "export_aligned_he": True,
    },
    "qc": {"landmark_tolerance_pixels": 20.0, "minimum_landmarks": 5},
    "artifact_qc": {"evidence_pairs": 4, "crop_size": 256, "boundary_band_pixels": 8},
    "report": {"preview_size": 1024, "slide_tile": 160},
}


def _merge(config, supplied, schema, *, location="configuration"):
    """Strict recursive table merge; every assigned caller value is copied."""
    if not isinstance(supplied, dict):
        raise ValueError(f"{location} must be a dictionary/table")
    unknown = set(supplied) - set(schema)
    if unknown:
        raise ValueError(f"Unknown {location} settings: {sorted(map(str, unknown))}")
    for key, value in supplied.items():
        expected = schema[key]
        name = f"{location}.{key}"
        if isinstance(expected, dict):
            _merge(config.setdefault(key, {}), value, expected, location=name)
            continue
        if key == "timeout_seconds" and location.endswith(".geometry"):
            valid = not isinstance(value, bool) and isinstance(value, (int, float))
            kind = "a number"
        elif isinstance(expected, bool):
            valid = type(value) is bool
            kind = "a Boolean"
        elif isinstance(expected, int):
            valid = type(value) is int
            kind = "an integer"
        elif isinstance(expected, float):
            valid = not isinstance(value, bool) and isinstance(value, (int, float))
            kind = "a number"
        elif isinstance(expected, str):
            valid = isinstance(value, str)
            kind = "a string"
        elif isinstance(expected, (list, tuple)):
            valid = (
                isinstance(value, (list, tuple))
                if isinstance(expected, tuple)
                else isinstance(value, list)
            )
            kind = "an array"
        else:
            raise ValueError(f"Unsupported configuration schema at {name}")
        if not valid:
            raise ValueError(f"{name} must be {kind}")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and not is_finite_number(value)
        ):
            raise ValueError(f"{name} must be finite")
        config[key] = deepcopy(value)


def load_config(
    path: str | Path | None = None, *, base: dict | None = None, overrides: dict | None = None
) -> dict:
    """Resolve built-in defaults → saved/base values → TOML → dictionary overrides.

    Inline core tables remain sparse. The external core TOML is inspected later
    by preflight/pair stages, preserving AF failure isolation when it is missing.
    """
    config = deepcopy(DEFAULTS)
    if path is not None and not isinstance(path, (str, Path)):
        raise ValueError("Configuration path must be a string or Path")
    core_path_from_file = False
    schema = deepcopy(DEFAULTS)
    schema["registration"].update(inline_schema())
    if base is not None:
        _merge(config, base, schema, location="base configuration")
    if path:
        with Path(path).open("rb") as f:
            supplied = tomllib.load(f)
        _merge(config, supplied, schema)
        core_path_from_file = "core_config" in supplied.get("registration", {})
    if overrides is not None:
        _merge(config, overrides, schema, location="override configuration")
        if "core_config" in overrides.get("registration", {}):
            core_path_from_file = False
    validate_inline_settings(config["registration"])
    if not config["runtime"]["cache_root"].strip():
        raise ValueError("runtime.cache_root must be a nonempty string")
    timeout = config["geometry"]["timeout_seconds"]
    if not is_finite_number(timeout) or timeout <= 0:
        raise ValueError("geometry.timeout_seconds must be finite and positive")
    for section, key in [
        ("tile_qc", "window"),
        ("tile_qc", "stride"),
        ("runtime", "threads"),
        ("runtime", "tile_workers"),
        ("mosaic", "chunk_rows"),
        ("mosaic", "max_pixels"),
        ("report", "preview_size"),
        ("report", "slide_tile"),
        ("artifact_qc", "evidence_pairs"),
        ("artifact_qc", "crop_size"),
        ("artifact_qc", "boundary_band_pixels"),
        ("qc", "minimum_landmarks"),
    ]:
        value = config[section][key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{section}.{key} must be a positive integer")
    if config["runtime"]["threads"] > 2**31 - 1:
        raise ValueError("runtime.threads exceeds the signed 32-bit OpenCV limit")
    if config["illumination"]["mode"] not in {"cidre", "none"}:
        raise ValueError("illumination.mode must be cidre or none")
    if (
        type(config["runtime"]["decoded_cache_bytes"]) is not int
        or config["runtime"]["decoded_cache_bytes"] < 0
    ):
        raise ValueError(
            "runtime.decoded_cache_bytes must be a nonnegative integer (0 disables it)"
        )
    if config["geometry"]["backend"] not in {"provided", "mist"}:
        raise ValueError("geometry.backend must be provided or mist")
    if config["registration"]["backend"] not in {"provided", "sift_affine", "core"}:
        raise ValueError("registration.backend must be provided, sift_affine or core")
    if config["registration"]["core_method"] not in {
        "sift",
        "orb",
        "geometry_sift",
        "geometry_roma",
    }:
        raise ValueError("Unknown registration.core_method")
    if not isinstance(config["registration"]["core_config"], str):
        raise ValueError("registration.core_config must be a TOML path string")
    if config["registration"]["core_config"]:
        core_path = Path(config["registration"]["core_config"]).expanduser()
        if not core_path.is_absolute():
            core_path = (
                Path(path).resolve().parent if core_path_from_file else Path.cwd()
            ) / core_path
        config["registration"]["core_config"] = str(core_path.resolve())
    for key in ("max_dimension", "min_matches"):
        if type(config["registration"][key]) is not int or config["registration"][key] < 3:
            raise ValueError(f"registration.{key} must be an integer >= 3")
    if type(config["registration"]["export_aligned_he"]) is not bool:
        raise ValueError("registration.export_aligned_he must be Boolean")
    reserve = config["storage"]["reserve_bytes"]
    factor = config["storage"]["safety_factor"]
    if (
        type(reserve) is not int
        or reserve < 0
        or isinstance(factor, bool)
        or not isinstance(factor, (int, float))
        or not is_finite_number(factor)
        or factor < 1
    ):
        raise ValueError(
            "storage requires nonnegative integer reserve_bytes and finite safety_factor >= 1"
        )
    downsample = config["geometry"]["downsample"]
    if type(downsample) is not int or downsample < 1:
        raise ValueError("geometry.downsample must be a positive integer")
    shape = config["profile"]["shape"]
    if (
        not isinstance(shape, list)
        or len(shape) != 2
        or any(isinstance(v, bool) or not isinstance(v, int) or v < 8 for v in shape)
    ):
        raise ValueError("profile.shape must contain two integer dimensions >= 8")
    if config["profile"]["dtype"] != "uint16":
        raise ValueError("The current raw AF profile supports uint16 only")
    for section, key in [
        ("tile_qc", "prefilter_sigma"),
        ("mosaic", "alpha"),
        ("registration", "ransac_pixels"),
        ("qc", "landmark_tolerance_pixels"),
    ]:
        value = config[section][key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not is_finite_number(value)
            or value <= 0
        ):
            raise ValueError(f"{section}.{key} must be finite and positive")
    for key in ("quantile", "mad_multiplier"):
        value = config["tissue"][key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not is_finite_number(value)
            or value < 0
            or (key == "quantile" and value > 1)
        ):
            raise ValueError("Invalid tissue support parameters")
    if (
        min(
            config["tile_qc"]["window"],
            config["report"]["preview_size"],
            config["report"]["slide_tile"],
            config["artifact_qc"]["crop_size"],
        )
        < 8
    ):
        raise ValueError("Window and preview dimensions must be at least 8")
    return config
