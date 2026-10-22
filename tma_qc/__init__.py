"""Compatibility for the original standalone scripts; implementation lives in src."""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent.parent / "src" / "tma_qc")]
__version__ = "0.4.1"
