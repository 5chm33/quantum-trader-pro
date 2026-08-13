from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantum_trader.cli import decimal_argument, main


def write_prices(path: Path) -> None:
    path.write_text(
        "datetime,open,high,low,close,volume\n"
        "2024-01-01,10,10,10,10,100\n"
        "2024-01-02,11,11,11,11,100\n"
        "2024-01-03,12,12,12,12,100\n"
        "2024-01-04,13,13,13,13,100\n"
        "2024-01-05,9,9,9,9,100\n"
        "2024-01-06,8,8,8,8,100\n",
        encoding="utf-8",
    )


def simulation_arguments(data: Path, output: Path) -> list[str]:
    return [
        "simulate",
        "--data",
        str(data),
        "--output",
        str(output),
        "--symbol",
        "test",
        "--initial-cash",
        "1000",
        "--fast-window",
        "2",
        "--slow-window",
        "3",
        "--invested-fraction",
        "0.50",
        "--slippage-bps",
        "0",
        "--fee-per-order",
        "0",
        "--fee-per-share",
        "0",
        "--max-position-fraction",
        "0.50",
        "--max-order-notional",
        "10000",
        "--min-cash-reserve-fraction",
        "0.05",
        "--max-drawdown-fraction",
        "0.90",
        "--max-realized-loss",
        "1000",
        "--maximum-gap-days",
        "0",
    ]


def test_cli_preflight_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preflight"]) == 0
    preflight = json.loads(capsys.readouterr().out)
    assert preflight["allowed_modes"] == ["simulation"]
    assert preflight["network_required"] is False
    assert preflight["paper_arming_contracts"] is True
    assert preflight["external_broker_contracts"] is True
    assert preflight["paper_secure_credential_files"] is True
    assert preflight["operator_store_default_paused"] is True
    assert preflight["operator_one_use_approvals"] is True
    assert preflight["cancel_owned_orders_kill_switch"] is True
    assert preflight["crash_safe_paper_executor"] is True
    assert preflight["no_blind_retry_recovery"] is True
    assert preflight["deterministic_failure_injection"] is True
    assert preflight["literal_process_crash_acceptance"] is True
    assert preflight["simulated_storage_exhaustion_acceptance"] is True
    assert preflight["service_manager_restart_acceptance"] is False
    assert preflight["flatten_positions_kill_switch"] is False
    assert preflight["operator_paper_commands_available"] is False
    assert preflight["authenticated_paper_acceptance"] is False
    assert preflight["paper_trading_implemented"] is False
    assert preflight["live_execution_available"] is False
    assert preflight["one_click_demo"] is True

    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_cli_demo_runs_from_bundled_data(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "demo"

    assert main(["demo", "--output", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary["status"] == "completed"
    assert summary["mode"] == "simulation"
    assert summary["events"] == 252
    assert summary["risk_halted"] is False
    assert (output / "events.sqlite3").is_file()
    assert (output / "simulation_report.md").is_file()
    assert (output / "simulation_report.json").is_file()
    assert (output / "equity_curve.csv").is_file()
    assert (output / "fills.csv").is_file()


def test_cli_runs_and_protects_existing_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = tmp_path / "prices.csv"
    output = tmp_path / "artifacts"
    write_prices(data)
    arguments = simulation_arguments(data, output)

    assert main(arguments) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "completed"
    assert summary["mode"] == "simulation"
    assert summary["events"] == 6

    assert main(arguments) == 2
    assert "already exist" in capsys.readouterr().err

    assert main([*arguments, "--overwrite"]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == summary["run_id"]


def test_cli_returns_a_safe_error_for_missing_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "simulate",
            "--data",
            str(tmp_path / "missing.csv"),
            "--output",
            str(tmp_path / "output"),
        ]
    )
    assert code == 2
    assert "missing.csv" in capsys.readouterr().err


def test_decimal_argument_rejects_invalid_or_non_finite_values() -> None:
    with pytest.raises(Exception, match="invalid decimal"):
        decimal_argument("not-a-number")
    with pytest.raises(Exception, match="finite"):
        decimal_argument("NaN")
