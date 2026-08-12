#!/usr/bin/env python3
"""Verify repository-relative Markdown links without making network requests."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
SKIPPED_PREFIXES = ("http://", "https://", "mailto:", "#")


def target_path(document: Path, raw_target: str) -> Path | None:
    target = raw_target.strip()
    if not target or target.startswith(SKIPPED_PREFIXES):
        return None
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split("#", 1)[0]
    if not target:
        return None
    if ' "' in target:
        target = target.split(' "', 1)[0]
    return (document.parent / unquote(target)).resolve()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    for document in sorted(root.rglob("*.md")):
        if any(part.startswith(".") and part != ".github" for part in document.parts):
            continue
        content = document.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(content):
            candidate = target_path(document, match.group(1))
            if candidate is not None and not candidate.exists():
                failures.append(
                    f"{document.relative_to(root)}: missing {candidate.relative_to(root)}"
                )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("All repository-relative Markdown links resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
