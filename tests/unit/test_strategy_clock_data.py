from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantum_trader.adapters.csv_replay import CsvReplayMarketData
from quantum_trader.domain.clock import ReplayClock, WeekdayExchangeClock
from quantum_trader.domain.models import MarketEvent
from quantum_trader.domain.strategy import MovingAverageCrossover


def event(day: int, price: str) -> MarketEvent:
    value = Decimal(price)
    return MarketEvent(
        timestamp=datetime(2024, 1, day, tzinfo=UTC),
        symbol="TEST",
        open=value,
        high=value,
        low=value,
        close=value,
        volume=100,
        source="fixture:test",
    )


def write_csv(path: Path, rows: list[str]) -> None:
    path.write_text(
        "datetime,open,high,low,close,volume\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def test_moving_average_strategy_is_deterministic_and_warms_up() -> None:
    first = MovingAverageCrossover(fast_window=2, slow_window=3, invested_fraction=Decimal("0.8"))
    second = MovingAverageCrossover(fast_window=2, slow_window=3, invested_fraction=Decimal("0.8"))
    events = [event(1, "10"), event(2, "11"), event(3, "12"), event(4, "9")]

    first_signals = [first.on_market_event(item) for item in events]
    second_signals = [second.on_market_event(item) for item in events]
    assert first_signals == second_signals
    assert first_signals[0].target_fraction == 0
    assert first_signals[1].target_fraction == 0
    assert first_signals[2].target_fraction == Decimal("0.8")
    assert first_signals[3].target_fraction == 0


def test_csv_replay_adds_timezone_and_cryptographic_provenance(tmp_path: Path) -> None:
    path = tmp_path / "prices.csv"
    write_csv(path, ["2024-01-01,10,11,9,10,100", "2024-01-02,11,12,10,11,120"])
    provider = CsvReplayMarketData(path, symbol="test")
    observations = list(provider.stream())

    assert len(observations) == 2
    assert observations[0].timestamp.tzinfo is not None
    assert observations[0].symbol == "TEST"
    assert "sha256:" in provider.source_name
    assert observations[0].source == provider.source_name


def test_csv_replay_rejects_out_of_order_or_excessively_gapped_data(tmp_path: Path) -> None:
    out_of_order = tmp_path / "out_of_order.csv"
    write_csv(out_of_order, ["2024-01-02,10,10,10,10,1", "2024-01-01,10,10,10,10,1"])
    with pytest.raises(ValueError, match="strictly increasing"):
        list(CsvReplayMarketData(out_of_order, symbol="TEST").stream())

    gap = tmp_path / "gap.csv"
    write_csv(gap, ["2024-01-01,10,10,10,10,1", "2024-01-10,10,10,10,10,1"])
    with pytest.raises(ValueError, match="exceeds maximum"):
        list(
            CsvReplayMarketData(
                gap,
                symbol="TEST",
                maximum_gap=timedelta(days=7),
            ).stream()
        )


def test_replay_clock_rejects_time_travel() -> None:
    clock = ReplayClock()
    first = datetime(2024, 1, 1, tzinfo=UTC)
    clock.advance(first)
    assert clock.now() == first
    with pytest.raises(ValueError, match="strictly increasing"):
        clock.advance(first)


def test_exchange_clock_uses_new_york_time_explicitly() -> None:
    clock = WeekdayExchangeClock()
    assert clock.is_regular_session(datetime(2024, 1, 8, 15, 0, tzinfo=UTC)) is True
    assert clock.is_regular_session(datetime(2024, 1, 8, 22, 0, tzinfo=UTC)) is False
    assert clock.next_weekday_open(datetime(2024, 1, 5, 22, 0, tzinfo=UTC)) == datetime(
        2024,
        1,
        8,
        14,
        30,
        tzinfo=UTC,
    )


def test_csv_replay_ingests_complete_adjusted_close_benchmark(tmp_path: Path) -> None:
    path = tmp_path / "adjusted.csv"
    path.write_text(
        "datetime,open,high,low,close,adjusted_close,volume\n"
        "2024-01-01,10,11,9,10,8,100\n"
        "2024-01-02,11,12,10,11,9,120\n",
        encoding="utf-8",
    )

    observations = list(CsvReplayMarketData(path, symbol="TEST").stream())

    assert [item.adjusted_close for item in observations] == [Decimal("8"), Decimal("9")]
    assert observations[0].close == Decimal("10")


def test_csv_replay_rejects_missing_adjusted_close_value(tmp_path: Path) -> None:
    path = tmp_path / "incomplete_adjusted.csv"
    path.write_text(
        "datetime,open,high,low,close,adjusted_close,volume\n2024-01-01,10,11,9,10,,100\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid CSV row"):
        list(CsvReplayMarketData(path, symbol="TEST").stream())
