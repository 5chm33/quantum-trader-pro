#!/usr/bin/env python3
"""Portable one-click bootstrap for the offline Quantum Trader Pro demo."""

from __future__ import annotations

import sys
from pathlib import Path

MINIMUM_PYTHON = (3, 11)


def main() -> int:
    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(str(part) for part in MINIMUM_PYTHON)
        current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        print(
            f"Quantum Trader Pro requires Python {required} or newer; found {current}.",
            file=sys.stderr,
        )
        print(
            "Install a current Python release from https://www.python.org/downloads/",
            file=sys.stderr,
        )
        return 2

    repository_root = Path(__file__).resolve().parent
    source_root = repository_root / "src"
    if not source_root.is_dir():
        print("Unable to find the bundled source directory.", file=sys.stderr)
        return 2

    sys.path.insert(0, str(source_root))
    from quantum_trader.cli import main as cli_main

    return cli_main(["demo", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
