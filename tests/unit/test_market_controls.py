from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quantum_trader.domain.brokerage import (
    AccountStatus,
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerClockSnapshot,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerPositionSnapshot,
    TimeInForce,
)
from quantum_trader.domain.execution import (
    PAPER_ACKNOWLEDGEMENT,
    ArmingRecord,
    BrokerPreflight,
    ExecutionFingerprint,
    ExecutionGate,
    ExecutionMode,
)
from quantum_trader.domain.market_controls import (
    AssetTradingSnapshot,
    LatestQuoteSnapshot,
    MarketCalendarDay,
    PaperControlDecision,
    PaperControlLimits,
    PaperPreTradeState,
    evaluate_paper_pretrade,
)
from quantum_trader.domain.models import OrderIntent, RiskDecision, Side

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
EASTERN = ZoneInfo("America/New_York")
ACCOUNT_SHA = "a" * 64
RAW = "b" * 64


def approved_order(
    *,
    side: Side = Side.BUY,
    quantity: int = 10,
    symbol: str = "SPY",
    order_type: BrokerOrderType = BrokerOrderType.LIMIT,
    time_in_force: TimeInForce = TimeInForce.DAY,
    limit_price: Decimal | None = Decimal("101"),
    extended_hours: bool = False,
) -> ApprovedBrokerOrder:
    fingerprint = ExecutionFingerprint("c" * 64, "d" * 64, ACCOUNT_SHA)
    record = ArmingRecord.issue_paper(
        strategy_namespace="qtpro-paper",
        fingerprint=fingerprint,
        issued_at=NOW - timedelta(minutes=5),
        ttl=timedelta(hours=1),
        acknowledgement=PAPER_ACKNOWLEDGEMENT,
    )
    context = ExecutionGate.arm_paper(
        requested_mode=ExecutionMode.PAPER,
        record=record,
        expected_namespace="qtpro-paper",
        expected_fingerprint=fingerprint,
        preflight=BrokerPreflight(
            environment_verified=True,
            account_verified=True,
            account_active=True,
            account_unblocked=True,
            reconciliation_complete=True,
            broker_clock_verified=True,
            market_data_fresh=True,
            durable_journal_ready=True,
            secret_source_secure=True,
        ),
        now=NOW - timedelta(minutes=4),
    )
    intent = OrderIntent.create(
        correlation_id=f"control-{side.value}-{symbol}",
        timestamp=NOW - timedelta(seconds=2),
        symbol=symbol,
        side=side,
        quantity=quantity,
        reference_price=Decimal("100"),
        rationale="control fixture",
    )
    decision = RiskDecision(
        allowed=True,
        reason="within_limits",
        approved_quantity=quantity,
        intent_id=intent.intent_id,
        correlation_id=intent.correlation_id,
    )
    return ApprovedBrokerOrder.from_approved_intent(
        context=context,
        intent=intent,
        decision=decision,
        order_type=order_type,
        time_in_force=time_in_force,
        limit_price=limit_price,
        extended_hours=extended_hours,
    )


def calendar_day(*, trade_date: date = date(2026, 8, 12)) -> MarketCalendarDay:
    return MarketCalendarDay(
        trade_date=trade_date,
        regular_open=datetime.combine(trade_date, time(9, 30), tzinfo=EASTERN),
        regular_close=datetime.combine(trade_date, time(16, 0), tzinfo=EASTERN),
        session_open=datetime.combine(trade_date, time(4, 0), tzinfo=EASTERN),
        session_close=datetime.combine(trade_date, time(20, 0), tzinfo=EASTERN),
        raw_payload_sha256=RAW,
    )


def account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        environment=ExecutionMode.PAPER,
        account_sha256=ACCOUNT_SHA,
        status=AccountStatus.ACTIVE,
        trading_blocked=False,
        account_blocked=False,
        transfers_blocked=False,
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        buying_power=Decimal("10000"),
        captured_at=NOW - timedelta(seconds=1),
        raw_payload_sha256=RAW,
    )


def clock() -> BrokerClockSnapshot:
    return BrokerClockSnapshot(
        is_open=True,
        timestamp=NOW - timedelta(seconds=1),
        next_open=NOW + timedelta(days=1),
        next_close=calendar_day().regular_close,
        raw_payload_sha256=RAW,
    )


def asset(*, symbol: str = "SPY") -> AssetTradingSnapshot:
    return AssetTradingSnapshot(
        symbol=symbol,
        asset_class="us_equity",
        status="active",
        tradable=True,
        fractionable=True,
        marginable=True,
        shortable=True,
        borrow_status="easy_to_borrow",
        captured_at=NOW - timedelta(seconds=1),
        raw_payload_sha256=RAW,
    )


def quote(*, symbol: str = "SPY") -> LatestQuoteSnapshot:
    return LatestQuoteSnapshot(
        symbol=symbol,
        bid_price=Decimal("100.00"),
        bid_size=10,
        ask_price=Decimal("100.02"),
        ask_size=12,
        timestamp=NOW - timedelta(seconds=1),
        feed="iex",
        raw_payload_sha256=RAW,
    )


def position(
    *,
    symbol: str = "SPY",
    quantity: Decimal = Decimal("10"),
    market_value: Decimal = Decimal("1000"),
    captured_at: datetime = NOW - timedelta(seconds=1),
) -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        symbol=symbol,
        quantity=quantity,
        average_entry_price=Decimal("95"),
        market_price=Decimal("100"),
        market_value=market_value,
        unrealized_pnl=Decimal("50"),
        captured_at=captured_at,
        raw_payload_sha256=RAW,
    )


def open_order(
    order: ApprovedBrokerOrder,
    *,
    client_order_id: str | None = None,
    side: Side = Side.BUY,
    quantity: Decimal = Decimal("5"),
    limit_price: Decimal | None = Decimal("100"),
    status: BrokerOrderStatus = BrokerOrderStatus.NEW,
) -> BrokerOrderSnapshot:
    return BrokerOrderSnapshot(
        broker_order_id=f"broker-{len(client_order_id or order.client_order_id)}",
        client_order_id=client_order_id or order.client_order_id,
        status=status,
        symbol=order.symbol,
        side=side,
        quantity=quantity,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        submitted_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(seconds=2),
        raw_payload_sha256=RAW,
        limit_price=limit_price,
    )


def state(**changes: object) -> PaperPreTradeState:
    base: dict[str, object] = {
        "now": NOW,
        "strategy_namespace": "qtpro-paper",
        "account": account(),
        "clock": clock(),
        "calendar": calendar_day(),
        "asset": asset(),
        "quote": quote(),
        "order": approved_order(),
        "positions": (),
        "open_orders": (),
        "submission_timestamps": (),
        "reconciliation_ready": True,
        "reconciliation_timestamp": NOW - timedelta(seconds=1),
    }
    base.update(changes)
    return PaperPreTradeState(**base)  # type: ignore[arg-type]


def test_fresh_regular_session_limit_buy_is_allowed() -> None:
    decision = evaluate_paper_pretrade(state(), PaperControlLimits())
    assert decision.allowed is True
    assert decision.reasons == ()
    assert decision.candidate_notional == Decimal("1010")
    assert decision.projected_cash == Decimal("8990")
    assert decision.as_dict()["allowed"] is True


def test_stale_future_closed_and_unreconciled_state_fails_closed() -> None:
    prior_day = calendar_day(trade_date=date(2026, 8, 11))
    stale_account = replace(account(), captured_at=NOW - timedelta(seconds=6))
    closed_clock = replace(
        clock(),
        is_open=False,
        timestamp=NOW - timedelta(seconds=6),
        next_open=NOW + timedelta(hours=23),
        next_close=NOW + timedelta(days=1, hours=6),
    )
    stale_asset = replace(asset(), captured_at=NOW - timedelta(minutes=6))
    future_quote = replace(quote(), timestamp=NOW + timedelta(seconds=2))
    stale_position = position(captured_at=NOW - timedelta(seconds=6))
    decision = evaluate_paper_pretrade(
        state(
            reconciliation_ready=False,
            reconciliation_timestamp=NOW - timedelta(seconds=6),
            account=stale_account,
            clock=closed_clock,
            calendar=prior_day,
            asset=stale_asset,
            quote=future_quote,
            positions=(stale_position,),
        ),
        PaperControlLimits(),
    )
    assert decision.allowed is False
    assert {
        "reconciliation_not_ready",
        "reconciliation_stale",
        "account_stale",
        "clock_stale",
        "broker_clock_closed",
        "calendar_date_mismatch",
        "clock_calendar_close_mismatch",
        "outside_regular_session",
        "asset_stale",
        "quote_from_future",
        "position_stale",
    } <= set(decision.reasons)


def test_asset_quote_and_order_policy_violations_are_explicit() -> None:
    market_order = approved_order(
        order_type=BrokerOrderType.MARKET,
        time_in_force=TimeInForce.GOOD_TIL_CANCELED,
        limit_price=None,
    )
    ineligible_asset = replace(asset(symbol="QQQ"), status="inactive", tradable=False)
    wide_quote = replace(
        quote(symbol="QQQ"),
        bid_price=Decimal("90"),
        ask_price=Decimal("110"),
    )
    decision = evaluate_paper_pretrade(
        state(order=market_order, asset=ineligible_asset, quote=wide_quote),
        PaperControlLimits(),
    )
    assert {
        "symbol_snapshot_mismatch",
        "asset_not_eligible",
        "spread_too_wide",
        "limit_order_required",
        "day_time_in_force_required",
    } <= set(decision.reasons)

    extended = approved_order(extended_hours=True)
    extended_decision = evaluate_paper_pretrade(
        state(order=extended),
        PaperControlLimits(),
    )
    assert "extended_hours_disabled" in extended_decision.reasons


def test_portfolio_open_order_and_rate_limits_reserve_all_commitments() -> None:
    order = approved_order()
    owned = open_order(order)
    foreign = open_order(order, client_order_id="manual:desk.order-1")
    unknown_price = open_order(order, limit_price=None)
    constrained_account = replace(
        account(),
        cash=Decimal("1500"),
        buying_power=Decimal("500"),
    )
    large_position = position(quantity=Decimal("45"), market_value=Decimal("4500"))
    submissions = (
        *(NOW - timedelta(seconds=30) for _ in range(25)),
        NOW + timedelta(seconds=2),
    )
    limits = replace(
        PaperControlLimits(),
        max_open_owned_orders=1,
        max_order_notional=Decimal("1000"),
    )
    decision = evaluate_paper_pretrade(
        state(
            account=constrained_account,
            positions=(large_position,),
            open_orders=(owned, foreign, unknown_price),
            submission_timestamps=submissions,
        ),
        limits,
    )
    assert decision.committed_open_buy_notional == Decimal("500")
    assert decision.recent_order_count == 25
    assert decision.session_order_count == 26
    assert {
        "order_notional_limit",
        "foreign_open_order",
        "open_order_price_unknown",
        "open_order_limit",
        "buying_power_limit",
        "gross_exposure_limit",
        "symbol_exposure_limit",
        "cash_reserve_limit",
        "submission_timestamp_from_future",
        "order_rate_limit",
        "session_order_limit",
    } <= set(decision.reasons)


def test_risk_reducing_sell_is_allowed_but_oversell_is_rejected() -> None:
    sell = approved_order(
        side=Side.SELL,
        quantity=5,
        limit_price=Decimal("99"),
    )
    allowed = evaluate_paper_pretrade(
        state(order=sell, positions=(position(),)),
        PaperControlLimits(),
    )
    assert allowed.allowed is True
    assert allowed.projected_symbol_exposure == Decimal("505")
    assert allowed.projected_cash == Decimal("10000")

    oversell = approved_order(
        side=Side.SELL,
        quantity=11,
        limit_price=Decimal("99"),
    )
    denied = evaluate_paper_pretrade(
        state(order=oversell, positions=(position(),)),
        PaperControlLimits(),
    )
    assert "sell_exceeds_position" in denied.reasons


def test_snapshot_and_limit_models_reject_unsafe_values() -> None:
    with pytest.raises(ValueError, match="crossed"):
        replace(quote(), bid_price=Decimal("101"), ask_price=Decimal("100"))
    with pytest.raises(ValueError, match="sizes"):
        replace(quote(), bid_size=0)
    with pytest.raises(ValueError, match="session times"):
        replace(calendar_day(), session_open=calendar_day().regular_close)
    with pytest.raises(ValueError, match="max_orders_per_window"):
        replace(PaperControlLimits(), max_orders_per_window=31)
    with pytest.raises(ValueError, match="symbol exposure"):
        replace(
            PaperControlLimits(),
            max_symbol_exposure_fraction=Decimal("0.8"),
        )
    with pytest.raises(ValueError, match="exactly reflect"):
        PaperControlDecision(
            allowed=True,
            reasons=("failure",),
            candidate_notional=Decimal("1"),
            committed_open_buy_notional=Decimal("0"),
            projected_gross_exposure=Decimal("1"),
            projected_symbol_exposure=Decimal("1"),
            projected_cash=Decimal("1"),
            recent_order_count=0,
            session_order_count=0,
        )


def test_control_snapshot_validation_edges_are_fail_closed() -> None:
    with pytest.raises(ValueError, match="uppercase"):
        replace(asset(), symbol="spy")
    with pytest.raises(ValueError, match="class and status"):
        replace(asset(), asset_class="")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(asset(), captured_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="SHA-256"):
        replace(asset(), raw_payload_sha256="bad")

    with pytest.raises(ValueError, match="uppercase"):
        replace(quote(), symbol="spy")
    with pytest.raises(ValueError, match="positive"):
        replace(quote(), bid_price=Decimal("0"))
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(quote(), timestamp=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="feed"):
        replace(quote(), feed="")
    with pytest.raises(ValueError, match="SHA-256"):
        replace(quote(), raw_payload_sha256="bad")

    with pytest.raises(ValueError, match="timezone-aware"):
        replace(calendar_day(), regular_open=calendar_day().regular_open.replace(tzinfo=None))
    with pytest.raises(ValueError, match="trade date"):
        replace(calendar_day(), trade_date=date(2026, 8, 13))
    with pytest.raises(ValueError, match="SHA-256"):
        replace(calendar_day(), raw_payload_sha256="bad")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"max_quote_age": timedelta(0)}, "max_quote_age"),
        ({"max_spread_bps": Decimal("0")}, "max_spread_bps"),
        ({"max_orders_per_session": 101}, "max_orders_per_session"),
        ({"max_open_owned_orders": 21}, "max_open_owned_orders"),
        ({"max_order_notional": Decimal("0")}, "max_order_notional"),
        ({"max_gross_exposure_fraction": Decimal("1.1")}, "finite and in"),
        ({"minimum_cash_reserve": Decimal("-1")}, "minimum_cash_reserve"),
    ],
)
def test_control_limit_configuration_edges_are_rejected(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(PaperControlLimits(), **changes)


def test_pretrade_state_and_decision_validation_edges_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(state(), now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="reconciliation_timestamp"):
        replace(state(), reconciliation_timestamp=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="strategy_namespace"):
        replace(state(), strategy_namespace="")
    with pytest.raises(ValueError, match="submission timestamp"):
        replace(state(), submission_timestamps=(NOW.replace(tzinfo=None),))
    with pytest.raises(ValueError, match="candidate_notional"):
        PaperControlDecision(
            allowed=False,
            reasons=("failure",),
            candidate_notional=Decimal("-1"),
            committed_open_buy_notional=Decimal("0"),
            projected_gross_exposure=Decimal("0"),
            projected_symbol_exposure=Decimal("0"),
            projected_cash=Decimal("0"),
            recent_order_count=0,
            session_order_count=0,
        )
    with pytest.raises(ValueError, match="order counts"):
        PaperControlDecision(
            allowed=False,
            reasons=("failure",),
            candidate_notional=Decimal("0"),
            committed_open_buy_notional=Decimal("0"),
            projected_gross_exposure=Decimal("0"),
            projected_symbol_exposure=Decimal("0"),
            projected_cash=Decimal("0"),
            recent_order_count=-1,
            session_order_count=0,
        )


def test_identity_future_state_and_short_position_fail_closed_together() -> None:
    wrong_order = replace(
        approved_order(),
        account_sha256="f" * 64,
        strategy_namespace="other-paper",
    )
    blocked_account = replace(
        account(),
        trading_blocked=True,
        captured_at=NOW + timedelta(seconds=2),
    )
    future_clock = replace(clock(), timestamp=NOW + timedelta(seconds=2))
    future_asset = replace(asset(), captured_at=NOW + timedelta(seconds=2))
    stale_quote = replace(quote(), timestamp=NOW - timedelta(seconds=6))
    short_position = position(
        quantity=Decimal("-1"),
        market_value=Decimal("-100"),
        captured_at=NOW + timedelta(seconds=2),
    )
    terminal = open_order(wrong_order, status=BrokerOrderStatus.CANCELED)
    owned_sell = open_order(wrong_order, side=Side.SELL)
    decision = evaluate_paper_pretrade(
        state(
            account=blocked_account,
            clock=future_clock,
            asset=future_asset,
            quote=stale_quote,
            order=wrong_order,
            positions=(short_position,),
            open_orders=(terminal, owned_sell),
            reconciliation_timestamp=NOW + timedelta(seconds=2),
        ),
        PaperControlLimits(),
    )
    assert {
        "reconciliation_from_future",
        "account_not_permitted",
        "account_from_future",
        "account_fingerprint_mismatch",
        "strategy_namespace_mismatch",
        "clock_from_future",
        "asset_from_future",
        "quote_stale",
        "position_from_future",
        "short_or_negative_position",
    } <= set(decision.reasons)
