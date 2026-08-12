"""Deterministic in-memory broker used by every default and test run."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantum_trader.domain.models import (
    ZERO,
    Fill,
    MarketEvent,
    OrderIntent,
    RiskDecision,
    Side,
    stable_id,
)


@dataclass(frozen=True, slots=True)
class _PendingOrder:
    order_id: str
    intent: OrderIntent
    approved_quantity: int


class SimulatedBroker:
    """Queue orders and fill them at the next event open with declared costs."""

    def __init__(
        self,
        *,
        slippage_bps: Decimal = Decimal("1"),
        fee_per_order: Decimal = ZERO,
        fee_per_share: Decimal = ZERO,
    ) -> None:
        if slippage_bps < ZERO or fee_per_order < ZERO or fee_per_share < ZERO:
            raise ValueError("slippage and fees must not be negative")
        self.slippage_bps = slippage_bps
        self.fee_per_order = fee_per_order
        self.fee_per_share = fee_per_share
        self._pending: list[_PendingOrder] = []
        self._order_ids: set[str] = set()

    @property
    def pending_order_count(self) -> int:
        return len(self._pending)

    def submit(self, intent: OrderIntent, decision: RiskDecision) -> str:
        if not decision.allowed:
            raise ValueError("the simulated broker accepts only allowed risk decisions")
        if decision.intent_id != intent.intent_id:
            raise ValueError("risk decision does not correspond to the supplied intent")
        if decision.approved_quantity <= 0 or decision.approved_quantity > intent.quantity:
            raise ValueError("approved quantity is invalid")
        order_id = stable_id("order", intent.intent_id, decision.approved_quantity)
        if order_id in self._order_ids:
            raise ValueError("duplicate order submission")
        self._order_ids.add(order_id)
        self._pending.append(
            _PendingOrder(
                order_id=order_id,
                intent=intent,
                approved_quantity=decision.approved_quantity,
            )
        )
        return order_id

    def on_market_event(self, event: MarketEvent) -> tuple[Fill, ...]:
        fills: list[Fill] = []
        remaining: list[_PendingOrder] = []
        for pending in self._pending:
            intent = pending.intent
            if intent.symbol != event.symbol or event.timestamp <= intent.timestamp:
                remaining.append(pending)
                continue

            per_share_slippage = event.open * self.slippage_bps / Decimal("10000")
            if intent.side is Side.BUY:
                fill_price = event.open + per_share_slippage
            else:
                fill_price = event.open - per_share_slippage
            if fill_price <= ZERO:
                raise ValueError("slippage produced a non-positive fill price")
            fill_price = fill_price.quantize(Decimal("0.000001"))
            fee = (self.fee_per_order + self.fee_per_share * pending.approved_quantity).quantize(
                Decimal("0.000001")
            )
            slippage = (per_share_slippage * pending.approved_quantity).quantize(
                Decimal("0.000001")
            )
            fill_id = stable_id(
                "fill",
                pending.order_id,
                event.timestamp.isoformat(),
                fill_price,
                pending.approved_quantity,
            )
            fills.append(
                Fill(
                    fill_id=fill_id,
                    order_id=pending.order_id,
                    intent_id=intent.intent_id,
                    correlation_id=intent.correlation_id,
                    timestamp=event.timestamp,
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=pending.approved_quantity,
                    price=fill_price,
                    fee=fee,
                    slippage=slippage,
                )
            )
        self._pending = remaining
        return tuple(fills)

    def cancel_all(self) -> tuple[str, ...]:
        canceled = tuple(pending.order_id for pending in self._pending)
        self._pending.clear()
        return canceled
