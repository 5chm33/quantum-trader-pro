"""Normalized external broker state and order lifecycle contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from quantum_trader.domain.execution import ArmedExecutionContext, ExecutionMode
from quantum_trader.domain.models import OrderIntent, RiskDecision, Side

OWNED_CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
BROKER_CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class AccountStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUBMISSION_FAILED = "submission_failed"
    ACTION_REQUIRED = "action_required"
    UNKNOWN = "unknown"


class BrokerOrderStatus(StrEnum):
    PENDING_NEW = "pending_new"
    ACCEPTED = "accepted"
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    PENDING_CANCEL = "pending_cancel"
    PENDING_REPLACE = "pending_replace"
    ACCEPTED_FOR_BIDDING = "accepted_for_bidding"
    HELD = "held"
    STOPPED = "stopped"
    FILLED = "filled"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    REPLACED = "replaced"
    SUSPENDED = "suspended"
    DONE_FOR_DAY = "done_for_day"
    CALCULATED = "calculated"
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in {
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
            BrokerOrderStatus.REJECTED,
            BrokerOrderStatus.REPLACED,
            BrokerOrderStatus.DONE_FOR_DAY,
            BrokerOrderStatus.CALCULATED,
        }


class BrokerOrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GOOD_TIL_CANCELED = "gtc"
    IMMEDIATE_OR_CANCEL = "ioc"
    FILL_OR_KILL = "fok"


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _finite(value: Decimal, field_name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")


def _nonnegative(value: Decimal, field_name: str) -> None:
    if value < 0 or not value.is_finite():
        raise ValueError(f"{field_name} must be finite and nonnegative")


def _positive(value: Decimal, field_name: str) -> None:
    if value <= 0 or not value.is_finite():
        raise ValueError(f"{field_name} must be finite and positive")


def _sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def deterministic_client_order_id(
    *,
    strategy_namespace: str,
    account_sha256: str,
    intent: OrderIntent,
    approved_quantity: int,
) -> str:
    """Create a compact deterministic ID suitable for broker idempotency queries."""

    _sha256(account_sha256, "account_sha256")
    if approved_quantity <= 0:
        raise ValueError("approved_quantity must be positive")
    namespace = strategy_namespace.strip().lower()
    if not namespace or not namespace.replace("-", "").replace("_", "").isalnum():
        raise ValueError("strategy_namespace has invalid characters")
    digest = hashlib.sha256(
        "\x1f".join(
            (
                "qtpro-client-order-v1",
                namespace,
                account_sha256,
                intent.intent_id,
                intent.correlation_id,
                intent.timestamp.isoformat(),
                intent.symbol.upper(),
                intent.side.value,
                str(approved_quantity),
                str(intent.reference_price),
            )
        ).encode("utf-8")
    ).hexdigest()[:24]
    prefix = re.sub(r"[^a-z0-9_-]", "-", namespace)[:12]
    client_order_id = f"qt-{prefix}-{digest}"
    if not OWNED_CLIENT_ORDER_ID_PATTERN.fullmatch(client_order_id):
        raise ValueError("derived client_order_id violates the broker-safe format")
    return client_order_id


@dataclass(frozen=True, slots=True)
class BrokerAccountSnapshot:
    environment: ExecutionMode
    account_sha256: str
    status: AccountStatus
    trading_blocked: bool
    account_blocked: bool
    transfers_blocked: bool
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    captured_at: datetime
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if self.environment not in {ExecutionMode.PAPER, ExecutionMode.LIVE}:
            raise ValueError("broker account environment must be paper or live")
        _sha256(self.account_sha256, "account_sha256")
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")
        _finite(self.cash, "cash")
        _nonnegative(self.equity, "equity")
        _nonnegative(self.buying_power, "buying_power")
        _aware(self.captured_at, "captured_at")

    @property
    def permits_new_exposure(self) -> bool:
        return (
            self.status is AccountStatus.ACTIVE
            and not self.trading_blocked
            and not self.account_blocked
            and not self.transfers_blocked
            and self.buying_power > 0
            and self.cash >= 0
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment.value,
            "account_sha256": self.account_sha256,
            "status": self.status.value,
            "trading_blocked": self.trading_blocked,
            "account_blocked": self.account_blocked,
            "transfers_blocked": self.transfers_blocked,
            "cash": str(self.cash),
            "equity": str(self.equity),
            "buying_power": str(self.buying_power),
            "captured_at": self.captured_at.isoformat(),
            "raw_payload_sha256": self.raw_payload_sha256,
            "permits_new_exposure": self.permits_new_exposure,
        }


@dataclass(frozen=True, slots=True)
class BrokerClockSnapshot:
    is_open: bool
    timestamp: datetime
    next_open: datetime
    next_close: datetime
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        _aware(self.timestamp, "timestamp")
        _aware(self.next_open, "next_open")
        _aware(self.next_close, "next_close")
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")
        if self.next_close <= self.timestamp and self.is_open:
            raise ValueError("open clock must have a future next_close")
        if self.next_open < self.timestamp and not self.is_open:
            raise ValueError("closed clock must not report next_open in the past")

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_open": self.is_open,
            "timestamp": self.timestamp.isoformat(),
            "next_open": self.next_open.isoformat(),
            "next_close": self.next_close.isoformat(),
            "raw_payload_sha256": self.raw_payload_sha256,
        }


@dataclass(frozen=True, slots=True)
class BrokerPositionSnapshot:
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    market_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    captured_at: datetime
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("position symbol must not be empty")
        if not self.quantity.is_finite():
            raise ValueError("position quantity must be finite")
        _positive(self.average_entry_price, "average_entry_price")
        _positive(self.market_price, "market_price")
        if not self.market_value.is_finite() or not self.unrealized_pnl.is_finite():
            raise ValueError("position values must be finite")
        _aware(self.captured_at, "captured_at")
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol.upper(),
            "quantity": str(self.quantity),
            "average_entry_price": str(self.average_entry_price),
            "market_price": str(self.market_price),
            "market_value": str(self.market_value),
            "unrealized_pnl": str(self.unrealized_pnl),
            "captured_at": self.captured_at.isoformat(),
            "raw_payload_sha256": self.raw_payload_sha256,
        }


@dataclass(frozen=True, slots=True)
class ApprovedBrokerOrder:
    """Immutable external order request created only from a risk approval."""

    client_order_id: str
    intent_id: str
    correlation_id: str
    symbol: str
    side: Side
    quantity: int
    order_type: BrokerOrderType
    time_in_force: TimeInForce
    reference_price: Decimal
    limit_price: Decimal | None
    extended_hours: bool
    strategy_namespace: str
    account_sha256: str
    created_at: datetime

    @classmethod
    def from_approved_intent(
        cls,
        *,
        context: ArmedExecutionContext,
        intent: OrderIntent,
        decision: RiskDecision,
        order_type: BrokerOrderType,
        time_in_force: TimeInForce,
        limit_price: Decimal | None,
        extended_hours: bool = False,
    ) -> ApprovedBrokerOrder:
        if (
            decision.intent_id != intent.intent_id
            or decision.correlation_id != intent.correlation_id
        ):
            raise ValueError("risk decision does not belong to the order intent")
        if not decision.allowed:
            raise ValueError("denied risk decision cannot create a broker order")
        client_order_id = deterministic_client_order_id(
            strategy_namespace=context.strategy_namespace,
            account_sha256=context.fingerprint.account_sha256,
            intent=intent,
            approved_quantity=decision.approved_quantity,
        )
        return cls(
            client_order_id=client_order_id,
            intent_id=intent.intent_id,
            correlation_id=intent.correlation_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=decision.approved_quantity,
            order_type=order_type,
            time_in_force=time_in_force,
            reference_price=intent.reference_price,
            limit_price=limit_price,
            extended_hours=extended_hours,
            strategy_namespace=context.strategy_namespace,
            account_sha256=context.fingerprint.account_sha256,
            created_at=intent.timestamp,
        )

    def __post_init__(self) -> None:
        if not OWNED_CLIENT_ORDER_ID_PATTERN.fullmatch(self.client_order_id):
            raise ValueError("client_order_id has invalid characters or length")
        if self.quantity <= 0:
            raise ValueError("broker order quantity must be positive")
        _positive(self.reference_price, "reference_price")
        if self.order_type is BrokerOrderType.LIMIT:
            if self.limit_price is None:
                raise ValueError("limit order requires limit_price")
            _positive(self.limit_price, "limit_price")
        elif self.limit_price is not None:
            raise ValueError("market order must not define limit_price")
        if self.extended_hours and (
            self.order_type is not BrokerOrderType.LIMIT
            or self.time_in_force is not TimeInForce.DAY
        ):
            raise ValueError("extended-hours orders require limit/day policy")
        _sha256(self.account_sha256, "account_sha256")
        _aware(self.created_at, "created_at")

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "intent_id": self.intent_id,
            "correlation_id": self.correlation_id,
            "symbol": self.symbol.upper(),
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "time_in_force": self.time_in_force.value,
            "reference_price": str(self.reference_price),
            "limit_price": str(self.limit_price) if self.limit_price is not None else None,
            "extended_hours": self.extended_hours,
            "strategy_namespace": self.strategy_namespace,
            "account_sha256": self.account_sha256,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class BrokerOrderSnapshot:
    broker_order_id: str
    client_order_id: str
    status: BrokerOrderStatus
    symbol: str
    side: Side
    quantity: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    submitted_at: datetime
    updated_at: datetime
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if not self.broker_order_id:
            raise ValueError("broker_order_id must not be empty")
        if not BROKER_CLIENT_ORDER_ID_PATTERN.fullmatch(self.client_order_id):
            raise ValueError("client_order_id has invalid characters or length")
        _positive(self.quantity, "quantity")
        _nonnegative(self.filled_quantity, "filled_quantity")
        if self.filled_quantity > self.quantity:
            raise ValueError("filled_quantity must not exceed order quantity")
        if self.filled_quantity > 0:
            if self.average_fill_price is None:
                raise ValueError("filled order state requires average_fill_price")
            _positive(self.average_fill_price, "average_fill_price")
        elif self.average_fill_price is not None:
            raise ValueError("unfilled order state must not have average_fill_price")
        if self.status is BrokerOrderStatus.FILLED and self.filled_quantity != self.quantity:
            raise ValueError("filled status requires cumulative filled quantity")
        _aware(self.submitted_at, "submitted_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.submitted_at:
            raise ValueError("updated_at must not precede submitted_at")
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity

    def as_dict(self) -> dict[str, Any]:
        return {
            "broker_order_id": self.broker_order_id,
            "client_order_id": self.client_order_id,
            "status": self.status.value,
            "terminal": self.status.terminal,
            "symbol": self.symbol.upper(),
            "side": self.side.value,
            "quantity": str(self.quantity),
            "filled_quantity": str(self.filled_quantity),
            "remaining_quantity": str(self.remaining_quantity),
            "average_fill_price": (
                str(self.average_fill_price) if self.average_fill_price is not None else None
            ),
            "submitted_at": self.submitted_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "raw_payload_sha256": self.raw_payload_sha256,
        }


@dataclass(frozen=True, slots=True)
class BrokerFillActivity:
    activity_id: str
    execution_id: str
    broker_order_id: str
    client_order_id: str | None
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal | None
    timestamp: datetime
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        for name in ("activity_id", "execution_id", "broker_order_id"):
            if not getattr(self, name):
                raise ValueError(f"{name} must not be empty")
        if self.client_order_id is not None and not BROKER_CLIENT_ORDER_ID_PATTERN.fullmatch(
            self.client_order_id
        ):
            raise ValueError("client_order_id has invalid characters or length")
        _positive(self.quantity, "quantity")
        _positive(self.price, "price")
        if self.fee is not None:
            _nonnegative(self.fee, "fee")
        _aware(self.timestamp, "timestamp")
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")

    def as_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "execution_id": self.execution_id,
            "broker_order_id": self.broker_order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol.upper(),
            "side": self.side.value,
            "quantity": str(self.quantity),
            "price": str(self.price),
            "fee": str(self.fee) if self.fee is not None else None,
            "timestamp": self.timestamp.isoformat(),
            "raw_payload_sha256": self.raw_payload_sha256,
        }


@dataclass(frozen=True, slots=True)
class BrokerActivityPage:
    activities: tuple[BrokerFillActivity, ...]
    next_page_token: str | None
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")
        execution_ids = [activity.execution_id for activity in self.activities]
        if len(execution_ids) != len(set(execution_ids)):
            raise ValueError("activity page contains duplicate execution IDs")


@dataclass(frozen=True, slots=True)
class BrokerCancelResult:
    broker_order_id: str
    requested_at: datetime
    accepted: bool
    observed_status: BrokerOrderStatus
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if not self.broker_order_id:
            raise ValueError("broker_order_id must not be empty")
        _aware(self.requested_at, "requested_at")
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")

    @property
    def verified_terminal(self) -> bool:
        return self.observed_status.terminal


class SubmissionState(StrEnum):
    """Durable submission states surrounding the external network side effect."""

    PERSISTED = "persisted"
    STARTED = "started"
    ACKNOWLEDGED = "acknowledged"
    AMBIGUOUS = "ambiguous"
    RECONCILED = "reconciled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SubmissionJournalEntry:
    """Evidence that an approved payload was persisted before broker submission."""

    sequence: int
    client_order_id: str
    requested_payload_sha256: str
    state: SubmissionState
    created_at: datetime
    updated_at: datetime
    broker_order_id: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise ValueError("submission journal sequence must be positive")
        if not OWNED_CLIENT_ORDER_ID_PATTERN.fullmatch(self.client_order_id):
            raise ValueError("client_order_id has invalid characters or length")
        _sha256(self.requested_payload_sha256, "requested_payload_sha256")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("submission journal updated_at must not precede created_at")
        if self.state in {SubmissionState.ACKNOWLEDGED, SubmissionState.RECONCILED}:
            if not self.broker_order_id:
                raise ValueError("acknowledged submission requires broker_order_id")
        elif self.broker_order_id is not None:
            raise ValueError("broker_order_id is allowed only after acknowledgement")
        if self.state in {SubmissionState.AMBIGUOUS, SubmissionState.REJECTED} and not self.reason:
            raise ValueError("ambiguous or rejected submission requires a reason")

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "client_order_id": self.client_order_id,
            "requested_payload_sha256": self.requested_payload_sha256,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "broker_order_id": self.broker_order_id,
            "reason": self.reason,
        }


class TransitionDisposition(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"


_ALLOWED_ORDER_TRANSITIONS: dict[BrokerOrderStatus, frozenset[BrokerOrderStatus]] = {
    BrokerOrderStatus.PENDING_NEW: frozenset(
        {
            BrokerOrderStatus.ACCEPTED,
            BrokerOrderStatus.NEW,
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
            BrokerOrderStatus.REJECTED,
        }
    ),
    BrokerOrderStatus.ACCEPTED: frozenset(
        {
            BrokerOrderStatus.NEW,
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.PENDING_CANCEL,
            BrokerOrderStatus.PENDING_REPLACE,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
            BrokerOrderStatus.REJECTED,
            BrokerOrderStatus.HELD,
        }
    ),
    BrokerOrderStatus.NEW: frozenset(
        {
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.PENDING_CANCEL,
            BrokerOrderStatus.PENDING_REPLACE,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
            BrokerOrderStatus.REJECTED,
            BrokerOrderStatus.DONE_FOR_DAY,
            BrokerOrderStatus.HELD,
            BrokerOrderStatus.SUSPENDED,
            BrokerOrderStatus.STOPPED,
        }
    ),
    BrokerOrderStatus.PARTIALLY_FILLED: frozenset(
        {
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.PENDING_CANCEL,
            BrokerOrderStatus.PENDING_REPLACE,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
            BrokerOrderStatus.DONE_FOR_DAY,
        }
    ),
    BrokerOrderStatus.PENDING_CANCEL: frozenset(
        {
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
        }
    ),
    BrokerOrderStatus.PENDING_REPLACE: frozenset(
        {
            BrokerOrderStatus.NEW,
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.REPLACED,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.REJECTED,
        }
    ),
    BrokerOrderStatus.ACCEPTED_FOR_BIDDING: frozenset(
        {
            BrokerOrderStatus.NEW,
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
        }
    ),
    BrokerOrderStatus.HELD: frozenset(
        {
            BrokerOrderStatus.NEW,
            BrokerOrderStatus.PENDING_CANCEL,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.REJECTED,
        }
    ),
    BrokerOrderStatus.STOPPED: frozenset(
        {
            BrokerOrderStatus.NEW,
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.CANCELED,
        }
    ),
    BrokerOrderStatus.SUSPENDED: frozenset(
        {
            BrokerOrderStatus.NEW,
            BrokerOrderStatus.PENDING_CANCEL,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.REJECTED,
        }
    ),
    BrokerOrderStatus.UNKNOWN: frozenset(),
    BrokerOrderStatus.FILLED: frozenset(),
    BrokerOrderStatus.CANCELED: frozenset(),
    BrokerOrderStatus.EXPIRED: frozenset(),
    BrokerOrderStatus.REJECTED: frozenset(),
    BrokerOrderStatus.REPLACED: frozenset(),
    BrokerOrderStatus.DONE_FOR_DAY: frozenset(),
    BrokerOrderStatus.CALCULATED: frozenset(),
}


class BrokerOrderStateMachine:
    """Validate normalized broker transitions without applying accounting twice."""

    @staticmethod
    def require_transition(
        previous: BrokerOrderSnapshot,
        current: BrokerOrderSnapshot,
    ) -> TransitionDisposition:
        if previous.broker_order_id != current.broker_order_id:
            raise ValueError("broker_order_id changed across one order transition")
        if previous.client_order_id != current.client_order_id:
            raise ValueError("client_order_id changed across one order transition")
        if previous.symbol.upper() != current.symbol.upper() or previous.side is not current.side:
            raise ValueError("symbol or side changed across one order transition")
        if previous.quantity != current.quantity:
            raise ValueError("order quantity changed without an explicit replacement order")
        if current.updated_at < previous.updated_at:
            raise ValueError("out-of-order broker update timestamp")
        if current.filled_quantity < previous.filled_quantity:
            raise ValueError("cumulative filled quantity moved backwards")
        if (
            current.status is previous.status
            and current.filled_quantity == previous.filled_quantity
            and current.average_fill_price == previous.average_fill_price
        ):
            return TransitionDisposition.DUPLICATE
        if previous.status.terminal:
            raise ValueError("terminal broker order cannot transition")
        allowed = _ALLOWED_ORDER_TRANSITIONS[previous.status]
        if current.status not in allowed:
            raise ValueError(
                f"invalid broker order transition: {previous.status.value} -> "
                f"{current.status.value}"
            )
        return TransitionDisposition.APPLIED
