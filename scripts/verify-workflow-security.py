#!/usr/bin/env python3
"""Fail closed when GitHub Actions workflows use mutable actions or risky authority."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
USE_PATTERN = re.compile(r"^\s*uses:\s*([^@\s]+)@([^\s#]+)", re.MULTILINE)
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
FORBIDDEN = (
    "pull_request_target:",
    "permissions: write-all",
    "persist-credentials: true",
)


def main() -> int:
    paths = sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")))
    if not paths:
        raise SystemExit("no GitHub Actions workflows found")
    action_count = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN:
            if marker in text:
                raise SystemExit(f"{path.relative_to(ROOT)} contains forbidden marker: {marker}")
        matches = USE_PATTERN.findall(text)
        action_count += len(matches)
        for action, reference in matches:
            if not SHA_PATTERN.fullmatch(reference):
                raise SystemExit(
                    f"{path.relative_to(ROOT)} uses mutable action reference: {action}@{reference}"
                )
    if action_count == 0:
        raise SystemExit("no external action references found")
    print(f"verified {action_count} immutable action references across {len(paths)} workflow(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
