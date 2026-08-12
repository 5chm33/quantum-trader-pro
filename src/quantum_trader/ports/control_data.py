"""External control-data contract for paper pre-trade validation."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from quantum_trader.domain.market_controls import (
    AssetTradingSnapshot,
    LatestQuoteSnapshot,
    MarketCalendarDay,
)


class PaperControlData(Protocol):
    """Read only the broker and market state required by pre-trade controls."""

    def get_asset(self, symbol: str) -> AssetTradingSnapshot:
        """Return current broker eligibility for one symbol."""

    def get_calendar_day(self, trade_date: date) -> MarketCalendarDay | None:
        """Return the broker calendar row for one bounded trade date."""

    def get_latest_quote(self, symbol: str) -> LatestQuoteSnapshot:
        """Return one timestamped best bid and ask snapshot."""
