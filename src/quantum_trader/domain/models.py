"""Validated domain models used by the deterministic simulation engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_URL, uuid5

ZERO = Decimal("0")
ONE = Decimal("1")


def decimal_value(value: Decimal | int | float | str) -> Decimal:
    """Convert a numeric value to ``Decimal`` without binary-float surprises."""

    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def stable_id(prefix: str, *parts: object) -> str:
    """Create a deterministic identifier from explicit inputs."""

    payload = "|".join(str(part) for part in parts)
    return f"{prefix}_{uuid5(NAMESPACE_URL, payload).hex}"


class Side(StrEnum):
    """Order direction supported by the long-only simulator."""

    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """One validated OHLCV market observation."""

    timestamp: datetime
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    source: str
    adjusted_close: Decimal | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("market-event timestamps must be timezone-aware")
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if not self.source or not self.source.strip():
            raise ValueError("source provenance must not be empty")
        prices = (self.open, self.high, self.low, self.close)
        if any(price <= ZERO or not price.is_finite() for price in prices):
            raise ValueError("all OHLC prices must be finite and positive")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low price is inconsistent with OHLC values")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high price is inconsistent with OHLC values")
        if self.volume < 0:
            raise ValueError("volume must not be negative")
        if self.adjusted_close is not None and (
            self.adjusted_close <= ZERO or not self.adjusted_close.is_finite()
        ):
            raise ValueError("adjusted close must be finite and positive when supplied")

    @property
    def correlation_id(self) -> str:
        return stable_id("event", self.source, self.symbol, self.timestamp.isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": self.volume,
            "adjusted_close": (None if self.adjusted_close is None else str(self.adjusted_close)),
            "source": self.source,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True, slots=True)
class Signal:
    """A deterministic strategy target expressed as a portfolio fraction."""

    timestamp: datetime
    symbol: str
    target_fraction: Decimal
    rationale: str
    correlation_id: str

    def __post_init__(self) -> None:
        if not ZERO <= self.target_fraction <= ONE:
            raise ValueError("target_fraction must be between zero and one")
        if not self.rationale:
            raise ValueError("signal rationale must not be empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "target_fraction": str(self.target_fraction),
            "rationale": self.rationale,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """A proposed order that has not yet passed risk checks."""

    intent_id: str
    correlation_id: str
    timestamp: datetime
    symbol: str
    side: Side
    quantity: int
    reference_price: Decimal
    rationale: str

    @classmethod
    def create(
        cls,
        *,
        correlation_id: str,
        timestamp: datetime,
        symbol: str,
        side: Side,
        quantity: int,
        reference_price: Decimal,
        rationale: str,
    ) -> OrderIntent:
        return cls(
            intent_id=stable_id(
                "intent",
                correlation_id,
                timestamp.isoformat(),
                symbol,
                side.value,
                quantity,
                reference_price,
            ),
            correlation_id=correlation_id,
            timestamp=timestamp,
            symbol=symbol,
            side=side,
            quantity=quantity,
            reference_price=reference_price,
            rationale=rationale,
        )

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("order-intent quantity must be positive")
        if self.reference_price <= ZERO or not self.reference_price.is_finite():
            raise ValueError("reference price must be finite and positive")

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "reference_price": str(self.reference_price),
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """The fail-closed result of evaluating an order intent."""

    allowed: bool
    reason: str
    approved_quantity: int
    intent_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("risk decision reason must not be empty")
        if self.allowed and self.approved_quantity <= 0:
            raise ValueError("an allowed decision requires a positive quantity")
        if not self.allowed and self.approved_quantity != 0:
            raise ValueError("a denied decision must approve zero quantity")

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "approved_quantity": self.approved_quantity,
            "intent_id": self.intent_id,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True, slots=True)
class Fill:
    """A deterministic simulated fill."""

    fill_id: str
    order_id: str
    intent_id: str
    correlation_id: str
    timestamp: datetime
    symbol: str
    side: Side
    quantity: int
    price: Decimal
    fee: Decimal
    slippage: Decimal

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("fill quantity must be positive")
        if self.price <= ZERO or not self.price.is_finite():
            raise ValueError("fill price must be finite and positive")
        if self.fee < ZERO or self.slippage < ZERO:
            raise ValueError("fee and slippage must not be negative")

    @property
    def gross_notional(self) -> Decimal:
        return self.price * self.quantity

    def as_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "intent_id": self.intent_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "price": str(self.price),
            "fee": str(self.fee),
            "slippage": str(self.slippage),
            "gross_notional": str(self.gross_notional),
        }


@dataclass(slots=True)
class Position:
    """Mutable position state owned exclusively by the portfolio aggregate."""

    symbol: str
    quantity: int = 0
    average_price: Decimal = ZERO

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "average_price": str(self.average_price),
        }


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One reconciled portfolio-equity observation."""

    timestamp: datetime
    cash: Decimal
    market_value: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    fees: Decimal

    def __post_init__(self) -> None:
        expected = self.cash + self.market_value
        if abs(expected - self.equity) > Decimal("0.000001"):
            raise ValueError("equity must reconcile to cash plus market value")
        if any(not value.is_finite() for value in self.__dict_values()):
            raise ValueError("equity values must be finite")

    def __dict_values(self) -> tuple[Decimal, ...]:
        return (
            self.cash,
            self.market_value,
            self.equity,
            self.realized_pnl,
            self.unrealized_pnl,
            self.fees,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "cash": str(self.cash),
            "market_value": str(self.market_value),
            "equity": str(self.equity),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "fees": str(self.fees),
        }
