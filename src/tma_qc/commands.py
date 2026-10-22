"""Application handlers for the CLI; scientific methods live in stages/backends.

Imports of image libraries are deferred until a command needs them, so help and
legacy metadata-only tools remain usable without the imaging dependencies.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path


def _execution_exit_code(summary):
    """CLI status for execution only: 0 complete, 1 error, 2 blocked."""
    return 1 if summary["execution_errors"] else 2 if summary["blocked_stages"] else 0


def _settings_options(args):
    from .settings import parse_parameters

    return {
        "parameters": parse_parameters(getattr(args, "parameter_assignments", None)),
        **{
            name: getattr(args, name, None)
            for name in (
                "geometry_method",
                "illumination_method",
                "registration_method",
                "registration_transform",
                "registration_refinement",
            )
        },
    }


def list_methods(args: Namespace) -> int:
    """List supported choices without importing image or optional method libraries."""
    from .settings import method_registry

    registry = method_registry()
    if args.json_output:
        print(json.dumps(registry, indent=2, allow_nan=False))
        return 0
    print("TMA QC methods and defaults")
    for selector, section in registry["methods"].items():
        print(f"\n--{selector.replace('_', '-')} (default: {section['default']})")
        for name, choice in section["choices"].items():
            print(f"  {name}: {choice['description']}")
            if choice["requirements"]:
                print("    Requires: " + "; ".join(choice["requirements"]))
    print("\nAF stage sequence (fixed): " + " → ".join(registry["fixed_af_stages"]))
    print("Pair stage sequence (fixed): " + " → ".join(registry["fixed_pair_stages"]))
    print("\nFixed algorithm stages (settings may still be tuned):")
    for name, item in registry["fixed_algorithms"].items():
        print(f"  {name}: {item['description']}")
    print("\nOverride precedence: " + " → ".join(registry["precedence"]))
    for note in registry["notes"]:
        print(note)
    print("Use run/plan/qc --help for method flags and repeated --set SECTION.KEY=VALUE.")
    return 0


def run_preprocessing(args: Namespace) -> int:
    """Run verified AF inputs and explicitly assigned H&E pairs."""
    from .pipeline import run_pipeline

    summary = run_pipeline(
        args.manifest,
        args.out,
        args.config,
        resume=args.resume,
        selected=args.core,
        **_settings_options(args),
    )
    print(f"Report: {Path(args.out).resolve() / 'reports/index.html'}")
    return _execution_exit_code(summary)


def import_transfer(args: Namespace) -> int:
    """Create a manifest from a completed transfer inventory opened read-only."""
    from .io.manifest import import_copy_inventory

    data = import_copy_inventory(args.database, args.raw_root, args.out, args.models)
    blocked = sum(bool(c["blocked_reasons"]) for c in data["cores"])
    print(
        f"Indexed {len(data['cores'])} core acquisitions: {len(data['cores']) - blocked} eligible, {blocked} blocked for review."
    )
    print("Add reviewed geometry and explicit section pairs.")
    return 0


def import_folder(args: Namespace) -> int:
    """Discover NAS-style AF acquisitions and hash their selected original tiles."""
    from .io.folder import import_raw_folder

    print("Scanning raw folders and hashing selected AF tiles...", flush=True)
    data = import_raw_folder(args.raw_root, args.out, args.models, dataset_id=args.dataset_id)
    coverage = data["metadata"]["import_coverage"]
    print(
        f"Indexed {len(data['cores'])} core acquisitions: "
        f"{coverage['eligible']} with candidate FOV sets, {coverage['blocked']} blocked for review."
    )
    print(f"Manifest: {Path(args.out).resolve()}")
    print("Review source completeness, model assignments, geometry and optional H&E pairs.")
    return 0


def import_pairs(args: Namespace) -> int:
    """Hash explicitly assigned H&E inputs and publish a new manifest."""
    from .io.section_pairs import import_section_pairs

    data = import_section_pairs(args.manifest, args.pairs, args.out)
    print(f"Manifest: {Path(args.out).resolve()} ({len(data['pairs'])} explicit section pairs)")
    print("Review section identities and geometry; select the intended registration backend.")
    return 0


def preflight(args: Namespace) -> int:
    """Print source/asset/capacity readiness without processing images."""
    from .io.manifest import Manifest
    from .preflight import check
    from .settings import resolve_settings

    config = resolve_settings(args.config, **_settings_options(args))
    result = check(Manifest(args.manifest), config, args.out, args.core)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["ready_for_execution"] else 2


def _saved_run(args):
    from .io.manifest import Manifest
    from .provenance import read_json

    root = Path(args.run).resolve()
    record = read_json(root / "run.json")
    return root, record, Manifest(record["manifest_path"])


def rebuild_report(args: Namespace) -> int:
    """Rebuild a saved run's offline report without processing its images."""
    from .provenance import read_json
    from .reporting.batch import build_report
    from .storage import validate_writable_tree

    root, record, manifest = _saved_run(args)
    validate_writable_tree(root, manifest.raw_root)
    build_report(root, manifest, read_json(root / "stage-results.json"), record["parameters"])
    return 0


def reevaluate_qc(args: Namespace) -> int:
    """Replay the saved manifest with explicit configuration overrides."""
    from .pipeline import Runner
    from .settings import resolve_settings

    root, record, manifest = _saved_run(args)
    config = resolve_settings(args.config, base=record["parameters"], **_settings_options(args))
    runner = Runner(manifest, root, config, resume=True)
    try:
        result = runner.run(record.get("selected_cores"))
    finally:
        runner.state.close()
    return _execution_exit_code(result)


def run_demo(args: Namespace) -> int:
    """Create synthetic data and execute the software example."""
    from .demo import create_demo
    from .pipeline import run_pipeline

    manifest, config = create_demo(args.out)
    result = run_pipeline(manifest, Path(args.out) / "results", config)
    print(f"SYNTHETIC SOFTWARE DEMO: {Path(args.out).resolve() / 'results/reports/index.html'}")
    return 1 if result["execution_errors"] or result["blocked_stages"] else 0


def fit_cidre(args: Namespace) -> int:
    """Validate an explicit compatible fitting pool and call pinned Java CIDRE."""
    from .backends.cidre import fit_model
    from .io.manifest import Manifest
    from .provenance import read_json, write_json
    from .qc.integrity import verify_file

    manifest = Manifest(args.manifest)
    cores = [c for c in manifest.cores if c.group == args.group and not c.blocked_reasons]
    if len(cores) < 4:
        raise ValueError(
            "CIDRE fitting requires an explicit compatible pool from at least four cores"
        )
    pool = read_json(args.pool)
    selected = [(r["core_uid"], str(r["fov_id"])) for r in pool]
    if (
        len(set(selected)) != len(selected)
        or len(selected) < 64
        or len({r[0] for r in selected}) < 4
    ):
        raise ValueError(
            "Fitting pool must contain at least 64 unique FOVs from at least four cores"
        )
    available = {(c.uid, f.id): f for c in cores for f in c.fovs}
    if any(key not in available for key in selected):
        raise ValueError("Fitting pool contains inputs outside the explicit compatible group")
    inputs, verified_pool = [], []
    seen_paths, seen_hashes = set(), set()
    for key in selected:
        f = available[key]
        path = manifest.raw_path(f)
        digest = verify_file(path, f.sha256)
        if path in seen_paths or digest in seen_hashes:
            raise ValueError(
                "CIDRE fitting pool reuses a source file or exact file contents under different FOV labels"
            )
        seen_paths.add(path)
        seen_hashes.add(digest)
        inputs.append(path)
        verified_pool.append(
            {"core_uid": key[0], "fov_id": key[1], "path": str(path), "sha256": digest}
        )
    record = fit_model(inputs, Path(args.out).resolve(), Path(args.runtime).resolve())
    record.update(group_id=args.group, selected_pool=pool, verified_pool=verified_pool)
    write_json(Path(args.out) / "model.json", record)
    print(
        "Model fitted. Method QC remains NOT PASSED; review before adding the fields to the manifest."
    )
    return 0
