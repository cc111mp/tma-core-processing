"""Content identities and atomic, strictly finite JSON records."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def write_json(path: str | Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, allow_nan=False, sort_keys=True) + "\n"
    fd, name = tempfile.mkstemp(prefix=".json-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_new_json(path: str | Path, value) -> None:
    """Publish complete JSON while preserving any concurrent/existing output."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        write_json(temporary, value)
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: str | Path):
    def invalid(value):
        raise ValueError(f"Nonfinite JSON value: {value}")

    return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=invalid)


def code_identity() -> str:
    root = Path(__file__).resolve().parent
    return fingerprint(
        {p.relative_to(root).as_posix(): sha256(p) for p in sorted(root.rglob("*.py"))}
    )
