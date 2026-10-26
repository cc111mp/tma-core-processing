"""Small TOML/YAML readers and a reproducible YAML writer without image imports.

YAML files intentionally use only JSON-like values. Aliases, anchors and explicit
tags are rejected so settings cannot acquire hidden sharing or special Python
types. File suffixes other than .yaml/.yml retain the historical TOML reader.
"""

from __future__ import annotations

import math
import tomllib
from pathlib import Path


def _yaml_module():
    try:
        import yaml
    except ImportError as error:
        raise ValueError("YAML configuration requires PyYAML>=6.0; install PyYAML") from error
    return yaml


def _json_values(value, *, location="configuration", active=None):
    """Validate values without coercing types or modifying the caller's data."""
    active = set() if active is None else active
    if type(value) in {dict, list}:
        identity = id(value)
        if identity in active:
            raise ValueError(f"{location} must not contain recursive values")
        active.add(identity)
        try:
            if type(value) is dict:
                for key, child in value.items():
                    if type(key) is not str:
                        raise ValueError(f"{location} keys must be strings")
                    _json_values(child, location=f"{location}.{key}", active=active)
            else:
                for index, child in enumerate(value):
                    _json_values(child, location=f"{location}[{index}]", active=active)
        finally:
            active.remove(identity)
        return
    if value is None or type(value) in {str, bool}:
        return
    if type(value) in {int, float}:
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"{location} numbers must be finite")
        return
    raise ValueError(f"{location} must contain only JSON-like values, got {type(value).__name__}")


def read_configuration(path: str | Path) -> dict:
    """Read .yaml/.yml as strict safe YAML; read every other suffix as TOML.

    Syntax/type errors become ValueError with the input path. Missing files and
    other filesystem errors are preserved for callers to handle independently.
    """
    if not isinstance(path, (str, Path)):
        raise ValueError("Configuration path must be a string or Path")
    path = Path(path)
    if path.suffix.lower() not in {".yaml", ".yml"}:
        try:
            with path.open("rb") as handle:
                return tomllib.load(handle)
        except (tomllib.TOMLDecodeError, UnicodeError) as error:
            raise ValueError(f"Invalid TOML configuration {path}: {error}") from error

    yaml = _yaml_module()

    class SettingsLoader(yaml.SafeLoader):
        pass

    def unique_string_mapping(loader, node):
        values = {}
        for key_node, value_node in node.value:
            if key_node.tag != "tag:yaml.org,2002:str":
                raise ValueError(
                    f"Mapping keys must be strings at line {key_node.start_mark.line + 1}"
                )
            key = loader.construct_object(key_node, deep=True)
            if key in values:
                raise ValueError(f"Duplicate key {key!r} at line {key_node.start_mark.line + 1}")
            values[key] = loader.construct_object(value_node, deep=True)
        return values

    SettingsLoader.add_constructor("tag:yaml.org,2002:map", unique_string_mapping)
    try:
        contents = path.read_text(encoding="utf-8")
        for token in yaml.scan(contents):
            if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)):
                raise ValueError("YAML anchors and aliases are not supported")
            if isinstance(token, yaml.tokens.TagToken):
                raise ValueError("Explicit YAML tags are not supported")
        values = yaml.load(contents, Loader=SettingsLoader)
        if type(values) is not dict:
            raise ValueError("Configuration must be a mapping/object")
        _json_values(values)
        return values
    except (yaml.YAMLError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError(f"Invalid YAML configuration {path}: {error}") from error


def render_yaml_settings(config: dict) -> str:
    """Render settings as ordered safe YAML that read_configuration can reload.

    Shared Python values are written independently; no anchors or special tags
    are emitted. This helper preserves values and does not apply stage defaults
    or validation; use load_config/resolve_settings to resolve settings first.
    """
    if type(config) is not dict:
        raise ValueError("Configuration must be a mapping/object")
    try:
        _json_values(config)
    except RecursionError as error:
        raise ValueError("Configuration nesting is too deep") from error
    yaml = _yaml_module()

    class SettingsDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    try:
        return yaml.dump(
            config,
            Dumper=SettingsDumper,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
    except (yaml.YAMLError, RecursionError) as error:
        raise ValueError(f"Cannot render YAML configuration: {error}") from error
