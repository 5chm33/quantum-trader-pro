"""Append-only SQLite event store with canonical payload integrity hashes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


class SQLiteEventStore:
    """Persist one simulation run as an ordered immutable event stream."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '1');

            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_type_idx ON events(event_type);
            CREATE INDEX IF NOT EXISTS events_correlation_idx ON events(correlation_id);
            """
        )
        self._connection.commit()

    def append(
        self,
        *,
        event_type: str,
        timestamp: datetime,
        correlation_id: str,
        payload: Mapping[str, Any],
    ) -> int:
        connection = self._require_connection()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("stored event timestamps must be timezone-aware")
        if not event_type.strip() or not correlation_id.strip():
            raise ValueError("event_type and correlation_id must not be empty")
        payload_json = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=self._json_default,
        )
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        cursor = connection.execute(
            """
            INSERT INTO events(event_type, timestamp, correlation_id, payload_json, payload_sha256)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_type.strip(),
                timestamp.isoformat(),
                correlation_id.strip(),
                payload_json,
                payload_sha256,
            ),
        )
        connection.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an event sequence")
        return int(cursor.lastrowid)

    def iter_events(self) -> Iterator[dict[str, Any]]:
        connection = self._require_connection()
        rows = connection.execute(
            """
            SELECT sequence, event_type, timestamp, correlation_id, payload_json, payload_sha256
            FROM events
            ORDER BY sequence
            """
        )
        for row in rows:
            yield {
                "sequence": int(row["sequence"]),
                "event_type": str(row["event_type"]),
                "timestamp": str(row["timestamp"]),
                "correlation_id": str(row["correlation_id"]),
                "payload": json.loads(str(row["payload_json"])),
                "payload_sha256": str(row["payload_sha256"]),
            }

    def close(self) -> None:
        if self._connection is not None:
            self._connection.commit()
            self._connection.close()
            self._connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("event store is closed")
        return self._connection

    @staticmethod
    def _json_default(value: object) -> str:
        if isinstance(value, (Decimal, datetime)):
            return str(value)
        raise TypeError(f"unsupported JSON value: {type(value).__name__}")
