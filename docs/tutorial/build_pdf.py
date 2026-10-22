"""Print the self-contained tutorial with a local Chrome-compatible browser."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--browser", help="Path to Google Chrome, Chromium or another compatible browser"
    )
    parser.add_argument("--output", type=Path, default=here / "TMA_QC_User_Guide.pdf")
    args = parser.parse_args()
    browser = args.browser or next(
        (
            path
            for name in ["google-chrome", "chromium", "chromium-browser"]
            if (path := shutil.which(name))
        ),
        None,
    )
    if not browser:
        parser.error(
            "Install Chrome/Chromium or supply --browser. The HTML can also be printed manually."
        )
    source = here / "user-guide.html"
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tma-qc-tutorial-") as temporary:
        temporary = Path(temporary)
        rendered = temporary / "guide.pdf"
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-pdf-header-footer",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={temporary / 'profile'}",
            f"--print-to-pdf={rendered}",
            source.as_uri(),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode or not rendered.exists():
            raise RuntimeError(result.stderr or "The browser did not produce a PDF")
        payload = rendered.read_bytes()
        if not payload.startswith(b"%PDF-"):
            raise RuntimeError("The browser output is not a PDF")
        staged = output.with_suffix(".pdf.partial")
        staged.write_bytes(payload)
        staged.replace(output)
    print(f"Tutorial PDF: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
