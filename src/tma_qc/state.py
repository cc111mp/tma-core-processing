"""Local checkpoints and a process lock. Scientific artifacts remain file based."""

from __future__ import annotations

import fcntl
import json
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from .provenance import fingerprint, utc
from .schema import StageResult


def filesystem_type(path):
    """Inspect the existing mount table without opening NAS directories."""
    try:
        mounts = []
        for line in Path("/proc/mounts").read_text().splitlines():
            fields = line.split()
            mount = Path(fields[1].replace("\\040", " "))
            if path == mount or mount in path.parents:
                mounts.append((len(str(mount)), fields[2]))
        return max(mounts)[1] if mounts else None
    except OSError:
        return None


class State:
    def __init__(self, directory, lock_identity=None):
        directory = Path(directory).resolve()
        if filesystem_type(directory) in {"cifs", "smbfs", "nfs", "nfs4", "fuse.sshfs"}:
            raise ValueError(
                "SQLite checkpoints require local storage; set runtime.cache_root to a local SSD"
            )
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.lock_directory = (
            Path(tempfile.gettempdir())
            / "tma-qc-locks"
            / fingerprint(str(lock_identity or directory))
        )
        self.lock_directory.mkdir(parents=True, exist_ok=True)
        self.db = None
        self.events = []

    def _open(self):
        self.db = sqlite3.connect(self.directory / "state.sqlite3", timeout=30)
        try:
            checked = self.db.execute("PRAGMA quick_check").fetchall()
            if checked != [("ok",)]:
                raise ValueError("Checkpoint integrity check failed: " + repr(checked))
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS stages (unit TEXT, stage TEXT, fingerprint TEXT, result TEXT, PRIMARY KEY(unit,stage,fingerprint))"
            )
            columns = [r[1] for r in self.db.execute("PRAGMA table_info(stages)")]
            if columns != ["unit", "stage", "fingerprint", "result"]:
                raise ValueError("Unsupported checkpoint schema")
            self.db.commit()
        except Exception:
            self.close()
            raise

    def _initialize(self):
        if self.db is not None:
            return
        try:
            self._open()
        except (sqlite3.DatabaseError, ValueError) as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 255
            if isinstance(exc, sqlite3.DatabaseError) and code not in {
                sqlite3.SQLITE_CORRUPT,
                sqlite3.SQLITE_NOTADB,
            }:
                raise  # Busy, permissions, disk-full and I/O failures are not corruption.
            quarantine = self.directory / "quarantine" / uuid.uuid4().hex
            quarantine.mkdir(parents=True)
            moved = []
            for name in ("state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm"):
                path = self.directory / name
                if path.exists():
                    path.rename(quarantine / name)
                    moved.append(name)
            self.events.append(
                {
                    "source": "sqlite-database",
                    "reason": str(exc),
                    "created_utc": utc(),
                    "quarantine": str(quarantine),
                    "preserved_files": moved,
                }
            )
            self._open()

    def get(self, unit, stage, key):
        row = self.db.execute(
            "SELECT result FROM stages WHERE unit=? AND stage=? AND fingerprint=?",
            (unit, stage, key),
        ).fetchone()
        return StageResult(**json.loads(row[0])) if row else None

    def put(self, result: StageResult):
        self.db.execute(
            "INSERT OR REPLACE INTO stages VALUES (?,?,?,?)",
            (
                result.unit,
                result.stage,
                result.fingerprint,
                json.dumps(result.to_dict(), allow_nan=False),
            ),
        )
        self.db.commit()

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    @contextmanager
    def lock(self):
        with (self.lock_directory / "run.lock").open("a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another process is running this output directory") from exc
            try:
                self._initialize()
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
