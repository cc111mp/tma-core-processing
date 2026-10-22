"""Transfer check - count slides, cores and FOVs and flag anything odd.

Read-only: it never changes, moves or deletes data.

  python check_transfer.py scan "<data folder>" --out "<report folder>"
  python check_transfer.py scan "<data folder>" --out "<report folder>" --reference reference.csv
  python check_transfer.py compare "<first report folder>" "<second report folder>" --out "<report folder>"

See README.md for what each alert means.
"""

import argparse
import sys
from pathlib import Path

from tma_qc.report import load_settings
from tma_qc.transfer_check import run_compare, run_scan

HERE = Path(__file__).resolve().parent


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # show progress live, also in log files
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--settings",
        default=HERE / "settings.toml",
        help="settings file (default: settings.toml next to this script)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="read a data folder and write a report")
    scan.add_argument("data_folder")
    scan.add_argument("--out", required=True, help="folder for the report")
    scan.add_argument("--reference", help="optional CSV: slide, core, expected_fovs")
    scan.add_argument(
        "--rescan",
        action="store_true",
        help="read every slide again, even if already in the report folder",
    )

    compare = sub.add_parser("compare", help="compare the reports of two sites")
    compare.add_argument("first_report", help="report folder of the sending site")
    compare.add_argument("second_report", help="report folder of the receiving site")
    compare.add_argument("--out", required=True, help="folder for the comparison report")

    args = parser.parse_args()
    if args.command == "scan":
        run_scan(
            args.data_folder, args.out, load_settings(args.settings), args.reference, args.rescan
        )
    else:
        run_compare(args.first_report, args.second_report, args.out)


if __name__ == "__main__":
    main()
