from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from quantum_trader.adapters.csv_replay import CsvReplayMarketData
from quantum_trader.adapters.simulated_broker import SimulatedBroker
from quantum_trader.adapters.sqlite_store import SQLiteEventStore
from quantum_trader.config import ExecutionMode, ExecutionPolicy, SimulationConfig
from quantum_trader.domain.clock import ReplayClock, SystemClock, WeekdayExchangeClock
from quantum_trader.domain.models import (
    EquityPoint,
    Fill,
    MarketEvent,
    OrderIntent,
    Position,
    RiskDecision,
    Side,
    Signal,
    decimal_value,
)
from quantum_trader.domain.risk import RiskLimits
from quantum_trader.domain.strategy import MovingAverageCrossover


class InvalidTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> None:
        return None


class UnsupportedPayload:
    pass


def event(*, day: int = 1, price: str = "10") -> MarketEvent:
    value = Decimal(price)
    return MarketEvent(
        timestamp=datetime(2024, 1, day, tzinfo=UTC),
        symbol="TEST",
        open=value,
        high=value,
        low=value,
        close=value,
        volume=1,
        source="fixture:test",
    )


def order_intent(*, side: Side = Side.BUY, quantity: int = 1) -> OrderIntent:
    return OrderIntent.create(
        correlation_id="event-test",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        symbol="TEST",
        side=side,
        quantity=quantity,
        reference_price=Decimal("10"),
        rationale="edge test",
    )


def allowed_decision(intent: OrderIntent, quantity: int | None = None) -> RiskDecision:
    return RiskDecision(
        allowed=True,
        reason="approved",
        approved_quantity=intent.quantity if quantity is None else quantity,
        intent_id=intent.intent_id,
        correlation_id=intent.correlation_id,
    )


def write_csv(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"symbol": ""}, "symbol"),
        ({"initial_cash": Decimal("0")}, "initial_cash"),
        ({"fast_window": 1}, "windows"),
        ({"fast_window": 3, "slow_window": 3}, "windows"),
        ({"invested_fraction": Decimal("0")}, "invested_fraction"),
        ({"slippage_bps": Decimal("-1")}, "slippage"),
    ],
)
def test_simulation_config_rejects_invalid_values(
    overrides: dict[str, Any],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        SimulationConfig(**overrides)


def test_execution_policy_rejects_unrepresented_modes() -> None:
    assert ExecutionPolicy.require_simulation(ExecutionMode.SIMULATION) is ExecutionMode.SIMULATION
    with pytest.raises(ValueError, match="permits only"):
        ExecutionPolicy.require_simulation("live")


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_position_fraction": Decimal("0")},
        {"min_cash_reserve_fraction": Decimal("1")},
        {"max_drawdown_fraction": Decimal("-0.1")},
        {"max_order_notional": Decimal("0")},
        {"max_realized_loss": Decimal("0")},
    ],
)
def test_risk_limits_reject_invalid_values(overrides: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError):
        RiskLimits(**overrides)


def test_csv_constructor_and_schema_validation(tmp_path: Path) -> None:
    valid = tmp_path / "valid.csv"
    write_csv(valid, "datetime,open,high,low,close,volume\n2024-01-01,10,10,10,10,1\n")
    with pytest.raises(ValueError, match="symbol"):
        CsvReplayMarketData(valid, symbol="")
    with pytest.raises(ValueError, match="UTC offset"):
        CsvReplayMarketData(valid, symbol="TEST", naive_timezone=InvalidTimezone())
    with pytest.raises(ValueError, match="maximum_gap"):
        CsvReplayMarketData(valid, symbol="TEST", maximum_gap=timedelta(0))

    missing = tmp_path / "missing.csv"
    write_csv(missing, "datetime,close\n2024-01-01,10\n")
    with pytest.raises(ValueError, match="missing required"):
        list(CsvReplayMarketData(missing, symbol="TEST").stream())

    malformed = tmp_path / "malformed.csv"
    write_csv(
        malformed,
        "datetime,open,high,low,close,volume\n2024-01-01,bad,10,10,10,1\n",
    )
    with pytest.raises(ValueError, match="invalid CSV row"):
        list(CsvReplayMarketData(malformed, symbol="TEST").stream())

    empty = tmp_path / "empty.csv"
    write_csv(empty, "datetime,open,high,low,close,volume\n")
    with pytest.raises(ValueError, match="no market events"):
        list(CsvReplayMarketData(empty, symbol="TEST").stream())


def test_csv_preserves_aware_instants_in_utc(tmp_path: Path) -> None:
    path = tmp_path / "aware.csv"
    write_csv(
        path,
        "datetime,open,high,low,close,volume\n2024-01-01T02:00:00+02:00,10,10,10,10,1\n",
    )
    observation = next(iter(CsvReplayMarketData(path, symbol="TEST").stream()))
    assert observation.timestamp == datetime(2024, 1, 1, tzinfo=UTC)


def test_simulated_broker_rejects_invalid_configuration_and_contracts() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        SimulatedBroker(slippage_bps=Decimal("-1"))

    broker = SimulatedBroker()
    candidate = order_intent(quantity=2)
    denied = RiskDecision(
        allowed=False,
        reason="blocked",
        approved_quantity=0,
        intent_id=candidate.intent_id,
        correlation_id=candidate.correlation_id,
    )
    with pytest.raises(ValueError, match="only allowed"):
        broker.submit(candidate, denied)

    mismatch = RiskDecision(
        allowed=True,
        reason="approved",
        approved_quantity=1,
        intent_id="other",
        correlation_id=candidate.correlation_id,
    )
    with pytest.raises(ValueError, match="correspond"):
        broker.submit(candidate, mismatch)
    with pytest.raises(ValueError, match="quantity"):
        broker.submit(candidate, allowed_decision(candidate, 3))

    decision = allowed_decision(candidate)
    broker.submit(candidate, decision)
    with pytest.raises(ValueError, match="duplicate"):
        broker.submit(candidate, decision)


def test_simulated_broker_rejects_non_positive_slippage_fill() -> None:
    broker = SimulatedBroker(slippage_bps=Decimal("20000"))
    candidate = order_intent(side=Side.SELL)
    broker.submit(candidate, allowed_decision(candidate))
    with pytest.raises(ValueError, match="non-positive"):
        broker.on_market_event(event(day=2, price="10"))


def test_sqlite_store_validates_payloads_and_closed_state(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    sequence = store.append(
        event_type="test",
        timestamp=timestamp,
        correlation_id="correlation",
        payload={"decimal": Decimal("1.2"), "timestamp": timestamp},
    )
    assert sequence == 1
    assert next(store.iter_events())["payload"] == {
        "decimal": "1.2",
        "timestamp": str(timestamp),
    }

    with pytest.raises(ValueError, match="timezone-aware"):
        store.append(
            event_type="test",
            timestamp=datetime(2024, 1, 1),
            correlation_id="correlation",
            payload={},
        )
    with pytest.raises(ValueError, match="must not be empty"):
        store.append(
            event_type="",
            timestamp=timestamp,
            correlation_id="correlation",
            payload={},
        )
    with pytest.raises(TypeError, match="unsupported JSON"):
        store.append(
            event_type="test",
            timestamp=timestamp,
            correlation_id="correlation",
            payload={"unsupported": UnsupportedPayload()},
        )

    store.close()
    store.close()
    with pytest.raises(RuntimeError, match="closed"):
        list(store.iter_events())


def test_clock_defensive_paths_and_system_clock() -> None:
    replay = ReplayClock()
    with pytest.raises(RuntimeError, match="has not received"):
        replay.now()
    with pytest.raises(ValueError, match="timezone-aware"):
        replay.advance(datetime(2024, 1, 1))
    assert SystemClock().now().tzinfo is not None

    exchange = WeekdayExchangeClock()
    with pytest.raises(ValueError, match="timezone-aware"):
        exchange.is_regular_session(datetime(2024, 1, 1))
    with pytest.raises(ValueError, match="timezone-aware"):
        exchange.next_weekday_open(datetime(2024, 1, 1))
    before_open = datetime(2024, 1, 8, 13, 0, tzinfo=UTC)
    assert exchange.next_weekday_open(before_open) == datetime(
        2024,
        1,
        8,
        14,
        30,
        tzinfo=UTC,
    )


def test_model_invariants_cover_invalid_signal_order_fill_and_equity() -> None:
    assert decimal_value(Decimal("1")) == Decimal("1")
    assert decimal_value(1.25) == Decimal("1.25")
    with pytest.raises(ValueError, match="target_fraction"):
        Signal(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            symbol="TEST",
            target_fraction=Decimal("1.1"),
            rationale="invalid",
            correlation_id="event",
        )
    with pytest.raises(ValueError, match="rationale"):
        Signal(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            symbol="TEST",
            target_fraction=Decimal("0"),
            rationale="",
            correlation_id="event",
        )
    with pytest.raises(ValueError, match="quantity"):
        order_intent(quantity=0)

    candidate = order_intent()
    with pytest.raises(ValueError, match="positive quantity"):
        RiskDecision(
            allowed=True,
            reason="approved",
            approved_quantity=0,
            intent_id=candidate.intent_id,
            correlation_id=candidate.correlation_id,
        )
    with pytest.raises(ValueError, match="approve zero"):
        RiskDecision(
            allowed=False,
            reason="denied",
            approved_quantity=1,
            intent_id=candidate.intent_id,
            correlation_id=candidate.correlation_id,
        )
    with pytest.raises(ValueError, match="reason"):
        RiskDecision(
            allowed=False,
            reason="",
            approved_quantity=0,
            intent_id=candidate.intent_id,
            correlation_id=candidate.correlation_id,
        )

    with pytest.raises(ValueError, match="fill quantity"):
        Fill(
            fill_id="fill",
            order_id="order",
            intent_id="intent",
            correlation_id="event",
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            symbol="TEST",
            side=Side.BUY,
            quantity=0,
            price=Decimal("10"),
            fee=Decimal("0"),
            slippage=Decimal("0"),
        )
    with pytest.raises(ValueError, match="equity must reconcile"):
        EquityPoint(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            cash=Decimal("10"),
            market_value=Decimal("5"),
            equity=Decimal("20"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            fees=Decimal("0"),
        )
    assert Position(symbol="TEST").as_dict()["quantity"] == 0


def test_strategy_constructor_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError, match="at least two"):
        MovingAverageCrossover(fast_window=1, slow_window=3)
    with pytest.raises(ValueError, match="greater"):
        MovingAverageCrossover(fast_window=3, slow_window=3)
    with pytest.raises(ValueError, match="invested_fraction"):
        MovingAverageCrossover(fast_window=2, slow_window=3, invested_fraction=Decimal("0"))
