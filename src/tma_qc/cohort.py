"""Latest per-unit observations across selected invocations of one run."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .provenance import fingerprint, read_json, sha256, utc, write_json
from .schema import StageResult


class Cohort:
    def __init__(self, root, manifest, config, code, versions):
        self.root = Path(root)
        self.directory = self.root / "cohort"
        self.manifest, self.config, self.code, self.versions = manifest, config, code, versions
        self.cores = {c.uid: c for c in manifest.cores}
        self.pairs = {p.uid: p for p in manifest.pairs}
        self.assets, self.definitions, self.active = {}, {}, {}

    def asset(self, path):
        if not path:
            return None
        if path not in self.assets:
            try:
                self.assets[path] = sha256(path)
            except OSError as exc:
                self.assets[path] = {"unavailable": str(exc)}
        return self.assets[path]

    def definition(self, uid):
        if uid not in self.definitions:
            common = {
                "dataset": self.manifest.dataset_id,
                "raw_root": str(self.manifest.raw_root),
                "code": self.code,
                "versions": self.versions,
                "config": self.config,
            }
            if uid in self.cores:
                core = self.cores[uid]
                group = self.manifest.group(core.group)
                inputs = {
                    "core": asdict(core),
                    "group": asdict(group),
                    "assets": {
                        str(path): self.asset(path)
                        for path in [
                            core.positions,
                            group.flatfield,
                            group.darkfield,
                            group.model_metadata,
                        ]
                        if path
                    },
                }
            else:
                pair = self.pairs[uid]
                paths = [pair.transform, pair.landmarks]
                registration = {}
                try:
                    if pair.transform:
                        from .qc.registration import transform_asset_paths

                        paths.extend(transform_asset_paths(pair.transform))
                    elif self.config["registration"]["backend"] == "core":
                        from .backends.core_registration import runtime_identity

                        registration = runtime_identity(self.config["registration"])
                except Exception as exc:
                    # A missing optional model/config is a pair prerequisite;
                    # recording its state must not abort unrelated AF work.
                    registration = {"unavailable": str(exc), "error_type": type(exc).__name__}
                inputs = {
                    "pair": asdict(pair),
                    "af_definition": self.definition(pair.af_core),
                    "assets": {str(path): self.asset(path) for path in paths if path},
                    "registration": registration,
                }
            self.definitions[uid] = fingerprint({**common, "inputs": inputs})
        return self.definitions[uid]

    def save(self, uid):
        self.active[uid]["updated_utc"] = utc()
        write_json(self.directory / (uid + ".json"), self.active[uid])

    def begin(self, uid, attempt):
        # Clear prior completion before the new attempt can be interrupted.
        self.active[uid] = {
            "schema_version": 1,
            "dataset_id": self.manifest.dataset_id,
            "unit": uid,
            "definition": self.definition(uid),
            "attempt_id": attempt,
            "processing_finished": False,
            "stage_results": [],
        }
        self.save(uid)

    def record(self, result):
        entry = self.active[result.unit]
        entry["stage_results"] = [
            r for r in entry["stage_results"] if r["stage"] != result.stage
        ] + [result.to_dict()]
        self.save(result.unit)

    def finish(self, uid):
        self.active[uid]["processing_finished"] = True
        self.save(uid)

    def snapshot(self, current_attempt=None):
        rows, units = [], {}
        for uid in [*self.cores, *self.pairs]:
            path = self.directory / (uid + ".json")
            info = {"observation": "not_run", "updated_utc": None, "attempt_id": None}
            if path.is_file():
                try:
                    entry = read_json(path)
                    if (
                        entry["schema_version"],
                        entry["dataset_id"],
                        entry["unit"],
                        entry["definition"],
                    ) != (1, self.manifest.dataset_id, uid, self.definition(uid)):
                        raise ValueError(
                            "Inputs, model assets, parameters or code differ from the recorded observation"
                        )
                    if type(entry["processing_finished"]) is not bool:
                        raise ValueError("Invalid cohort completion state")
                    fingerprint(entry)
                    chosen = [StageResult(**r).to_dict() for r in entry["stage_results"]]
                    if any(r["unit"] != uid for r in chosen) or len(
                        {r["stage"] for r in chosen}
                    ) != len(chosen):
                        raise ValueError("Invalid cohort unit/stage identity")
                    for row in chosen:
                        for asset in row["outputs"].values():
                            if (
                                not (self.root / asset["path"])
                                .resolve()
                                .is_relative_to(self.root.resolve())
                            ):
                                raise ValueError("Cohort artifact path outside this run")
                    info = {
                        "observation": "current_attempt"
                        if entry["attempt_id"] == current_attempt
                        else "historical_not_reverified",
                        "updated_utc": entry["updated_utc"],
                        "attempt_id": entry["attempt_id"],
                        "processing_finished": entry["processing_finished"],
                    }
                    rows.extend(chosen)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    info = {
                        "observation": "stale",
                        "reason": str(exc),
                        "updated_utc": None,
                        "attempt_id": None,
                    }
            units[uid] = info
        return {
            "schema_version": 1,
            "dataset_id": self.manifest.dataset_id,
            "updated_utc": utc(),
            "units": units,
            "stage_results": rows,
            "scope": "Latest compatible observation per unit. Historical raw files and artifact contents are not reverified by reporting. Current selected units are reverified by processing. Automatic release remains disabled.",
        }
