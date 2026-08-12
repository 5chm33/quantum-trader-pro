from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_portable_bootstrap_runs_bundled_demo(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    output = tmp_path / "one-click-output"

    completed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "launch_demo.py"),
            "--output",
            str(output),
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["status"] == "completed"
    assert summary["mode"] == "simulation"
    assert summary["events"] == 252
    assert summary["risk_halted"] is False
    assert (output / "events.sqlite3").is_file()
    assert (output / "simulation_report.md").is_file()


def test_launchers_are_simulation_only() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    launcher_paths = [
        repository_root / "launch_demo.py",
        repository_root / "launch_demo.sh",
        repository_root / "launch_demo.cmd",
    ]

    for path in launcher_paths:
        content = path.read_text(encoding="utf-8").lower()
        assert "launch_demo" in path.name
        assert "--mode live" not in content
        assert "--mode paper" not in content
        assert "paper-api.alpaca.markets" not in content
        assert "api.alpaca.markets" not in content
        assert "secret" not in content
