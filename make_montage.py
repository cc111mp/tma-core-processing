"""Montage - one big picture per slide with every core in its TMA position.

Writes <slide>_AF.png, <slide>_HE.png and <slide>_overlay.png per registered slide.
Read-only for the data. Needs numpy, tifffile, imagecodecs and Pillow
(see requirements-image.txt).

  python make_montage.py "<registered data folder>" --out "<folder>"
  python make_montage.py "<registered data folder>" --out "<folder>" --registration-report "<registration.csv>"
"""

import argparse
import sys
from pathlib import Path

from tma_qc.montage import run_montage
from tma_qc.report import load_settings

HERE = Path(__file__).resolve().parent


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # show progress live, also in log files
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("data_folder", help="e.g. the Project 1_CQ_UV_HE_Reg folder, or one slide")
    parser.add_argument("--out", required=True, help="folder for the PNG files")
    parser.add_argument(
        "--registration-report",
        help="registration.csv from check_registration.py, to colour tile borders",
    )
    parser.add_argument("--settings", default=HERE / "settings.toml")
    args = parser.parse_args()
    run_montage(args.data_folder, args.out, load_settings(args.settings), args.registration_report)


if __name__ == "__main__":
    main()
