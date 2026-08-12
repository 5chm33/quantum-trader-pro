"""Fail-closed market-session, quote, portfolio, and order-rate controls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from quantum_trader.domain.brokerage import (
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerClockSnapshot,
    BrokerOrderSnapshot,
    BrokerOrderType,
    BrokerPositionSnapshot,
    TimeInForce,
    is_owned_client_order_id,
)
from quantum_trader.domain.models import Side

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _positive(value: Decimal, field_name: str) -> None:
    if value <= 0 or not value.is_finite():
        raise ValueError(f"{field_name} must be finite and positive")


def _nonnegative(value: Decimal, field_name: str) -> None:
    if value < 0 or not value.is_finite():
        raise ValueError(f"{field_name} must be finite and nonnegative")


def _sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class AssetTradingSnapshot:
    symbol: str
    asset_class: str
    status: str
    tradable: bool
    fractionable: bool
    marginable: bool
    shortable: bool
    borrow_status: str | None
    captured_at: datetime
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if not self.symbol.strip() or self.symbol != self.symbol.upper():
            raise ValueError("asset symbol must be uppercase and non-empty")
        if not self.asset_class.strip() or not self.status.strip():
            raise ValueError("asset class and status must not be empty")
        _aware(self.captured_at, "captured_at")
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")

    @property
    def permits_long_equity_order(self) -> bool:
        return self.asset_class == "us_equity" and self.status == "active" and self.tradable


@dataclass(frozen=True, slots=True)
class LatestQuoteSnapshot:
    symbol: str
    bid_price: Decimal
    bid_size: int
    ask_price: Decimal
    ask_size: int
    timestamp: datetime
    feed: str
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if not self.symbol.strip() or self.symbol != self.symbol.upper():
            raise ValueError("quote symbol must be uppercase and non-empty")
        _positive(self.bid_price, "bid_price")
        _positive(self.ask_price, "ask_price")
        if self.bid_price > self.ask_price:
            raise ValueError("quote must not be crossed")
        if self.bid_size <= 0 or self.ask_size <= 0:
            raise ValueError("quote sizes must be positive")
        _aware(self.timestamp, "timestamp")
        if not self.feed.strip():
            raise ValueError("quote feed must not be empty")
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")

    @property
    def midpoint(self) -> Decimal:
        return (self.bid_price + self.ask_price) / Decimal("2")

    @property
    def spread_bps(self) -> Decimal:
        return (self.ask_price - self.bid_price) / self.midpoint * TEN_THOUSAND


@dataclass(frozen=True, slots=True)
class MarketCalendarDay:
    trade_date: date
    regular_open: datetime
    regular_close: datetime
    session_open: datetime
    session_close: datetime
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        for name, value in (
            ("regular_open", self.regular_open),
            ("regular_close", self.regular_close),
            ("session_open", self.session_open),
            ("session_close", self.session_close),
        ):
            _aware(value, name)
        if not self.session_open <= self.regular_open < self.regular_close <= self.session_close:
            raise ValueError("calendar session times are inconsistent")
        if self.regular_open.date() != self.trade_date:
            raise ValueError("regular open must occur on the trade date")
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")


@dataclass(frozen=True, slots=True)
class PaperControlLimits:
    max_quote_age: timedelta = timedelta(seconds=5)
    max_quote_future_skew: timedelta = timedelta(seconds=1)
    max_clock_age: timedelta = timedelta(seconds=5)
    max_account_age: timedelta = timedelta(seconds=5)
    max_position_age: timedelta = timedelta(seconds=5)
    max_asset_age: timedelta = timedelta(minutes=5)
    max_reconciliation_age: timedelta = timedelta(seconds=5)
    max_spread_bps: Decimal = Decimal("50")
    max_orders_per_window: int = 5
    order_window: timedelta = timedelta(seconds=60)
    max_orders_per_session: int = 25
    max_open_owned_orders: int = 5
    max_order_notional: Decimal = Decimal("10000")
    max_gross_exposure_fraction: Decimal = Decimal("0.50")
    max_symbol_exposure_fraction: Decimal = Decimal("0.20")
    minimum_cash_reserve: Decimal = Decimal("1000")
    minimum_cash_reserve_fraction: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        for name, duration in (
            ("max_quote_age", self.max_quote_age),
            ("max_quote_future_skew", self.max_quote_future_skew),
            ("max_clock_age", self.max_clock_age),
            ("max_account_age", self.max_account_age),
            ("max_position_age", self.max_position_age),
            ("max_asset_age", self.max_asset_age),
            ("max_reconciliation_age", self.max_reconciliation_age),
            ("order_window", self.order_window),
        ):
            if duration <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        _positive(self.max_spread_bps, "max_spread_bps")
        if not 1 <= self.max_orders_per_window <= 30:
            raise ValueError("max_orders_per_window must be in [1, 30]")
        if not 1 <= self.max_orders_per_session <= 100:
            raise ValueError("max_orders_per_session must be in [1, 100]")
        if not 1 <= self.max_open_owned_orders <= 20:
            raise ValueError("max_open_owned_orders must be in [1, 20]")
        _positive(self.max_order_notional, "max_order_notional")
        for name, fraction in (
            ("max_gross_exposure_fraction", self.max_gross_exposure_fraction),
            ("max_symbol_exposure_fraction", self.max_symbol_exposure_fraction),
            ("minimum_cash_reserve_fraction", self.minimum_cash_reserve_fraction),
        ):
            if not ZERO <= fraction <= Decimal("1") or not fraction.is_finite():
                raise ValueError(f"{name} must be finite and in [0, 1]")
        if self.max_symbol_exposure_fraction > self.max_gross_exposure_fraction:
            raise ValueError("symbol exposure must not exceed gross exposure")
        _nonnegative(self.minimum_cash_reserve, "minimum_cash_reserve")


@dataclass(frozen=True, slots=True)
class PaperPreTradeState:
    now: datetime
    strategy_namespace: str
    account: BrokerAccountSnapshot
    clock: BrokerClockSnapshot
    calendar: MarketCalendarDay
    asset: AssetTradingSnapshot
    quote: LatestQuoteSnapshot
    order: ApprovedBrokerOrder
    positions: Sequence[BrokerPositionSnapshot]
    open_orders: Sequence[BrokerOrderSnapshot]
    submission_timestamps: Sequence[datetime]
    reconciliation_ready: bool
    reconciliation_timestamp: datetime

    def __post_init__(self) -> None:
        _aware(self.now, "now")
        _aware(self.reconciliation_timestamp, "reconciliation_timestamp")
        if not self.strategy_namespace.strip():
            raise ValueError("strategy_namespace must not be empty")
        for timestamp in self.submission_timestamps:
            _aware(timestamp, "submission timestamp")


@dataclass(frozen=True, slots=True)
class PaperControlDecision:
    allowed: bool
    reasons: tuple[str, ...]
    candidate_notional: Decimal
    committed_open_buy_notional: Decimal
    projected_gross_exposure: Decimal
    projected_symbol_exposure: Decimal
    projected_cash: Decimal
    recent_order_count: int
    session_order_count: int

    def __post_init__(self) -> None:
        if self.allowed != (not self.reasons):
            raise ValueError("allowed must exactly reflect an empty reason set")
        for name, value in (
            ("candidate_notional", self.candidate_notional),
            ("committed_open_buy_notional", self.committed_open_buy_notional),
            ("projected_gross_exposure", self.projected_gross_exposure),
            ("projected_symbol_exposure", self.projected_symbol_exposure),
        ):
            _nonnegative(value, name)
        if not self.projected_cash.is_finite():
            raise ValueError("projected_cash must be finite")
        if self.recent_order_count < 0 or self.session_order_count < 0:
            raise ValueError("order counts must not be negative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "candidate_notional": str(self.candidate_notional),
            "committed_open_buy_notional": str(self.committed_open_buy_notional),
            "projected_gross_exposure": str(self.projected_gross_exposure),
            "projected_symbol_exposure": str(self.projected_symbol_exposure),
            "projected_cash": str(self.projected_cash),
            "recent_order_count": self.recent_order_count,
            "session_order_count": self.session_order_count,
        }


def evaluate_paper_pretrade(
    state: PaperPreTradeState,
    limits: PaperControlLimits,
) -> PaperControlDecision:
    """Evaluate every external-order control without performing a side effect."""

    reasons: list[str] = []
    now = state.now
    order = state.order
    quote = state.quote
    account = state.account

    if not state.reconciliation_ready:
        reasons.append("reconciliation_not_ready")
    reconciliation_age = now - state.reconciliation_timestamp
    if reconciliation_age > limits.max_reconciliation_age:
        reasons.append("reconciliation_stale")
    if reconciliation_age < -limits.max_quote_future_skew:
        reasons.append("reconciliation_from_future")
    if not account.permits_new_exposure and order.side is Side.BUY:
        reasons.append("account_not_permitted")
    account_age = now - account.captured_at
    if account_age > limits.max_account_age:
        reasons.append("account_stale")
    if account_age < -limits.max_quote_future_skew:
        reasons.append("account_from_future")
    if account.account_sha256 != order.account_sha256:
        reasons.append("account_fingerprint_mismatch")
    if order.strategy_namespace != state.strategy_namespace:
        reasons.append("strategy_namespace_mismatch")

    if state.clock.timestamp > now + limits.max_quote_future_skew:
        reasons.append("clock_from_future")
    elif now - state.clock.timestamp > limits.max_clock_age:
        reasons.append("clock_stale")
    if not state.clock.is_open:
        reasons.append("broker_clock_closed")
    local_now_date = now.astimezone(state.calendar.regular_open.tzinfo).date()
    if state.calendar.trade_date != local_now_date:
        reasons.append("calendar_date_mismatch")
    if abs(state.clock.next_close - state.calendar.regular_close) > timedelta(minutes=1):
        reasons.append("clock_calendar_close_mismatch")
    if not state.calendar.regular_open <= now < state.calendar.regular_close:
        reasons.append("outside_regular_session")

    if state.asset.symbol != order.symbol.upper() or quote.symbol != order.symbol.upper():
        reasons.append("symbol_snapshot_mismatch")
    if not state.asset.permits_long_equity_order:
        reasons.append("asset_not_eligible")
    asset_age = now - state.asset.captured_at
    if asset_age > limits.max_asset_age:
        reasons.append("asset_stale")
    if asset_age < -limits.max_quote_future_skew:
        reasons.append("asset_from_future")
    quote_age = now - quote.timestamp
    if quote_age > limits.max_quote_age:
        reasons.append("quote_stale")
    if quote_age < -limits.max_quote_future_skew:
        reasons.append("quote_from_future")
    if quote.spread_bps > limits.max_spread_bps:
        reasons.append("spread_too_wide")

    if order.order_type is not BrokerOrderType.LIMIT or order.limit_price is None:
        reasons.append("limit_order_required")
    if order.time_in_force is not TimeInForce.DAY:
        reasons.append("day_time_in_force_required")
    if order.extended_hours:
        reasons.append("extended_hours_disabled")

    candidate_price = quote.ask_price if order.side is Side.BUY else quote.bid_price
    if order.limit_price is not None:
        if order.side is Side.BUY:
            candidate_price = max(candidate_price, order.limit_price)
        else:
            candidate_price = min(candidate_price, order.limit_price)
    candidate_notional = candidate_price * Decimal(order.quantity)
    if candidate_notional > limits.max_order_notional:
        reasons.append("order_notional_limit")

    committed_open_buy_notional, owned_open_count = _open_order_commitment(
        state.open_orders,
        state.strategy_namespace,
        reasons,
    )
    if owned_open_count >= limits.max_open_owned_orders:
        reasons.append("open_order_limit")
    if (
        order.side is Side.BUY
        and committed_open_buy_notional + candidate_notional > account.buying_power
    ):
        reasons.append("buying_power_limit")

    if any(now - position.captured_at > limits.max_position_age for position in state.positions):
        reasons.append("position_stale")
    if any(
        now - position.captured_at < -limits.max_quote_future_skew for position in state.positions
    ):
        reasons.append("position_from_future")
    gross_positions, symbol_positions, symbol_quantities = _position_exposure(
        state.positions,
        reasons,
    )
    if order.side is Side.SELL and Decimal(order.quantity) > symbol_quantities.get(
        order.symbol.upper(), ZERO
    ):
        reasons.append("sell_exceeds_position")
    order_delta = candidate_notional if order.side is Side.BUY else -candidate_notional
    projected_gross = max(ZERO, gross_positions + committed_open_buy_notional + order_delta)
    projected_symbol = max(
        ZERO,
        symbol_positions.get(order.symbol.upper(), ZERO) + order_delta,
    )
    projected_cash = (
        account.cash
        - committed_open_buy_notional
        - (candidate_notional if order.side is Side.BUY else ZERO)
    )
    if projected_gross > account.equity * limits.max_gross_exposure_fraction:
        reasons.append("gross_exposure_limit")
    if projected_symbol > account.equity * limits.max_symbol_exposure_fraction:
        reasons.append("symbol_exposure_limit")
    required_cash_reserve = max(
        limits.minimum_cash_reserve,
        account.equity * limits.minimum_cash_reserve_fraction,
    )
    if order.side is Side.BUY and projected_cash < required_cash_reserve:
        reasons.append("cash_reserve_limit")

    recent_cutoff = now - limits.order_window
    if any(
        timestamp > now + limits.max_quote_future_skew for timestamp in state.submission_timestamps
    ):
        reasons.append("submission_timestamp_from_future")
    recent_order_count = sum(
        recent_cutoff <= timestamp <= now for timestamp in state.submission_timestamps
    )
    session_order_count = sum(
        state.calendar.regular_open <= timestamp < state.calendar.regular_close
        for timestamp in state.submission_timestamps
    )
    if recent_order_count >= limits.max_orders_per_window:
        reasons.append("order_rate_limit")
    if session_order_count >= limits.max_orders_per_session:
        reasons.append("session_order_limit")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return PaperControlDecision(
        allowed=not unique_reasons,
        reasons=unique_reasons,
        candidate_notional=candidate_notional,
        committed_open_buy_notional=committed_open_buy_notional,
        projected_gross_exposure=projected_gross,
        projected_symbol_exposure=projected_symbol,
        projected_cash=projected_cash,
        recent_order_count=recent_order_count,
        session_order_count=session_order_count,
    )


def _open_order_commitment(
    orders: Sequence[BrokerOrderSnapshot],
    strategy_namespace: str,
    reasons: list[str],
) -> tuple[Decimal, int]:
    commitment = ZERO
    owned_count = 0
    for order in orders:
        if order.status.terminal:
            continue
        if not is_owned_client_order_id(order.client_order_id, strategy_namespace):
            reasons.append("foreign_open_order")
            continue
        owned_count += 1
        if order.side is not Side.BUY:
            continue
        remaining = order.quantity - order.filled_quantity
        price = order.limit_price or order.average_fill_price
        if price is None:
            reasons.append("open_order_price_unknown")
            continue
        commitment += remaining * price
    return commitment, owned_count


def _position_exposure(
    positions: Sequence[BrokerPositionSnapshot],
    reasons: list[str],
) -> tuple[Decimal, Mapping[str, Decimal], Mapping[str, Decimal]]:
    gross = ZERO
    by_symbol: dict[str, Decimal] = {}
    quantities: dict[str, Decimal] = {}
    for position in positions:
        if position.quantity < 0 or position.market_value < 0:
            reasons.append("short_or_negative_position")
        value = abs(position.market_value)
        gross += value
        symbol = position.symbol.upper()
        by_symbol[symbol] = by_symbol.get(symbol, ZERO) + value
        quantities[symbol] = quantities.get(symbol, ZERO) + position.quantity
    return gross, by_symbol, quantities
