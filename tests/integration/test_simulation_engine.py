from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from quantum_trader.adapters.csv_replay import CsvReplayMarketData
from quantum_trader.adapters.simulated_broker import SimulatedBroker
from quantum_trader.adapters.sqlite_store import SQLiteEventStore
from quantum_trader.application.engine import SimulationEngine
from quantum_trader.application.reporting import calculate_metrics, write_report
from quantum_trader.config import SimulationConfig
from quantum_trader.domain.portfolio import Portfolio
from quantum_trader.domain.risk import RiskLimits, RiskManager
from quantum_trader.domain.strategy import MovingAverageCrossover

pytestmark = pytest.mark.integration


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


def run_simulation(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    data_path = root / "prices.csv"
    write_prices(data_path)
    limits = RiskLimits(
        max_position_fraction=Decimal("0.50"),
        max_order_notional=Decimal("10000"),
        min_cash_reserve_fraction=Decimal("0.05"),
        max_drawdown_fraction=Decimal("0.90"),
        max_realized_loss=Decimal("1000"),
    )
    config = SimulationConfig(
        symbol="TEST",
        initial_cash=Decimal("1000"),
        fast_window=2,
        slow_window=3,
        invested_fraction=Decimal("0.50"),
        slippage_bps=Decimal("0"),
        fee_per_order=Decimal("0"),
        fee_per_share=Decimal("0"),
        risk_limits=limits,
    )
    store = SQLiteEventStore(root / "events.sqlite3")
    result = SimulationEngine(
        config=config,
        market_data=CsvReplayMarketData(data_path, symbol="TEST"),
        strategy=MovingAverageCrossover(
            fast_window=2,
            slow_window=3,
            invested_fraction=Decimal("0.50"),
        ),
        risk_manager=RiskManager(limits),
        portfolio=Portfolio("1000"),
        broker=SimulatedBroker(),
        event_store=store,
    ).run()
    events = list(store.iter_events())
    report_paths = write_report(result, root / "report")
    store.close()
    return result, events, report_paths


def test_end_to_end_replay_is_ordered_reconciled_and_reproducible(tmp_path: Path) -> None:
    first_result, first_events, first_reports = run_simulation(tmp_path / "first")
    second_result, second_events, second_reports = run_simulation(tmp_path / "second")

    assert first_result.event_count == 6
    assert first_result.fill_count == 3
    assert first_result.pending_order_count == 0
    assert first_result.rejected_fill_count == 0
    assert first_result.final_portfolio["positions"] == {}
    assert first_result.risk_halted is False

    assert [event["sequence"] for event in first_events] == list(range(1, len(first_events) + 1))
    assert first_events[0]["event_type"] == "run_started"
    assert first_events[-1]["event_type"] == "run_completed"
    for event in first_events:
        canonical = json.dumps(
            event["payload"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        assert hashlib.sha256(canonical.encode()).hexdigest() == event["payload_sha256"]

    assert first_result.run_id == second_result.run_id
    assert calculate_metrics(first_result) == calculate_metrics(second_result)
    assert [event["event_type"] for event in first_events] == [
        event["event_type"] for event in second_events
    ]
    assert first_reports["json"].read_text(encoding="utf-8") == second_reports["json"].read_text(
        encoding="utf-8"
    )
    assert first_reports["markdown"].read_text(encoding="utf-8") == second_reports[
        "markdown"
    ].read_text(encoding="utf-8")
