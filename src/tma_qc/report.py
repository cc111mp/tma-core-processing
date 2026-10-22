"""Shared pieces for every QC tool: result levels, settings, CSV and text output."""

from __future__ import annotations

import csv
from pathlib import Path

# OK     nothing to do
# CHECK  probably fine, a person should look
# ALERT  something is wrong
OK, CHECK, ALERT = "OK", "CHECK", "ALERT"
RANK = {OK: 0, CHECK: 1, ALERT: 2}


def worst(levels) -> str:
    return max(levels, key=RANK.get, default=OK)


def load_settings(path: str | Path) -> dict:
    import tomllib

    with open(path, "rb") as f:
        return tomllib.load(f)


def alert_lines(alerts: list[dict]) -> list[str]:
    """Text lines listing ALERT then CHECK rows (each row: level, slide, core, message)."""
    if not alerts:
        return ["No problems found."]
    lines = []
    for level in (ALERT, CHECK):
        chosen = [a for a in alerts if a["level"] == level]
        if chosen:
            lines.append(f"{level} ({len(chosen)})")
            lines += [f"  {a['slide']} {a['core']}".rstrip() + f": {a['message']}" for a in chosen]
            lines.append("")
    return lines


def write_csv(path: Path, rows: list[dict], header: list[str] | None = None) -> None:
    """Write a CSV that opens directly in Excel (utf-8 with BOM)."""
    header = header or (list(rows[0]) if rows else [])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
