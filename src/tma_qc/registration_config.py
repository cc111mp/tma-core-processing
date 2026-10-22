# Adapted from the local tma-core-registration research workflow (tmareg 0.1.0).
"""A small, strict experiment configuration; all distances name their coordinate frame."""

from __future__ import annotations

import math
import tomllib
from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path


def is_finite_number(value):
    """TOML-compatible real values, including a controlled error for huge integers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class Matching:
    max_dimension: int = 1024
    rotations: tuple[float, ...] = (0, 90, 180, 270)
    feature_view: str = "gradient"
    transform: str = "similarity"
    max_matches: int = 3000
    ratio: float = 0.75
    ransac_view_pixels: float = 4.0
    min_inliers: int = 12
    min_inlier_fraction: float = 0.2
    min_hull_coverage: float = 0.08
    max_anisotropy: float = 1.4
    min_relative_scale: float = 0.4
    max_relative_scale: float = 2.5
    ambiguity_score_fraction: float = 0.1
    ambiguity_view_pixels: float = 8.0


@dataclass(frozen=True)
class Roma:
    device: str = "auto"
    coarse_resolution: int = 560
    upsample_resolution: int = 864


@dataclass(frozen=True)
class Refinement:
    backend: str = "none"
    mesh_size: int = 6
    iterations: int = 50
    max_displacement_view_pixels: float = 12.0
    min_jacobian: float = 0.25
    support_taper_view_pixels: float = 16.0


@dataclass(frozen=True)
class Experiment:
    methods: tuple[str, ...] = ("geometry_sift",)
    seed: int = 20261019
    threads: int = 2
    export_native: bool = False
    export_chunk: int = 512
    matching: Matching = Matching()
    roma: Roma = Roma()
    refinement: Refinement = Refinement()

    def to_dict(self):
        return asdict(self)


def _section(cls, values):
    if not isinstance(values, dict):
        raise ValueError(f"{cls.__name__} settings must be a TOML table")
    unknown = set(values) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} settings: {sorted(map(str, unknown))}")
    return cls(**deepcopy(values))


def _read_config(path: Path | None = None) -> Experiment:
    values = tomllib.loads(Path(path).read_text()) if path else {}
    for key, cls in (("matching", Matching), ("roma", Roma), ("refinement", Refinement)):
        values[key] = _section(cls, values.get(key, {}))
    return _section(Experiment, values)


def load_config(path: Path | None = None) -> Experiment:
    """Load a complete standalone research configuration."""
    config = _read_config(path)
    validate(config)
    return config


def inline_schema():
    """Known sparse inline fields, without importing any image-processing code."""
    return {
        "matching": asdict(Matching()),
        "roma": asdict(Roma()),
        "refinement": asdict(Refinement()),
    }


def validate_inline_settings(settings):
    """Validate sparse inline fields without guessing external-file cross-field values."""
    sections = {
        name: _section(cls, settings.get(name, {}))
        for name, cls in (("matching", Matching), ("roma", Roma), ("refinement", Refinement))
    }
    validate(Experiment(**sections), check_cross_fields=False)


def resolve_core_config(settings) -> Experiment:
    """Resolve defaults → optional core TOML → sparse inline registration tables.

    The main registration.core_method chooses one initializer. No image libraries,
    model runtimes, model weights or evaluation landmarks are loaded here.
    """
    if not isinstance(settings, dict):
        raise ValueError("Registration settings must be a dictionary")
    method = settings.get("core_method", "geometry_sift")
    if not isinstance(method, str) or method not in {
        "sift",
        "orb",
        "geometry_sift",
        "geometry_roma",
    }:
        raise ValueError("Unknown registration.core_method")
    path = settings.get("core_config", "")
    if not isinstance(path, (str, Path)):
        raise ValueError("registration.core_config must be a TOML path")
    config = _read_config(Path(path) if path else None)
    sections = {}
    for name, cls in (("matching", Matching), ("roma", Roma), ("refinement", Refinement)):
        values = settings.get(name, {})
        if not isinstance(values, dict):
            raise ValueError(f"registration.{name} must be a table")
        unknown = set(values) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown registration.{name} settings: {sorted(map(str, unknown))}")
        sections[name] = replace(getattr(config, name), **deepcopy(values))
    config = replace(config, methods=(method,), **sections)
    validate(config)
    return config


def validate(config: Experiment, *, check_cross_fields=True):
    integer_settings = [
        config.seed,
        config.threads,
        config.export_chunk,
        config.matching.max_dimension,
        config.matching.max_matches,
        config.matching.min_inliers,
        config.roma.coarse_resolution,
        config.roma.upsample_resolution,
        config.refinement.mesh_size,
        config.refinement.iterations,
    ]
    if any(type(value) is not int for value in integer_settings):
        raise ValueError("Integer settings require integers, not floats or Boolean values")
    if not isinstance(config.methods, (list, tuple)) or not all(
        isinstance(method, str) for method in config.methods
    ):
        raise ValueError("methods must be an array of method names")
    if not isinstance(config.matching.rotations, (list, tuple)):
        raise ValueError("rotations must be an array of degree values")
    allowed = {"sift", "orb", "geometry_sift", "geometry_roma"}
    if not config.methods or set(config.methods) - allowed:
        raise ValueError(f"Choose methods from {sorted(allowed)}")
    if len(set(config.methods)) != len(config.methods):
        raise ValueError("Methods must be unique")
    m, r, d = config.matching, config.roma, config.refinement
    real_settings = {
        "matching.ratio": m.ratio,
        "matching.ransac_view_pixels": m.ransac_view_pixels,
        "matching.min_inlier_fraction": m.min_inlier_fraction,
        "matching.min_hull_coverage": m.min_hull_coverage,
        "matching.max_anisotropy": m.max_anisotropy,
        "matching.min_relative_scale": m.min_relative_scale,
        "matching.max_relative_scale": m.max_relative_scale,
        "matching.ambiguity_score_fraction": m.ambiguity_score_fraction,
        "matching.ambiguity_view_pixels": m.ambiguity_view_pixels,
        "refinement.max_displacement_view_pixels": d.max_displacement_view_pixels,
        "refinement.min_jacobian": d.min_jacobian,
        "refinement.support_taper_view_pixels": d.support_taper_view_pixels,
    }
    for name, value in real_settings.items():
        if not is_finite_number(value):
            raise ValueError(f"{name} must be a finite number, not a Boolean or string")
    for name, value in (
        ("feature_view", m.feature_view),
        ("transform", m.transform),
        ("device", r.device),
        ("refinement.backend", d.backend),
    ):
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
    if m.feature_view not in {"gradient", "intensity"}:
        raise ValueError("feature_view must be gradient or intensity")
    if m.transform not in {"similarity", "affine"}:
        raise ValueError("transform must be similarity or affine")
    if not 64 <= m.max_dimension <= 4096 or not 12 <= m.max_matches <= 20000:
        raise ValueError("max_dimension must be 64..4096 and max_matches 12..20000")
    if (
        not m.rotations
        or len(m.rotations) > 24
        or any(not is_finite_number(value) for value in m.rotations)
    ):
        raise ValueError("Provide 1..24 finite rotation hypotheses in degrees")
    if not 0 < m.ratio < 1 or not 0 < m.min_inlier_fraction <= 1:
        raise ValueError("Matching ratio and inlier fraction must be in (0, 1]")
    if not 0 < m.min_hull_coverage <= 1 or m.min_inliers < 3:
        raise ValueError("Invalid inlier count or spatial coverage threshold")
    if not 0 < m.ransac_view_pixels < 4096 or (
        check_cross_fields and m.ransac_view_pixels >= m.max_dimension
    ):
        raise ValueError("RANSAC distance must be positive working-view pixels")
    if not 0 < m.min_relative_scale <= 1 <= m.max_relative_scale or m.max_anisotropy < 1:
        raise ValueError("Invalid scale or anisotropy limits")
    if not 0 <= m.ambiguity_score_fraction < 1 or m.ambiguity_view_pixels <= 0:
        raise ValueError("Invalid ambiguity settings")
    if r.device not in {"auto", "cpu", "cuda", "cuda:0"}:
        raise ValueError("RoMa device must be auto, cpu, cuda or cuda:0")
    if r.coarse_resolution < 112 or r.coarse_resolution % 14 or r.upsample_resolution < 112:
        raise ValueError("RoMa coarse resolution must be a multiple of 14 and >=112")
    if d.backend not in {"none", "bspline_mi"}:
        raise ValueError("refinement.backend must be none or bspline_mi")
    if not 2 <= d.mesh_size <= 16 or not 1 <= d.iterations <= 500:
        raise ValueError("Invalid B-spline mesh size or iteration count")
    if d.max_displacement_view_pixels <= 0 or not 0 < d.min_jacobian < 1:
        raise ValueError("Invalid deformation displacement or Jacobian limits")
    if d.support_taper_view_pixels <= 0:
        raise ValueError("Deformation support taper must be positive")
    if not 1 <= config.threads <= 32 or not 64 <= config.export_chunk <= 2048:
        raise ValueError("Invalid thread count or export chunk size")
    if not isinstance(config.export_native, bool) or not 0 <= config.seed < 2**31:
        raise ValueError("export_native must be Boolean; seed must fit a signed 32-bit integer")
    # NaNs must not bypass numeric safety checks.
    for section in (m, r, d):
        for value in asdict(section).values():
            if isinstance(value, (int, float)) and not is_finite_number(value):
                raise ValueError("Configuration numbers must be finite")
