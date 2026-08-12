"""Strict CSV market-data replay with cryptographic provenance."""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from pathlib import Path

from quantum_trader.domain.models import MarketEvent

_REQUIRED_COLUMNS = {"datetime", "open", "high", "low", "close", "volume"}


class CsvReplayMarketData:
    """Read one-symbol OHLCV events from a local CSV file."""

    def __init__(
        self,
        path: str | Path,
        *,
        symbol: str,
        naive_timezone: tzinfo = UTC,
        maximum_gap: timedelta | None = timedelta(days=7),
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        if not symbol.strip():
            raise ValueError("symbol must not be empty")
        if naive_timezone.utcoffset(None) is None:
            raise ValueError("naive_timezone must describe a real UTC offset")
        if maximum_gap is not None and maximum_gap <= timedelta(0):
            raise ValueError("maximum_gap must be positive")
        self.symbol = symbol.strip().upper()
        self.naive_timezone = naive_timezone
        self.maximum_gap = maximum_gap
        self._sha256 = self._digest()

    @property
    def source_name(self) -> str:
        return f"csv:{self.path.name}:sha256:{self._sha256}"

    def stream(self) -> Iterable[MarketEvent]:
        previous_timestamp: datetime | None = None
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or ())
            missing = sorted(_REQUIRED_COLUMNS - fieldnames)
            if missing:
                raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")

            row_count = 0
            for row_number, row in enumerate(reader, start=2):
                row_count += 1
                try:
                    timestamp = self._parse_timestamp(row["datetime"])
                    event = MarketEvent(
                        timestamp=timestamp,
                        symbol=self.symbol,
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=int(Decimal(row["volume"])),
                        source=self.source_name,
                    )
                except (InvalidOperation, ValueError, TypeError) as exc:
                    raise ValueError(f"invalid CSV row {row_number}: {exc}") from exc

                if previous_timestamp is not None:
                    if timestamp <= previous_timestamp:
                        raise ValueError(
                            "timestamps must be strictly increasing; "
                            f"row {row_number} is out of order"
                        )
                    gap = timestamp - previous_timestamp
                    if self.maximum_gap is not None and gap > self.maximum_gap:
                        raise ValueError(
                            f"data gap {gap} exceeds maximum {self.maximum_gap} at row {row_number}"
                        )
                previous_timestamp = timestamp
                yield event

            if row_count == 0:
                raise ValueError("CSV contains no market events")

    def _parse_timestamp(self, value: str) -> datetime:
        timestamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            timestamp = timestamp.replace(tzinfo=self.naive_timezone)
        return timestamp.astimezone(UTC)

    def _digest(self) -> str:
        digest = hashlib.sha256()
        with self.path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
