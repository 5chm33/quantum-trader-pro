from __future__ import annotations

import os
from pathlib import Path

import pytest

from quantum_trader.application.lifecycle import SingleInstanceLock


def test_single_instance_lock_blocks_a_second_owner_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "engine.lock"
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)

    with first:
        assert path.read_text(encoding="utf-8") == str(os.getpid())
        with pytest.raises(RuntimeError, match="another simulation process"):
            second.acquire()

    with second:
        assert path.read_text(encoding="utf-8") == str(os.getpid())
