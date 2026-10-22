"""Inspectable method choices and typed overrides, without image-library imports."""

from __future__ import annotations

import math
import re
import tomllib
from copy import deepcopy

from .schema import CORE_STAGES, PAIR_STAGES

METHODS = {
    "geometry_method": {
        "setting": "geometry.backend",
        "default": "provided",
        "choices": {
            "provided": {
                "description": "Use reviewed native tile positions",
                "requirements": ["Complete native position file for each core"],
            },
            "mist": {
                "description": "Estimate positions with the external MIST backend",
                "requirements": [
                    "Reviewed grid and acquisition sequence",
                    "Configured Java/classpath",
                ],
            },
        },
    },
    "illumination_method": {
        "setting": "illumination.mode",
        "default": "cidre",
        "choices": {
            "cidre": {
                "description": "Apply frozen flatfield and darkfield correction",
                "requirements": ["Compatible frozen model fields and receipt"],
            },
            "none": {
                "description": "Retain original counts without illumination correction",
                "requirements": [],
            },
        },
    },
    "registration_method": {
        "setting": "registration.backend / registration.core_method",
        "default": "provided",
        "choices": {
            "provided": {
                "description": "Use a reviewed native H&E-to-AF transform",
                "requirements": ["Explicit section pair and saved transform"],
            },
            "sift_affine": {
                "description": "Retained structural SIFT/affine baseline",
                "requirements": ["Explicit section pair", "Enough shared structural features"],
            },
            "sift": {
                "description": "Core fitter with SIFT initialization",
                "requirements": ["Explicit section pair", "Enough shared structural consensus"],
            },
            "orb": {
                "description": "Core fitter with ORB initialization",
                "requirements": ["Explicit section pair", "Enough shared structural consensus"],
            },
            "geometry_sift": {
                "description": "Core fitter with geometry and SIFT initialization",
                "requirements": ["Explicit section pair", "Enough shared structural consensus"],
            },
            "geometry_roma": {
                "description": "Core fitter with geometry and optional RoMa matching",
                "requirements": [
                    "Explicit section pair",
                    "registration-roma extra and prepared cached weights; no implicit downloads",
                ],
            },
        },
    },
    "registration_transform": {
        "setting": "registration.matching.transform",
        "default": "similarity",
        "choices": {
            "similarity": {
                "description": "Core global similarity transform",
                "requirements": ["A core registration method"],
            },
            "affine": {
                "description": "Core global affine transform",
                "requirements": ["A core registration method"],
            },
        },
    },
    "registration_refinement": {
        "setting": "registration.refinement.backend",
        "default": "none",
        "choices": {
            "none": {
                "description": "Keep the core global fit without local refinement",
                "requirements": ["A core registration method"],
            },
            "bspline_mi": {
                "description": "Optional bounded B-spline/MI refinement after a core fit",
                "requirements": ["A core registration method", "registration-refine extra"],
            },
        },
    },
}

FIXED_ALGORITHMS = {
    "index": {"description": "Source identity, SHA256 and image encoding verification"},
    "tile_qc": {"description": "Local native-window detail, saturation and zero measurements"},
    "geometry_qc": {"description": "Overlap correlation and native phase residuals"},
    "illumination_qc": {"description": "Raw-selected overlap comparisons and signal diagnostics"},
    "mosaic": {"description": "Native weighted linear blending with configurable alpha"},
    "mosaic_qc": {"description": "Native boundary steps and global/local tile-pitch projections"},
    "tissue": {"description": "Provisional raw-intensity support with seeded propagation"},
    "he_qc": {"description": "Original H&E image validity and visual-review evidence"},
    "registration_qc": {"description": "Independent held-out landmark evaluation"},
}
CORE_METHODS = frozenset({"sift", "orb", "geometry_sift", "geometry_roma"})


def method_choices(selector):
    """Return stable CLI choices for one named selector."""
    return tuple(METHODS[selector]["choices"])


def method_registry():
    """Return JSON-safe method metadata; this does not inspect data or runtimes."""
    return {
        "schema_version": 1,
        "methods": deepcopy(METHODS),
        "fixed_af_stages": list(CORE_STAGES),
        "fixed_pair_stages": list(PAIR_STAGES),
        "fixed_algorithms": deepcopy(FIXED_ALGORITHMS),
        "core_default_method": "geometry_sift",
        "precedence": ["defaults", "saved base", "TOML", "parameters / --set", "method selectors"],
        "notes": [
            "Registration runs only for explicitly assigned section pairs.",
            "A core's supplied native positions take precedence over geometry estimation.",
            "The default registration route is provided; the default core initializer is geometry_sift.",
            "Core transform/refinement defaults apply when a core method is selected.",
            "A pair's supplied transform takes precedence over registration fitting.",
            "Generic parameter dictionaries and TOML can retain presets; only active backend settings apply.",
            "QC remains advisory; method selection does not certify scientific quality.",
        ],
    }


def _supported_literal(value):
    if isinstance(value, dict):
        return all(isinstance(k, str) and _supported_literal(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_supported_literal(v) for v in value)
    if type(value) is float:
        return math.isfinite(value)
    return type(value) in {str, int, bool}


def parse_parameters(assignments):
    """Parse repeated SECTION.KEY=TOML_LITERAL arguments into strict nested overrides.

    Dotted paths must be unique and cannot overlap a parent/child assignment.
    String values require TOML quotes; arrays, booleans and numbers keep types.
    Unknown configuration keys are rejected by resolve_settings/load_config.
    """
    result, paths = {}, []
    for assignment in assignments or ():
        if not isinstance(assignment, str) or "=" not in assignment:
            raise ValueError("--set requires SECTION.KEY=VALUE using a TOML literal")
        path, value = assignment.split("=", 1)
        path = path.strip()
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", path, flags=re.ASCII):
            raise ValueError(f"Invalid --set path: {path!r}; use SECTION.KEY")
        parts = tuple(path.split("."))
        if any(parts[: len(p)] == p or p[: len(parts)] == parts for p in paths):
            raise ValueError(f"Duplicate or overlapping --set path: {path}")
        try:
            parsed = tomllib.loads("value = " + value)
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"Invalid TOML literal for --set {path}: {error}") from error
        if set(parsed) != {"value"} or not _supported_literal(parsed["value"]):
            raise ValueError(f"Unsupported --set literal for {path}; use finite TOML values")
        paths.append(parts)
        current = result
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = parsed["value"]
    return result


def _choose(name, value):
    if value is not None and (not isinstance(value, str) or value not in METHODS[name]["choices"]):
        choices = ", ".join(method_choices(name))
        raise ValueError(f"{name} must be one of: {choices}")


def _set(overrides, section, key, value):
    table = overrides.setdefault(section, {})
    if not isinstance(table, dict):
        raise ValueError(f"{section} settings must be a table")
    table[key] = value


def resolve_settings(
    config=None,
    *,
    parameters=None,
    geometry_method=None,
    illumination_method=None,
    registration_method=None,
    registration_transform=None,
    registration_refinement=None,
    base=None,
):
    """Resolve defaults → base → TOML → typed parameters → explicit method selectors.

    Return a validated configuration without opening images or creating outputs.
    Core-file and optional-runtime prerequisites are checked by plan/run.
    """
    from .config import load_config

    if parameters is not None and not isinstance(parameters, dict):
        raise ValueError("parameters must be a nested settings dictionary")
    overrides = deepcopy(parameters) if parameters is not None else {}
    selections = {
        "geometry_method": geometry_method,
        "illumination_method": illumination_method,
        "registration_method": registration_method,
        "registration_transform": registration_transform,
        "registration_refinement": registration_refinement,
    }
    for name, value in selections.items():
        _choose(name, value)
    if geometry_method is not None:
        _set(overrides, "geometry", "backend", geometry_method)
    if illumination_method is not None:
        _set(overrides, "illumination", "mode", illumination_method)
    if registration_method is not None:
        _set(
            overrides,
            "registration",
            "backend",
            "core" if registration_method in CORE_METHODS else registration_method,
        )
        if registration_method in CORE_METHODS:
            _set(overrides, "registration", "core_method", registration_method)
    for value, section, key in (
        (registration_transform, "matching", "transform"),
        (registration_refinement, "refinement", "backend"),
    ):
        if value is not None:
            table = overrides.setdefault("registration", {})
            if not isinstance(table, dict):
                raise ValueError("registration settings must be a table")
            nested = table.setdefault(section, {})
            if not isinstance(nested, dict):
                raise ValueError(f"registration.{section} settings must be a table")
            nested[key] = value
    effective = load_config(config, base=base, overrides=overrides)
    if effective["registration"]["backend"] != "core" and (
        registration_transform is not None or registration_refinement is not None
    ):
        raise ValueError("Registration transform/refinement selectors require a core method")
    return effective
