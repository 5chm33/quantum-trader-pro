#!/usr/bin/env python3
"""Verify that a one-click demo produced the complete safe artifact set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED_FILES = {
    "events.sqlite3",
    "simulation_report.json",
    "simulation_report.md",
    "equity_curve.csv",
    "fills.csv",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    output = arguments.output.resolve()

    missing = sorted(name for name in REQUIRED_FILES if not (output / name).is_file())
    if missing:
        raise SystemExit(f"missing demo artifacts: {', '.join(missing)}")

    report = json.loads((output / "simulation_report.json").read_text(encoding="utf-8"))
    if report.get("config", {}).get("mode") != "simulation":
        raise SystemExit("demo report is not labeled simulation")
    source = report.get("metrics", {}).get("source", "")
    if not isinstance(source, str) or not source.startswith("csv:") or ":sha256:" not in source:
        raise SystemExit("demo report does not identify checksummed CSV provenance")

    print(f"verified {len(REQUIRED_FILES)} demo artifacts in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
