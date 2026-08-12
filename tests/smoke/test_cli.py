from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke


def environment() -> dict[str, str]:
    values = os.environ.copy()
    source = Path(__file__).resolve().parents[2] / "src"
    values["PYTHONPATH"] = str(source)
    return values


def test_preflight_proves_simulation_only_boundary() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "quantum_trader.cli", "preflight"],
        check=True,
        capture_output=True,
        text=True,
        env=environment(),
    )
    payload = json.loads(completed.stdout)
    assert payload["allowed_modes"] == ["simulation"]
    assert payload["network_required"] is False
    assert payload["live_trading_implemented"] is False
    assert payload["paper_trading_implemented"] is False


def test_cli_rejects_live_mode_before_initializing_adapters(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "quantum_trader.cli",
            "simulate",
            "--data",
            str(tmp_path / "missing.csv"),
            "--output",
            str(tmp_path / "output"),
            "--mode",
            "live",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment(),
    )
    assert completed.returncode == 2
    assert "invalid choice: 'live'" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_cli_writes_a_complete_local_artifact_set(tmp_path: Path) -> None:
    data = tmp_path / "prices.csv"
    data.write_text(
        "datetime,open,high,low,close,volume\n"
        "2024-01-01,10,10,10,10,100\n"
        "2024-01-02,11,11,11,11,100\n"
        "2024-01-03,12,12,12,12,100\n"
        "2024-01-04,13,13,13,13,100\n"
        "2024-01-05,9,9,9,9,100\n"
        "2024-01-06,8,8,8,8,100\n",
        encoding="utf-8",
    )
    output = tmp_path / "artifacts"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "quantum_trader.cli",
            "simulate",
            "--data",
            str(data),
            "--output",
            str(output),
            "--symbol",
            "TEST",
            "--fast-window",
            "2",
            "--slow-window",
            "3",
            "--max-drawdown-fraction",
            "0.90",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment(),
    )
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "simulation"
    assert payload["events"] == 6
    assert (output / "events.sqlite3").is_file()
    assert (output / "simulation_report.json").is_file()
    assert (output / "simulation_report.md").is_file()
    assert (output / "equity_curve.csv").is_file()
    assert (output / "fills.csv").is_file()
