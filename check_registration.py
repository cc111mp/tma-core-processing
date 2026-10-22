"""Registration check - is every core registered, and does AF line up with HE?

Read-only. Uses only the text files written by the registration code.

  python check_registration.py "<registered data folder>" --out "<report folder>"

See README.md for what each message means.
"""

import argparse
import sys
from pathlib import Path

from tma_qc.registration_check import run_registration_check
from tma_qc.report import load_settings

HERE = Path(__file__).resolve().parent


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # show progress live, also in log files
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("data_folder", help="e.g. the Project 1_CQ_UV_HE_Reg folder, or one slide")
    parser.add_argument("--out", required=True, help="folder for the report")
    parser.add_argument("--settings", default=HERE / "settings.toml")
    args = parser.parse_args()
    run_registration_check(args.data_folder, args.out, load_settings(args.settings))


if __name__ == "__main__":
    main()
