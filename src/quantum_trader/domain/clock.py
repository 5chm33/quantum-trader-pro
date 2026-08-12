"""Timezone-aware clock abstractions for replay and service lifecycle logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class MarketClock(Protocol):
    """Clock contract used by the engine and lifecycle components."""

    def now(self) -> datetime:
        """Return a timezone-aware instant."""


@dataclass(slots=True)
class ReplayClock:
    """Clock advanced only by deterministic replay events."""

    current: datetime | None = None

    def advance(self, timestamp: datetime) -> None:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("replay timestamps must be timezone-aware")
        if self.current is not None and timestamp <= self.current:
            raise ValueError("replay timestamps must be strictly increasing")
        self.current = timestamp

    def now(self) -> datetime:
        if self.current is None:
            raise RuntimeError("replay clock has not received an event")
        return self.current


class SystemClock:
    """UTC system clock for service lifecycle timestamps only."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class WeekdayExchangeClock:
    """Minimal New York regular-session clock with no holiday-calendar claims."""

    market_open = time(9, 30)
    market_close = time(16, 0)

    def __init__(self) -> None:
        try:
            self.timezone = ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError as exc:
            raise RuntimeError(
                "the exchange clock requires IANA timezone data; install the 'tzdata' package"
            ) from exc

    def is_regular_session(self, timestamp: datetime) -> bool:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        local = timestamp.astimezone(self.timezone)
        return local.weekday() < 5 and self.market_open <= local.time() < self.market_close

    def next_weekday_open(self, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        local = timestamp.astimezone(self.timezone)
        candidate = local.replace(
            hour=self.market_open.hour,
            minute=self.market_open.minute,
            second=0,
            microsecond=0,
        )
        if local.weekday() < 5 and local < candidate:
            return candidate.astimezone(UTC)
        candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)
