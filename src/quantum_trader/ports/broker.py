"""Broker port implemented by simulation or explicitly isolated adapters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from quantum_trader.domain.models import Fill, MarketEvent, OrderIntent, RiskDecision


class Broker(Protocol):
    """Order lifecycle contract consumed by the orchestration engine."""

    def submit(self, intent: OrderIntent, decision: RiskDecision) -> str:
        """Queue a risk-approved order and return its deterministic order ID."""

    def on_market_event(self, event: MarketEvent) -> Sequence[Fill]:
        """Process one event and return fills generated for queued orders."""

    @property
    def pending_order_count(self) -> int:
        """Return the number of queued unfilled orders."""
