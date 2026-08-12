from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quantum_trader.domain.models import (
    Fill,
    MarketEvent,
    Side,
    Signal,
    stable_id,
)
from quantum_trader.domain.portfolio import Portfolio


def market_event(price: str = "110", *, day: int = 2) -> MarketEvent:
    close = Decimal(price)
    return MarketEvent(
        timestamp=datetime(2024, 1, day, tzinfo=UTC),
        symbol="TEST",
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=1_000,
        source="fixture:test",
    )


def fill(*, side: Side, quantity: int, price: str, fee: str = "1") -> Fill:
    return Fill(
        fill_id=stable_id("fill", side.value, quantity, price),
        order_id=stable_id("order", side.value, quantity, price),
        intent_id=stable_id("intent", side.value, quantity, price),
        correlation_id="event-test",
        timestamp=datetime(2024, 1, 2, tzinfo=UTC),
        symbol="TEST",
        side=side,
        quantity=quantity,
        price=Decimal(price),
        fee=Decimal(fee),
        slippage=Decimal("0"),
    )


def test_market_event_requires_timezone_and_consistent_ohlc() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketEvent(
            timestamp=datetime(2024, 1, 1),
            symbol="TEST",
            open=Decimal("10"),
            high=Decimal("11"),
            low=Decimal("9"),
            close=Decimal("10"),
            volume=1,
            source="fixture",
        )
    with pytest.raises(ValueError, match="high price"):
        MarketEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            symbol="TEST",
            open=Decimal("10"),
            high=Decimal("9"),
            low=Decimal("8"),
            close=Decimal("10"),
            volume=1,
            source="fixture",
        )


def test_stable_identifier_is_reproducible_and_input_sensitive() -> None:
    assert stable_id("event", "a", 1) == stable_id("event", "a", 1)
    assert stable_id("event", "a", 1) != stable_id("event", "a", 2)


def test_portfolio_reconciles_buy_sell_cash_and_pnl() -> None:
    portfolio = Portfolio("1000")
    portfolio.apply_fill(fill(side=Side.BUY, quantity=2, price="100"))
    assert portfolio.cash == Decimal("799")
    assert portfolio.position_quantity("TEST") == 2

    portfolio.apply_fill(fill(side=Side.SELL, quantity=1, price="120"))
    assert portfolio.cash == Decimal("918")
    assert portfolio.realized_pnl == Decimal("20")
    assert portfolio.total_fees == Decimal("2")

    snapshot = portfolio.equity_point(market_event("110"))
    assert snapshot.market_value == Decimal("110")
    assert snapshot.unrealized_pnl == Decimal("10")
    assert snapshot.equity == Decimal("1028")


def test_portfolio_rejects_negative_cash_and_overselling() -> None:
    portfolio = Portfolio("100")
    with pytest.raises(ValueError, match="negative cash"):
        portfolio.apply_fill(fill(side=Side.BUY, quantity=2, price="100", fee="0"))
    with pytest.raises(ValueError, match="exceeds"):
        portfolio.apply_fill(fill(side=Side.SELL, quantity=1, price="100", fee="0"))


def test_target_fraction_becomes_a_long_only_share_delta() -> None:
    portfolio = Portfolio("1000")
    event = market_event("100")
    signal = Signal(
        timestamp=event.timestamp,
        symbol=event.symbol,
        target_fraction=Decimal("0.50"),
        rationale="test target",
        correlation_id=event.correlation_id,
    )
    intent = portfolio.order_for_target(signal, event)
    assert intent is not None
    assert intent.side is Side.BUY
    assert intent.quantity == 5

    portfolio.apply_fill(fill(side=Side.BUY, quantity=5, price="100", fee="0"))
    assert portfolio.order_for_target(signal, event) is None

    exit_signal = Signal(
        timestamp=event.timestamp,
        symbol=event.symbol,
        target_fraction=Decimal("0"),
        rationale="exit",
        correlation_id=event.correlation_id,
    )
    exit_intent = portfolio.order_for_target(exit_signal, event)
    assert exit_intent is not None
    assert exit_intent.side is Side.SELL
    assert exit_intent.quantity == 5
