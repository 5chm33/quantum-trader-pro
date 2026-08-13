from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_literal_process_exit_recovers_without_duplicate_submission(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    worker = project_root / "tests" / "helpers" / "paper_process_worker.py"
    state_root = tmp_path / "literal-process-state"
    environment = os.environ.copy()
    source_path = str(project_root / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path
        if not existing_pythonpath
        else os.pathsep.join((source_path, existing_pythonpath))
    )

    crashed = subprocess.run(
        [sys.executable, str(worker), "crash", str(state_root)],
        check=False,
        env=environment,
        timeout=30,
    )
    assert crashed.returncode != 0
    broker_state = json.loads((state_root / "fake-broker.json").read_text(encoding="utf-8"))
    assert broker_state["submission_count"] == 1

    for _ in range(2):
        subprocess.run(
            [sys.executable, str(worker), "recover", str(state_root)],
            check=True,
            env=environment,
            timeout=30,
        )
        result = json.loads((state_root / "result.json").read_text(encoding="utf-8"))
        assert result == {
            "broker_order_id": "broker-process-1",
            "operator_paused": True,
            "ready": True,
            "submission_count": 1,
            "submission_state": "reconciled",
        }
        broker_state = json.loads((state_root / "fake-broker.json").read_text(encoding="utf-8"))
        assert broker_state["submission_count"] == 1
