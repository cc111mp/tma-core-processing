"""Explicit identities, source roles, and separate execution/quality states."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

CORE_STAGES = (
    "index",
    "tile_qc",
    "geometry",
    "geometry_qc",
    "illumination",
    "illumination_qc",
    "mosaic",
    "mosaic_qc",
    "tissue",
)
PAIR_STAGES = ("he_qc", "registration", "registration_qc")


class Execution(StrEnum):
    COMPLETE = "complete"
    ERROR = "error"
    BLOCKED = "blocked"


class Quality(StrEnum):
    PASS = "pass"
    REVIEW = "review"
    FAIL = "fail"
    NOT_ASSESSED = "not_assessed"


def safe_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", value)
        or ".." in value
    ):
        raise ValueError(f"Unsafe or invalid identifier: {value!r}")
    return value


@dataclass
class FOV:
    id: str
    path: str
    sha256: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.id = str(self.id)
        safe_id(self.id)
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError(f"FOV {self.id}: a source SHA256 is required")


@dataclass
class Core:
    uid: str
    acquisition: str
    slide: str
    core: str
    channel: str
    group: str
    fovs: list[FOV]
    positions: str | None = None
    geometry_reviewed: bool = False
    role: str = "raw_camera"
    metadata: dict = field(default_factory=dict)
    blocked_reasons: list[str] = field(default_factory=list)

    def __post_init__(self):
        for v in [self.uid, self.acquisition, self.slide, self.core, self.channel, self.group]:
            safe_id(v)
        if self.role != "raw_camera":
            raise ValueError(f"{self.uid}: only explicit raw_camera FOVs enter correction")
        if not isinstance(self.blocked_reasons, list) or any(
            not isinstance(v, str) or not v.strip() for v in self.blocked_reasons
        ):
            raise ValueError("blocked_reasons must be a list of nonempty explanations")
        if (not self.fovs and not self.blocked_reasons) or len({v.id for v in self.fovs}) != len(
            self.fovs
        ):
            raise ValueError(f"{self.uid}: empty or duplicate FOV ids")
        if len({v.path for v in self.fovs}) != len(self.fovs):
            raise ValueError(f"{self.uid}: one source path assigned to multiple FOV ids")
        if type(self.geometry_reviewed) is not bool:
            raise ValueError("geometry_reviewed must be a boolean")


@dataclass
class Group:
    id: str
    flatfield: str | None = None
    darkfield: str | None = None
    model_metadata: str | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        safe_id(self.id)
        if bool(self.flatfield) != bool(self.darkfield):
            raise ValueError("Supply both flatfield and darkfield")


@dataclass
class SectionPair:
    uid: str
    af_core: str
    he_path: str
    he_sha256: str
    relationship: str = "neighboring_sections"
    transform: str | None = None
    landmarks: str | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        safe_id(self.uid)
        if self.relationship not in {"neighboring_sections", "same_section"}:
            raise ValueError("Section relationship must be explicitly identified")
        if not re.fullmatch(r"[0-9a-f]{64}", self.he_sha256):
            raise ValueError("H&E source SHA256 is required")


@dataclass
class StageResult:
    unit: str
    stage: str
    fingerprint: str
    execution: str
    qc: str
    created_utc: str
    outputs: dict[str, dict] = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    cached: bool = False

    def __post_init__(self):
        Execution(self.execution)
        Quality(self.qc)

    def to_dict(self):
        return asdict(self)


@dataclass
class Payload:
    outputs: dict[str, Path] = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    qc: str = Quality.REVIEW


class NeedsInput(RuntimeError):
    """A recoverable missing prerequisite; never an implicit quality pass."""
