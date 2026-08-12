from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from quantum_trader.adapters.simulated_broker import SimulatedBroker
from quantum_trader.domain.models import (
    Fill,
    MarketEvent,
    OrderIntent,
    RiskDecision,
    Side,
    stable_id,
)
from quantum_trader.domain.portfolio import Portfolio
from quantum_trader.domain.risk import RiskLimits, RiskManager


def event(*, day: int, price: str) -> MarketEvent:
    value = Decimal(price)
    return MarketEvent(
        timestamp=datetime(2024, 1, day, tzinfo=UTC),
        symbol="TEST",
        open=value,
        high=value,
        low=value,
        close=value,
        volume=1_000,
        source="fixture:test",
    )


def intent(*, side: Side, quantity: int, price: str, day: int = 1) -> OrderIntent:
    return OrderIntent.create(
        correlation_id=f"event-{day}",
        timestamp=datetime(2024, 1, day, tzinfo=UTC),
        symbol="TEST",
        side=side,
        quantity=quantity,
        reference_price=Decimal(price),
        rationale="unit test",
    )


def existing_fill(quantity: int, price: str) -> Fill:
    return Fill(
        fill_id=stable_id("fill", quantity, price),
        order_id=stable_id("order", quantity, price),
        intent_id=stable_id("intent", quantity, price),
        correlation_id="existing",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        symbol="TEST",
        side=Side.BUY,
        quantity=quantity,
        price=Decimal(price),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )


def permissive_limits() -> RiskLimits:
    return RiskLimits(
        max_position_fraction=Decimal("0.50"),
        max_order_notional=Decimal("10000"),
        min_cash_reserve_fraction=Decimal("0.05"),
        max_drawdown_fraction=Decimal("0.10"),
        max_realized_loss=Decimal("1000"),
    )


def test_risk_manager_reduces_buy_to_position_limit_and_rejects_duplicate() -> None:
    portfolio = Portfolio("1000")
    snapshot = portfolio.equity_point(event(day=1, price="100"))
    manager = RiskManager(permissive_limits())
    candidate = intent(side=Side.BUY, quantity=10, price="100")

    decision = manager.evaluate(candidate, portfolio, snapshot)
    assert decision.allowed is True
    assert decision.approved_quantity == 5
    assert decision.reason == "approved_with_quantity_reduction"

    duplicate = manager.evaluate(candidate, portfolio, snapshot)
    assert duplicate.allowed is False
    assert duplicate.reason == "duplicate_intent"


def test_drawdown_halt_blocks_buys_but_permits_full_exit() -> None:
    portfolio = Portfolio("1000")
    portfolio.apply_fill(existing_fill(5, "100"))
    manager = RiskManager(permissive_limits())
    manager.observe(portfolio, portfolio.equity_point(event(day=1, price="100")))
    low_snapshot = portfolio.equity_point(event(day=2, price="70"))
    manager.observe(portfolio, low_snapshot)

    assert manager.halted is True
    assert manager.halt_reason == "maximum_drawdown_exceeded"
    blocked_buy = manager.evaluate(
        intent(side=Side.BUY, quantity=1, price="70", day=2),
        portfolio,
        low_snapshot,
    )
    assert blocked_buy.allowed is False

    exit_decision = manager.evaluate(
        intent(side=Side.SELL, quantity=5, price="70", day=2),
        portfolio,
        low_snapshot,
    )
    assert exit_decision.allowed is True
    assert exit_decision.approved_quantity == 5
    assert exit_decision.reason == "approved_risk_reducing_exit"


def test_simulated_broker_fills_only_on_next_event_with_declared_costs() -> None:
    broker = SimulatedBroker(
        slippage_bps=Decimal("10"),
        fee_per_order=Decimal("1"),
        fee_per_share=Decimal("0.10"),
    )
    candidate = intent(side=Side.BUY, quantity=3, price="100")
    decision = RiskDecision(
        allowed=True,
        reason="approved",
        approved_quantity=3,
        intent_id=candidate.intent_id,
        correlation_id=candidate.correlation_id,
    )
    broker.submit(candidate, decision)

    assert broker.on_market_event(event(day=1, price="100")) == ()
    fills = broker.on_market_event(event(day=2, price="110"))
    assert len(fills) == 1
    assert fills[0].price == Decimal("110.110000")
    assert fills[0].fee == Decimal("1.300000")
    assert fills[0].slippage == Decimal("0.330000")
    assert broker.pending_order_count == 0


def test_risk_sizing_reserves_execution_gap_slippage_and_fees() -> None:
    portfolio = Portfolio("1000")
    snapshot = portfolio.equity_point(event(day=1, price="100"))
    limits = RiskLimits(
        max_position_fraction=Decimal("0.90"),
        max_order_notional=Decimal("1000"),
        min_cash_reserve_fraction=Decimal("0.10"),
        max_drawdown_fraction=Decimal("0.10"),
        max_realized_loss=Decimal("1000"),
    )
    manager = RiskManager(
        limits,
        slippage_bps=Decimal("100"),
        execution_price_buffer_bps=Decimal("900"),
        fee_per_order=Decimal("10"),
        fee_per_share=Decimal("1"),
    )

    decision = manager.evaluate(
        intent(side=Side.BUY, quantity=10, price="100"),
        portfolio,
        snapshot,
    )

    assert decision.allowed is True
    assert decision.approved_quantity == 8
    assert decision.reason == "approved_with_conservative_quantity_reduction"


def test_actual_buy_gap_beyond_declared_bound_halts_new_exposure() -> None:
    portfolio = Portfolio("1000")
    snapshot = portfolio.equity_point(event(day=1, price="100"))
    manager = RiskManager(
        permissive_limits(),
        execution_price_buffer_bps=Decimal("500"),
    )
    candidate = intent(side=Side.BUY, quantity=5, price="100")
    decision = manager.evaluate(candidate, portfolio, snapshot)
    assert decision.approved_quantity == 4
    gap_fill = Fill(
        fill_id="gap-fill",
        order_id="gap-order",
        intent_id=candidate.intent_id,
        correlation_id=candidate.correlation_id,
        timestamp=datetime(2024, 1, 2, tzinfo=UTC),
        symbol="TEST",
        side=Side.BUY,
        quantity=4,
        price=Decimal("140"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )
    portfolio.apply_fill(gap_fill)

    manager.observe_fill(gap_fill, portfolio)

    assert manager.halted is True
    assert manager.halt_reason == "post_fill_position_limit_exceeded"


def test_risk_reducing_sell_never_trips_buy_side_exposure_limit() -> None:
    portfolio = Portfolio("1000")
    portfolio.apply_fill(existing_fill(5, "100"))
    manager = RiskManager(permissive_limits())
    sell_fill = Fill(
        fill_id="sell-fill",
        order_id="sell-order",
        intent_id="sell-intent",
        correlation_id="sell-event",
        timestamp=datetime(2024, 1, 2, tzinfo=UTC),
        symbol="TEST",
        side=Side.SELL,
        quantity=1,
        price=Decimal("1000"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )
    portfolio.apply_fill(sell_fill)

    manager.observe_fill(sell_fill, portfolio)

    assert manager.halted is False


def test_simulated_broker_cancels_every_pending_order_deterministically() -> None:
    broker = SimulatedBroker()
    candidate = intent(side=Side.BUY, quantity=3, price="100")
    decision = RiskDecision(
        allowed=True,
        reason="approved",
        approved_quantity=3,
        intent_id=candidate.intent_id,
        correlation_id=candidate.correlation_id,
    )
    order_id = broker.submit(candidate, decision)

    assert broker.cancel_all() == (order_id,)
    assert broker.pending_order_count == 0
    assert broker.cancel_all() == ()
