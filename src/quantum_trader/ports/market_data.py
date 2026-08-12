"""Market-data provider port for deterministic replay streams."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from quantum_trader.domain.models import MarketEvent


class MarketDataProvider(Protocol):
    """Supply validated events in strictly increasing timestamp order."""

    @property
    def source_name(self) -> str:
        """Return stable provenance for reports and event identifiers."""

    def stream(self) -> Iterable[MarketEvent]:
        """Yield a finite event stream without hidden network access."""
