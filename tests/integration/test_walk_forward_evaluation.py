from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quantum_trader.application.evaluation import run_locked_holdout, run_walk_forward

pytestmark = pytest.mark.integration


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_protocol(path: Path) -> dict[str, object]:
    protocol: dict[str, object] = {
        "protocol_id": "test-walk-forward-v1",
        "protocol_version": "1.0.0",
        "data": {
            "start_date": "2024-01-01",
            "end_date": "2024-02-29",
            "frequency": "1d",
            "adjusted_close_required": True,
            "maximum_gap_days": 2,
            "assets": [{"symbol": "TEST", "role": "synthetic correctness fixture"}],
        },
        "windows": {
            "train_bars": 10,
            "validation_bars": 5,
            "test_bars": 5,
            "step_bars": 5,
            "locked_holdout_bars": 5,
            "minimum_folds_per_asset": 2,
        },
        "candidate_grid": {
            "fast_windows": [2, 3],
            "slow_windows": [4, 5],
            "invested_fractions": ["0.50"],
            "constraint": "fast_window < slow_window",
        },
        "base_configuration": {
            "initial_cash": "1000",
            "slippage_bps": "1",
            "execution_price_buffer_bps": "100",
            "fee_per_order": "0",
            "fee_per_share": "0.001",
            "max_position_fraction": "0.50",
            "max_order_notional": "1000",
            "min_cash_reserve_fraction": "0.05",
            "max_drawdown_fraction": "0.90",
            "max_realized_loss": "1000",
        },
        "cost_scenarios": [
            {"name": "base", "multiplier": "1"},
            {"name": "cost_2x", "multiplier": "2"},
            {"name": "cost_5x", "multiplier": "5"},
        ],
        "selection": {
            "scope": "validation_only",
            "primary_metric": "excess_return_vs_total_return_proxy",
            "eligibility": {
                "risk_halted": False,
                "pending_orders_at_end": 0,
                "maximum_drawdown_floor": -0.90,
            },
            "tie_breakers": [
                "higher_excess_return_vs_total_return_proxy",
                "lower_absolute_maximum_drawdown",
                "lower_annualized_turnover",
                "lexicographically_smaller_candidate_id",
            ],
        },
        "robustness": {
            "start_offset_bars": [0, 1, 2],
            "execution_price_buffer_bps": ["0", "100", "200"],
            "retain_all_attempts": True,
            "allow_post_result_asset_removal": False,
            "allow_post_result_parameter_changes": False,
        },
        "locked_holdout": {
            "run_once_after_protocol_commit": True,
            "selection_source": "aggregate_pre_holdout_validation_results",
            "receipt_required": True,
            "receipt_filename": "holdout_receipt.json",
        },
        "promotion_gates": {
            "all_assets_complete": True,
            "minimum_folds_per_asset": 2,
            "median_base_test_excess_return_minimum": "-1",
            "positive_base_test_excess_share_minimum": "0",
            "median_cost_2x_test_excess_return_minimum": "-1",
            "median_cost_5x_test_excess_return_minimum": "-1",
            "maximum_test_drawdown_floor": "-1",
            "risk_halts_allowed": 100,
            "pending_orders_allowed": 0,
            "negative_cash_allowed": False,
            "locked_holdout_median_excess_return_minimum": "-1",
            "locked_holdout_positive_asset_share_minimum": "0",
        },
    }
    path.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return protocol


def write_panel(root: Path, protocol_path: Path, *, include_adjusted: bool = True) -> None:
    root.mkdir()
    csv_path = root / "test_daily.csv"
    header = (
        "datetime,open,high,low,close,adjusted_close,volume\n"
        if include_adjusted
        else "datetime,open,high,low,close,volume\n"
    )
    rows: list[str] = []
    start = datetime(2024, 1, 1, tzinfo=UTC)
    cycle = ("10", "11", "12", "11", "9", "10", "13", "12")
    for index in range(40):
        price = cycle[index % len(cycle)]
        timestamp = (start + timedelta(days=index)).isoformat()
        if include_adjusted:
            rows.append(f"{timestamp},{price},{price},{price},{price},{price},1000")
        else:
            rows.append(f"{timestamp},{price},{price},{price},{price},1000")
    csv_path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    manifest = {
        "protocol_id": "test-walk-forward-v1",
        "protocol_sha256": digest(protocol_path),
        "assets": [
            {
                "symbol": "TEST",
                "normalized_csv": csv_path.name,
                "csv_sha256": digest(csv_path),
            }
        ],
    }
    (root / "source_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_walk_forward_retains_trials_is_deterministic_and_locks_holdout(tmp_path: Path) -> None:
    protocol_path = tmp_path / "protocol.json"
    write_protocol(protocol_path)
    data_dir = tmp_path / "data"
    write_panel(data_dir, protocol_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_summary = run_walk_forward(
        protocol_path=protocol_path,
        data_dir=data_dir,
        output_dir=first,
    )
    second_summary = run_walk_forward(
        protocol_path=protocol_path,
        data_dir=data_dir,
        output_dir=second,
    )

    assert first_summary == second_summary
    assert first_summary["holdout_status"] == "locked"
    assert first_summary["trial_counts"] == {
        "validation": 16,
        "test": 12,
        "robustness": 14,
        "total": 42,
    }
    assert first_summary["promotion_gate"]["passed"] is True
    for name in (
        "protocol_snapshot.json",
        "trial_ledger.csv",
        "fold_selections.csv",
        "test_results.csv",
        "robustness_results.csv",
        "summary.json",
        "report.md",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()

    with (first / "trial_ledger.csv").open(encoding="utf-8", newline="") as handle:
        trials = list(csv.DictReader(handle))
    assert len(trials) == 16
    assert {row["stage"] for row in trials} == {"validation"}
    assert all(row["headline_benchmark"] == "adjusted_close_total_return_proxy" for row in trials)

    with pytest.raises(ValueError, match="confirmation"):
        run_locked_holdout(
            protocol_path=protocol_path,
            data_dir=data_dir,
            evaluation_dir=first,
            confirmation="wrong",
        )
    holdout = run_locked_holdout(
        protocol_path=protocol_path,
        data_dir=data_dir,
        evaluation_dir=first,
        confirmation="test-walk-forward-v1",
    )
    assert holdout["overall_research_acceptance"] is True
    receipt = json.loads((first / "holdout_receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "completed"
    assert receipt["overall_research_acceptance"] is True
    with pytest.raises(ValueError, match="already been opened"):
        run_locked_holdout(
            protocol_path=protocol_path,
            data_dir=data_dir,
            evaluation_dir=first,
            confirmation="test-walk-forward-v1",
        )


def test_walk_forward_rejects_missing_adjusted_close(tmp_path: Path) -> None:
    protocol_path = tmp_path / "protocol.json"
    write_protocol(protocol_path)
    data_dir = tmp_path / "data"
    write_panel(data_dir, protocol_path, include_adjusted=False)

    with pytest.raises(ValueError, match="adjusted-close"):
        run_walk_forward(
            protocol_path=protocol_path,
            data_dir=data_dir,
            output_dir=tmp_path / "output",
        )
