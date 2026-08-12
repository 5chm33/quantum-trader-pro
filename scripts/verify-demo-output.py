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
    "round_trip_trades.csv",
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
    metrics = report.get("metrics", {})
    source = metrics.get("source", "")
    if not isinstance(source, str) or not source.startswith("csv:") or ":sha256:" not in source:
        raise SystemExit("demo report does not identify checksummed CSV provenance")
    if metrics.get("headline_benchmark") != "unavailable_missing_adjusted_close":
        raise SystemExit("bundled demo must disclose that adjusted-close benchmark data is absent")
    if metrics.get("pending_orders_at_end") != 0:
        raise SystemExit("demo left pending orders at end of test")
    if not isinstance(metrics.get("open_position_at_end"), bool):
        raise SystemExit("demo does not disclose whether a position remains open")
    if metrics.get("end_of_test_policy") != "cancel_pending_mark_positions_to_final_close":
        raise SystemExit("demo does not declare the required end-of-test policy")
    if metrics.get("risk_halted") is not False:
        raise SystemExit("demo triggered a risk halt")

    print(f"verified {len(REQUIRED_FILES)} demo artifacts in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
