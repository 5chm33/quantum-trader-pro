#!/usr/bin/env python3
"""Verify the retained pre-holdout research evidence without market data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def row_count(path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def load_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        expected, name = line.split(maxsplit=1)
        checksums[name.strip()] = expected
    if not checksums:
        raise ValueError("checksum manifest is empty")
    return checksums


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "evidence_dir",
        type=Path,
        nargs="?",
        default=Path("evaluation/results/v1-preholdout"),
    )
    arguments = parser.parse_args()
    root = arguments.evidence_dir.expanduser().resolve()
    checksums = load_checksums(root / "SHA256SUMS")
    mismatches = [name for name, expected in checksums.items() if digest(root / name) != expected]
    if mismatches:
        raise ValueError(f"evidence checksum mismatch: {', '.join(sorted(mismatches))}")

    summary: dict[str, Any] = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    protocol = json.loads((root / "protocol_snapshot.json").read_text(encoding="utf-8"))
    source_manifest = json.loads((root / "source_manifest.json").read_text(encoding="utf-8"))
    if summary["protocol_id"] != protocol["protocol_id"]:
        raise ValueError("summary and protocol IDs do not match")
    if source_manifest["protocol_id"] != protocol["protocol_id"]:
        raise ValueError("source manifest and protocol IDs do not match")
    if source_manifest["protocol_sha256"] != summary["protocol_sha256"]:
        raise ValueError("source manifest and summary protocol digests do not match")
    if summary["holdout_status"] != "locked":
        raise ValueError("published v1 evidence must retain a locked holdout")
    if summary["promotion_gate"]["passed"] is not False:
        raise ValueError("published v1 evidence must retain the failed pre-holdout result")

    counts = summary["trial_counts"]
    observed = {
        "validation": row_count(root / "trial_ledger.csv"),
        "test": row_count(root / "test_results.csv"),
        "robustness": row_count(root / "robustness_results.csv"),
        "selections": row_count(root / "fold_selections.csv"),
    }
    for key in ("validation", "test", "robustness"):
        if observed[key] != counts[key]:
            raise ValueError(f"{key} row count does not match summary")
    if observed["selections"] != counts["test"] // len(protocol["cost_scenarios"]):
        raise ValueError("selection row count does not match test scenarios")
    if counts["total"] != counts["validation"] + counts["test"] + counts["robustness"]:
        raise ValueError("summary trial total is inconsistent")

    forbidden = (
        "holdout_receipt.json",
        "holdout_results.csv",
        "holdout_selections.csv",
        "holdout_summary.json",
    )
    leaked = [name for name in forbidden if (root / name).exists()]
    if leaked:
        raise ValueError(f"locked-holdout artifacts must not exist: {', '.join(leaked)}")
    market_data = sorted(root.glob("*_daily.csv")) + sorted(root.glob("*_yahoo_raw.json"))
    if market_data:
        raise ValueError("provider market-data files must not be committed with the evidence")

    print(
        json.dumps(
            {
                "status": "verified",
                "protocol_id": summary["protocol_id"],
                "promotion_gate_passed": False,
                "holdout_status": "locked",
                "trial_counts": counts,
                "fold_selections": observed["selections"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
