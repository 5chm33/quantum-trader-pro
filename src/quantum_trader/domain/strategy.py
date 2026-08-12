"""Deterministic strategy contracts and baseline implementation."""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Protocol

from quantum_trader.domain.models import MarketEvent, Signal


class Strategy(Protocol):
    """Pure strategy interface: explicit event in, immutable signal out."""

    @property
    def name(self) -> str:
        """Return the stable strategy name."""

    def on_market_event(self, event: MarketEvent) -> Signal:
        """Produce a target-position signal for one market event."""


class MovingAverageCrossover:
    """Long-or-cash moving-average crossover used as an auditable baseline."""

    def __init__(
        self,
        *,
        fast_window: int = 20,
        slow_window: int = 50,
        invested_fraction: Decimal = Decimal("0.95"),
    ) -> None:
        if fast_window < 2:
            raise ValueError("fast_window must be at least two")
        if slow_window <= fast_window:
            raise ValueError("slow_window must be greater than fast_window")
        if not Decimal("0") < invested_fraction <= Decimal("1"):
            raise ValueError("invested_fraction must be in (0, 1]")
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.invested_fraction = invested_fraction
        self._closes: deque[Decimal] = deque(maxlen=slow_window)

    @property
    def name(self) -> str:
        return "moving_average_crossover"

    def on_market_event(self, event: MarketEvent) -> Signal:
        self._closes.append(event.close)
        if len(self._closes) < self.slow_window:
            return Signal(
                timestamp=event.timestamp,
                symbol=event.symbol,
                target_fraction=Decimal("0"),
                rationale=(
                    f"warmup:{len(self._closes)}/{self.slow_window}; "
                    "no position before both averages are defined"
                ),
                correlation_id=event.correlation_id,
            )

        closes = tuple(self._closes)
        fast_average = sum(closes[-self.fast_window :], Decimal("0")) / self.fast_window
        slow_average = sum(closes, Decimal("0")) / self.slow_window
        invested = fast_average > slow_average
        target = self.invested_fraction if invested else Decimal("0")
        relation = "above" if invested else "at_or_below"
        return Signal(
            timestamp=event.timestamp,
            symbol=event.symbol,
            target_fraction=target,
            rationale=(
                f"fast_ma={fast_average:.6f} {relation} slow_ma={slow_average:.6f}; "
                f"target_fraction={target}"
            ),
            correlation_id=event.correlation_id,
        )
