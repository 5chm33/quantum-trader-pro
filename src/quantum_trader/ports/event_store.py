"""Append-only event-store contract for simulation audit trails."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any, Protocol


class EventStore(Protocol):
    """Persist ordered immutable events without storing credentials."""

    def append(
        self,
        *,
        event_type: str,
        timestamp: datetime,
        correlation_id: str,
        payload: Mapping[str, Any],
    ) -> int:
        """Append an event and return its monotonic sequence number."""

    def iter_events(self) -> Iterator[dict[str, Any]]:
        """Yield persisted events in sequence order."""

    def close(self) -> None:
        """Flush and close the event store."""
