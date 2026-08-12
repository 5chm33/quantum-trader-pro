"""Deterministic local request budgets for external broker and market-data APIs."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from threading import Lock


class RequestBudgetExceeded(RuntimeError):
    """The local request budget denied an external API call."""


class SlidingWindowRequestBudget:
    """Process-local rate budget that fails closed on bursts and clock regressions."""

    HARD_MAX_REQUESTS_PER_MINUTE = 180

    def __init__(
        self,
        *,
        max_requests: int = 120,
        window: timedelta = timedelta(minutes=1),
    ) -> None:
        if not 1 <= max_requests <= self.HARD_MAX_REQUESTS_PER_MINUTE:
            raise ValueError(f"max_requests must be in [1, {self.HARD_MAX_REQUESTS_PER_MINUTE}]")
        if window <= timedelta(0) or window > timedelta(minutes=1):
            raise ValueError("window must be positive and no longer than one minute")
        self.max_requests = max_requests
        self.window = window
        self._timestamps: deque[datetime] = deque()
        self._lock = Lock()

    def acquire(self, timestamp: datetime) -> int:
        """Retain one request timestamp and return the remaining window capacity."""

        _aware(timestamp)
        with self._lock:
            if self._timestamps and timestamp < self._timestamps[-1]:
                raise RequestBudgetExceeded("request clock moved backwards")
            cutoff = timestamp - self.window
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.max_requests:
                raise RequestBudgetExceeded("local external request budget exhausted")
            self._timestamps.append(timestamp)
            return self.max_requests - len(self._timestamps)

    def observed_count(self, timestamp: datetime) -> int:
        """Return retained timestamps in the active window without consuming capacity."""

        _aware(timestamp)
        with self._lock:
            cutoff = timestamp - self.window
            return sum(cutoff < item <= timestamp for item in self._timestamps)


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("request timestamp must be timezone-aware")
