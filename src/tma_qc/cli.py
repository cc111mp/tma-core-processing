"""Argument parsing and error boundary for the tma-qc command."""

from __future__ import annotations

import argparse
import sqlite3
import sys

from . import commands
from .settings import method_choices


def _method_options(parser):
    options = parser.add_argument_group("Methods and parameter overrides")
    for name, help_text in (
        ("geometry_method", "Tile position source"),
        ("illumination_method", "Illumination correction method"),
        ("registration_method", "Registration implementation for explicit section pairs"),
        ("registration_transform", "Global transform for core registration methods"),
        ("registration_refinement", "Local refinement for core registration methods"),
    ):
        options.add_argument(
            "--" + name.replace("_", "-"), choices=method_choices(name), help=help_text
        )
    options.add_argument(
        "--set",
        dest="parameter_assignments",
        action="append",
        metavar="SECTION.KEY=VALUE",
        help="Typed TOML override; repeat for separate keys, e.g. runtime.threads=2",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the supported command/option interface without opening data."""
    parser = argparse.ArgumentParser(
        prog="tma-qc",
        description="Reconstruct TMA cores from explicit inputs and inspect their QC evidence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Start here:\n"
            "  tma-qc demo --out /tmp/tma-qc-demo\n"
            "  tma-qc import-folder --raw-root /data/TMA_raw --out /data/manifest.json\n"
            "  tma-qc run --manifest /data/manifest.json --out /results/run_001 --resume\n"
            "\nInspect choices with 'tma-qc methods' and effective values with 'tma-qc settings'.\n"
            "Use 'tma-qc COMMAND --help' for command options."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser(
        "run",
        help="Run or resume preprocessing on verified inputs",
        description="Reconstruct prepared AF cores and any explicitly paired H&E sections.",
        epilog="Open OUT/reports/index.html for images, QC measurements and review evidence.",
    )
    run.add_argument("--manifest", required=True, help="Prepared input manifest JSON")
    run.add_argument("--config", help="TOML settings; omitted values use built-in defaults")
    run.add_argument("--out", required=True, help="Run directory outside the raw archive")
    run.add_argument(
        "--resume", action="store_true", help="Reuse unchanged stages after verifying saved outputs"
    )
    run.add_argument(
        "--core", action="append", help="Manifest core UID; repeat to select several; default: all"
    )
    _method_options(run)
    plan = sub.add_parser(
        "plan",
        help="Check source hashes, assets, runtime prerequisites and AF capacity without processing",
    )
    plan.add_argument("--manifest", required=True, help="Prepared input manifest JSON")
    plan.add_argument("--config", help="TOML settings; omitted values use built-in defaults")
    plan.add_argument(
        "--out", help="Planned output location, required for an operational readiness result"
    )
    plan.add_argument(
        "--core", action="append", help="Manifest core UID; repeat to select several; default: all"
    )
    _method_options(plan)
    index = sub.add_parser(
        "import-copy", help="Import a completed transfer SQLite inventory read-only"
    )
    index.add_argument("--database", required=True)
    index.add_argument("--raw-root", required=True)
    index.add_argument("--models", help="Optional JSON mapping group IDs to model assets")
    index.add_argument("--out", required=True)
    folder = sub.add_parser(
        "import-folder", help="Build an AF manifest from NAS-style raw acquisition folders"
    )
    folder.add_argument("--raw-root", required=True, help="Raw AF root containing slide folders")
    folder.add_argument("--out", required=True, help="New manifest outside the raw archive")
    folder.add_argument("--dataset-id", default="TMA-2026")
    folder.add_argument("--models", help="Optional JSON mapping group IDs to model assets")
    pairs = sub.add_parser("import-pairs", help="Add reviewed AF–H&E pairs from an explicit CSV")
    pairs.add_argument("--manifest", required=True, help="Existing AF manifest")
    pairs.add_argument("--pairs", required=True, help="Pair CSV; asset paths resolve beside it")
    pairs.add_argument("--out", required=True, help="New manifest; existing files are preserved")
    report = sub.add_parser("report", help="Rebuild the offline report from saved stage records")
    report.add_argument("--run", required=True, help="Existing run directory containing run.json")
    qc = sub.add_parser(
        "qc", help="Resume with updated QC settings, retaining unchanged image artifacts"
    )
    qc.add_argument("--run", required=True, help="Existing run directory containing run.json")
    qc.add_argument("--config", help="TOML overrides applied to the saved run configuration")
    _method_options(qc)
    methods = sub.add_parser("methods", help="List methods, defaults and required inputs")
    methods.add_argument(
        "--json", action="store_true", dest="json_output", help="Print structured method metadata"
    )
    settings = sub.add_parser(
        "settings", help="Print effective settings as JSON without opening images or creating a run"
    )
    settings.add_argument("--config", help="TOML settings; omitted values use built-in defaults")
    _method_options(settings)
    demo = sub.add_parser("demo", help="Create and run an explicitly synthetic software example")
    demo.add_argument("--out", required=True)
    fit = sub.add_parser(
        "fit-cidre", help="Fit the pinned Java CIDRE on an explicit compatible pool"
    )
    fit.add_argument("--manifest", required=True)
    fit.add_argument("--group", required=True)
    fit.add_argument(
        "--pool", required=True, help="JSON list of explicit {core_uid,fov_id} fitting inputs"
    )
    fit.add_argument("--runtime", required=True)
    fit.add_argument("--out", required=True)
    return parser


def main(argv=None) -> int:
    """Dispatch a command, preserving documented exit codes and error output."""
    args = build_parser().parse_args(argv)
    handlers = {
        "run": commands.run_preprocessing,
        "methods": commands.list_methods,
        "settings": commands.show_settings,
        "import-copy": commands.import_transfer,
        "import-folder": commands.import_folder,
        "import-pairs": commands.import_pairs,
        "plan": commands.preflight,
        "report": commands.rebuild_report,
        "qc": commands.reevaluate_qc,
        "demo": commands.run_demo,
        "fit-cidre": commands.fit_cidre,
    }
    try:
        return handlers[args.command](args)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, sqlite3.DatabaseError) as exc:
        print(f"tma-qc: {exc}", file=sys.stderr)
        return 1
