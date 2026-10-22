"""Resumable dependency graph; immutable stage artifacts and failure isolation."""

from __future__ import annotations

import importlib.metadata
import sqlite3
import traceback
import uuid
from pathlib import Path

import cv2

from .cohort import Cohort
from .context import Context as Context
from .io.manifest import Manifest
from .provenance import code_identity, fingerprint, read_json, sha256, utc, write_json
from .schema import Execution, NeedsInput, Payload, Quality, StageResult
from .settings import resolve_settings
from .state import State
from .storage import estimate_batch, validate_writable_tree
from .workflows import process_core, process_pair


def _dependencies(ctx, deps):
    return {
        name: {
            "fingerprint": ctx.results[name].fingerprint,
            "outputs": {k: v["sha256"] for k, v in ctx.results[name].outputs.items()},
        }
        for name in deps
        if name in ctx.results
    }


class Runner:
    """Execute stage functions with checksummed receipts and failure isolation.

    The scientific sequence is declared in workflows.py. This class handles the
    run lock, cache validation, stage transactions, cohort history and reports.
    """

    def __init__(self, manifest, root, config, *, resume=True):
        self.manifest = manifest
        self.root = Path(root).resolve()
        self.config = config
        self.cache = Path(config["runtime"]["cache_root"]).expanduser().resolve()
        for label, path in (("Results", self.root), ("Cache", self.cache)):
            if path.is_relative_to(manifest.raw_root) or manifest.raw_root.is_relative_to(path):
                raise ValueError(
                    f"{label} must be outside the immutable raw archive with disjoint directory trees"
                )
        for path in (self.root, self.cache):
            validate_writable_tree(path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.code = code_identity()
        self.versions = {
            p: importlib.metadata.version(p) for p in ["numpy", "scipy", "tifffile", "Pillow"]
        }
        self.versions["opencv"] = cv2.__version__
        self.resume = resume
        self.scratch = self.cache / "scratch"
        self.state = State(
            self.cache / "state" / fingerprint(str(self.root)), lock_identity=self.root
        )
        self.results = []
        self.cache_events = self.state.events
        self.cohort = None
        self.attempt_id = None
        self.tile_cache_statistics = []

    def summary(self, complete=False):
        return {
            "updated_utc": utc(),
            "dataset_id": self.manifest.dataset_id,
            "processing_attempt_finished": complete,
            "stage_results": [r.to_dict() for r in self.results],
            "execution_errors": sum(r.execution == Execution.ERROR for r in self.results),
            "blocked_stages": sum(r.execution == Execution.BLOCKED for r in self.results),
            "cache_recovery_events": self.cache_events,
            "decoded_tile_cache": self.tile_cache_statistics,
            "code_sha256": self.code,
            "runtime_versions": self.versions,
            "attempt_id": self.attempt_id,
            "automatic_image_quality_release": False,
        }

    def cached_stage(self, unit, name, key, destination):
        """Untrusted cache metadata is an optimization, never an execution gate."""
        for source in ("sqlite", "receipt"):
            try:
                if source == "sqlite":
                    cached = self.state.get(unit, name, key)
                else:
                    receipt = destination / "stage.json"
                    cached = StageResult(**read_json(receipt)) if receipt.is_file() else None
                if cached is None:
                    continue
                if (cached.unit, cached.stage, cached.fingerprint) != (unit, name, key):
                    raise ValueError(
                        "Cache identity does not match this unit, stage and input fingerprint"
                    )
                fingerprint(
                    cached.to_dict()
                )  # Reject nonfinite/invalid JSON values from SQLite too.
                if cached.execution != Execution.COMPLETE:
                    continue
                if not isinstance(cached.outputs, dict) or not cached.outputs:
                    raise ValueError("Complete cache receipt has no output records")
                for value in cached.outputs.values():
                    path = self.root / value["path"]
                    if not path.resolve().is_relative_to(destination.resolve()):
                        raise ValueError("Cached output is outside its stage directory")
                    if not path.is_file() or sha256(path) != value["sha256"]:
                        raise ValueError("Cached artifact is missing or its SHA256 has changed")
                cached.cached = True
                if source == "receipt":
                    self.state.put(cached)
                return cached
            except (ValueError, TypeError, KeyError, OSError, sqlite3.DatabaseError) as exc:
                self.cache_events.append(
                    {"unit": unit, "stage": name, "source": source, "reason": str(exc)}
                )
        return None

    def stage(self, ctx, unit, base, name, fn, deps=(), config_sections=(), extra=None):
        """Resolve inputs, reuse a valid receipt, or save one stage transaction.

        ``extra`` may be callable: asset checks then run inside this boundary,
        so a missing geometry/model asset cannot abort an unrelated stage/core.
        """
        failed = [
            d
            for d in deps
            if d not in ctx.results or ctx.results[d].execution != Execution.COMPLETE
        ]
        problem = None
        input_blocked = False
        try:
            ctx.validate_sources()
            assets = extra() if callable(extra) else extra
            ctx.validate_sources()
        except Exception as exc:
            assets, problem = {}, str(exc)
            input_blocked = isinstance(exc, NeedsInput)
        provenance = {
            "code_sha256": self.code,
            "runtime_versions": self.versions,
            "opencv_threads": self.config["runtime"]["threads"],
            "parameters": {k: self.config[k] for k in config_sections},
            "dependencies": _dependencies(ctx, deps),
            "inputs": assets or {},
        }
        stage_inputs = {
            "unit": unit,
            "stage": name,
            **provenance,
            "blocked_dependencies": failed,
            "input_error": problem,
        }
        key = fingerprint(stage_inputs)
        destination = base / name / key[:20]
        if not destination.resolve().is_relative_to(self.root):
            raise ValueError(f"Stage destination escapes the output directory: {destination}")
        result = (
            self.cached_stage(unit, name, key, destination)
            if self.resume and not failed and not problem
            else None
        )
        if result is not None:
            try:
                ctx.validate_sources()
            except Exception as exc:
                # A source may change while cached artifacts are being hashed.
                # Record that error under its own input fingerprint, preserving
                # the valid old artifacts and isolating unrelated cores.
                problem = str(exc)
                input_blocked = isinstance(exc, NeedsInput)
                stage_inputs["input_error"] = problem
                key = fingerprint(stage_inputs)
                destination = base / name / key[:20]
                result = None
        if result is None:
            if destination.exists():
                archive = self.root / "superseded" / (unit + "-" + name + "-" + uuid.uuid4().hex)
                archive.parent.mkdir(parents=True, exist_ok=True)
                destination.rename(archive)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = destination.parent / (".partial-" + uuid.uuid4().hex)
            temp.mkdir()
            try:
                if failed:
                    raise NeedsInput("Unavailable stage dependencies: " + ", ".join(failed))
                if problem:
                    raise (NeedsInput if input_blocked else ValueError)(problem)
                ctx.validate_sources()
                payload = fn(ctx, temp)
                ctx.validate_sources()
                if not isinstance(payload, Payload):
                    raise TypeError("Stage did not return a Payload")
                if any(
                    not p.is_file() or not p.resolve().is_relative_to(temp.resolve())
                    for p in payload.outputs.values()
                ):
                    raise ValueError("Declared stage outputs must exist inside the stage directory")
                outputs = {}
                named = {p.resolve(): name for name, p in payload.outputs.items()}
                for p in sorted(temp.rglob("*")):
                    if p.is_file():
                        relative = p.relative_to(temp)
                        label = named.get(p.resolve(), "artifact:" + relative.as_posix())
                        outputs[label] = {
                            "path": (destination / relative).relative_to(self.root).as_posix(),
                            "sha256": sha256(p),
                            "bytes": p.stat().st_size,
                        }
                result = StageResult(
                    unit,
                    name,
                    key,
                    Execution.COMPLETE,
                    payload.qc,
                    utc(),
                    outputs,
                    payload.metrics,
                    payload.findings,
                    provenance,
                )
                fingerprint(result.to_dict())
            except Exception as exc:
                blocked = isinstance(exc, NeedsInput)
                (temp / "error.txt").write_text(traceback.format_exc())
                error_path = destination / "error.txt"
                result = StageResult(
                    unit,
                    name,
                    key,
                    Execution.BLOCKED if blocked else Execution.ERROR,
                    Quality.NOT_ASSESSED,
                    utc(),
                    {
                        "error": {
                            "path": error_path.relative_to(self.root).as_posix(),
                            "sha256": sha256(temp / "error.txt"),
                            "bytes": (temp / "error.txt").stat().st_size,
                        }
                    },
                    {},
                    [str(exc)],
                    provenance,
                )
                for p in temp.rglob("*"):
                    if p.is_file() and p.suffix in {".log", ".json"}:
                        relative = p.relative_to(temp)
                        result.outputs["diagnostic:" + relative.as_posix()] = {
                            "path": (destination / relative).relative_to(self.root).as_posix(),
                            "sha256": sha256(p),
                            "bytes": p.stat().st_size,
                        }
            write_json(temp / "stage.json", result.to_dict())
            temp.rename(destination)
            self.state.put(result)
        ctx.results[name] = result
        self.results.append(result)
        self.cohort.record(result)
        write_json(self.root / "stage-results.json", self.summary())
        print(
            f"{unit} | {name}: {result.execution} / {result.qc}"
            + (" (cached)" if result.cached else ""),
            flush=True,
        )
        return result

    def run(self, selected=None):
        """Run selected AF cores and their explicit section pairs under one lock."""
        cv2.setNumThreads(self.config["runtime"]["threads"])
        with self.state.lock():
            existing = self.root / "run.json"
            if existing.exists() and read_json(existing)["dataset_id"] != self.manifest.dataset_id:
                raise ValueError("An output run cannot mix different dataset identities")
            known = {c.uid for c in self.manifest.cores}
            if selected and not set(selected) <= known:
                raise ValueError("Unknown selected core identifiers")
            frozen = self.manifest.snapshot()
            frozen_path = self.root / "manifests" / (fingerprint(frozen) + ".json")
            if not frozen_path.exists():
                write_json(frozen_path, frozen)
            attempt = {
                "created_utc": utc(),
                "dataset_id": self.manifest.dataset_id,
                "manifest_path": str(frozen_path),
                "manifest_sha256": sha256(frozen_path),
                "source_manifest_path": str(self.manifest.path),
                "source_manifest_sha256": sha256(self.manifest.path),
                "code_sha256": self.code,
                "runtime_versions": self.versions,
                "parameters": self.config,
                "selected_cores": selected,
                "automatic_image_quality_release": False,
            }
            self.attempt_id = uuid.uuid4().hex
            attempt["attempt_id"] = self.attempt_id
            write_json(self.root / "attempts" / (self.attempt_id + ".json"), attempt)
            write_json(existing, attempt)
            write_json(self.root / "stage-results.json", self.summary())
            from .reporting.batch import mark_report_incomplete

            mark_report_incomplete(self.root, self.manifest, attempt["created_utc"])
            self.cohort = Cohort(self.root, self.manifest, self.config, self.code, self.versions)
            selected_ids = {c.uid for c in self.manifest.cores if not selected or c.uid in selected}
            for uid in [
                *sorted(selected_ids),
                *(p.uid for p in self.manifest.pairs if p.af_core in selected_ids),
            ]:
                self.cohort.begin(uid, self.attempt_id)
            try:
                budget = estimate_batch(self.manifest, self.config, self.root, selected)
            except (OSError, ValueError, RuntimeError) as exc:
                budget = {"complete": False, "reason": str(exc)}
            write_json(self.root / "AF-storage-plan.json", budget)
            by_core = {}
            for core in self.manifest.cores:
                if selected and core.uid not in selected:
                    continue
                by_core[core.uid] = process_core(self, core)
            for pair in self.manifest.pairs:
                if pair.af_core in by_core:
                    process_pair(self, pair, by_core[pair.af_core])
            summary = self.summary(complete=True)
            write_json(self.root / "stage-results.json", summary)
            from .reporting.batch import build_report

            build_report(
                self.root,
                self.manifest,
                summary,
                self.config,
                cohort=self.cohort.snapshot(self.attempt_id),
            )
            return summary


def run_pipeline(
    manifest_path,
    output,
    config_path=None,
    *,
    resume=True,
    selected=None,
    parameters=None,
    geometry_method=None,
    illumination_method=None,
    registration_method=None,
    registration_transform=None,
    registration_refinement=None,
):
    """Load a manifest/configuration, execute the workflow, and close local state.

    ``selected`` contains manifest core UIDs. The returned summary distinguishes
    execution errors/blocked stages from scientific quality states. Parameters
    override the TOML file; explicit method selectors override parameters.
    """
    config = resolve_settings(
        config_path,
        parameters=parameters,
        geometry_method=geometry_method,
        illumination_method=illumination_method,
        registration_method=registration_method,
        registration_transform=registration_transform,
        registration_refinement=registration_refinement,
    )
    manifest = Manifest(manifest_path)
    runner = Runner(manifest, output, config, resume=resume)
    try:
        return runner.run(selected)
    finally:
        runner.state.close()
