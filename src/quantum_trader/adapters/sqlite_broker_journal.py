"""Transactional SQLite journal for paper submissions and reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from quantum_trader.domain.brokerage import (
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerFillActivity,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    SubmissionJournalEntry,
    SubmissionState,
)
from quantum_trader.domain.models import Side

_SCHEMA_VERSION = "1"
_TERMINAL_SUBMISSION_STATES = frozenset({SubmissionState.RECONCILED, SubmissionState.REJECTED})
_ALLOWED_SUBMISSION_TRANSITIONS: dict[SubmissionState, frozenset[SubmissionState]] = {
    SubmissionState.PERSISTED: frozenset({SubmissionState.STARTED, SubmissionState.REJECTED}),
    SubmissionState.STARTED: frozenset(
        {
            SubmissionState.ACKNOWLEDGED,
            SubmissionState.AMBIGUOUS,
            SubmissionState.REJECTED,
        }
    ),
    SubmissionState.AMBIGUOUS: frozenset({SubmissionState.RECONCILED, SubmissionState.REJECTED}),
    SubmissionState.ACKNOWLEDGED: frozenset({SubmissionState.RECONCILED}),
    SubmissionState.RECONCILED: frozenset(),
    SubmissionState.REJECTED: frozenset(),
}


class BrokerJournalError(RuntimeError):
    """Base durable-journal error."""


class BrokerJournalConflict(BrokerJournalError):
    """A supposedly idempotent identity arrived with different content."""


class SQLiteBrokerJournal:
    """Durable, credential-free paper submission and reconciliation journal."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        if not self.path.is_absolute():
            raise BrokerJournalError("broker journal path must be absolute")
        _prepare_secure_database_path(self.path)
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
            INSERT OR IGNORE INTO metadata(key, value)
            VALUES ('schema_version', '1');

            CREATE TABLE IF NOT EXISTS submissions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_id TEXT NOT NULL UNIQUE,
                intent_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                strategy_namespace TEXT NOT NULL,
                account_sha256 TEXT NOT NULL,
                requested_payload_sha256 TEXT NOT NULL,
                order_json TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                broker_order_id TEXT,
                reason TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS submissions_broker_id_idx
            ON submissions(broker_order_id)
            WHERE broker_order_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS broker_orders (
                broker_order_id TEXT PRIMARY KEY,
                client_order_id TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS broker_orders_client_idx
            ON broker_orders(client_order_id);

            CREATE TABLE IF NOT EXISTS broker_fills (
                execution_id TEXT PRIMARY KEY,
                activity_id TEXT NOT NULL UNIQUE,
                broker_order_id TEXT NOT NULL,
                client_order_id TEXT,
                timestamp TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS broker_fills_order_idx
            ON broker_fills(broker_order_id);

            CREATE TABLE IF NOT EXISTS broker_positions (
                symbol TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS account_snapshots (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reconciliation_reports (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS checkpoints (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        version = self._connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if version is None or str(version["value"]) != _SCHEMA_VERSION:
            raise BrokerJournalError("unsupported broker journal schema version")
        self._connection.commit()

    def persist_approved_order(
        self,
        *,
        order: ApprovedBrokerOrder,
        requested_payload_sha256: str,
        timestamp: datetime,
    ) -> SubmissionJournalEntry:
        connection = self._require_connection()
        _aware(timestamp, "timestamp")
        order_json = _canonical_json(order.as_dict())
        candidate = SubmissionJournalEntry(
            sequence=1,
            client_order_id=order.client_order_id,
            requested_payload_sha256=requested_payload_sha256,
            state=SubmissionState.PERSISTED,
            created_at=timestamp,
            updated_at=timestamp,
        )
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT * FROM submissions WHERE client_order_id = ?",
                (order.client_order_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["requested_payload_sha256"]) != requested_payload_sha256
                    or str(existing["order_json"]) != order_json
                ):
                    raise BrokerJournalConflict(
                        "client order ID already exists with different approved content"
                    )
                connection.commit()
                return _submission_from_row(existing)
            cursor = connection.execute(
                """
                INSERT INTO submissions(
                    client_order_id, intent_id, correlation_id, strategy_namespace,
                    account_sha256, requested_payload_sha256, order_json, state,
                    created_at, updated_at, broker_order_id, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    order.client_order_id,
                    order.intent_id,
                    order.correlation_id,
                    order.strategy_namespace,
                    order.account_sha256,
                    requested_payload_sha256,
                    order_json,
                    candidate.state.value,
                    _iso(timestamp),
                    _iso(timestamp),
                ),
            )
            if cursor.lastrowid is None:
                raise BrokerJournalError("SQLite did not return a submission sequence")
            connection.commit()
            return SubmissionJournalEntry(
                sequence=int(cursor.lastrowid),
                client_order_id=candidate.client_order_id,
                requested_payload_sha256=candidate.requested_payload_sha256,
                state=candidate.state,
                created_at=candidate.created_at,
                updated_at=candidate.updated_at,
            )
        except Exception:
            connection.rollback()
            raise

    def transition_submission(
        self,
        *,
        client_order_id: str,
        state: SubmissionState,
        timestamp: datetime,
        broker_order_id: str | None = None,
        reason: str | None = None,
    ) -> SubmissionJournalEntry:
        connection = self._require_connection()
        _aware(timestamp, "timestamp")
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT * FROM submissions WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if row is None:
                raise BrokerJournalError("submission does not exist")
            current = _submission_from_row(row)
            if state is current.state:
                if current.broker_order_id == broker_order_id and current.reason == reason:
                    connection.commit()
                    return current
                raise BrokerJournalConflict(
                    "duplicate submission state has different broker ID or reason"
                )
            if state not in _ALLOWED_SUBMISSION_TRANSITIONS[current.state]:
                raise BrokerJournalConflict(
                    f"invalid submission transition: {current.state.value} -> {state.value}"
                )
            updated = SubmissionJournalEntry(
                sequence=current.sequence,
                client_order_id=current.client_order_id,
                requested_payload_sha256=current.requested_payload_sha256,
                state=state,
                created_at=current.created_at,
                updated_at=timestamp,
                broker_order_id=broker_order_id,
                reason=reason,
            )
            connection.execute(
                """
                UPDATE submissions
                SET state = ?, updated_at = ?, broker_order_id = ?, reason = ?
                WHERE client_order_id = ?
                """,
                (
                    updated.state.value,
                    _iso(updated.updated_at),
                    updated.broker_order_id,
                    updated.reason,
                    updated.client_order_id,
                ),
            )
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise

    def unresolved_submissions(self) -> Sequence[SubmissionJournalEntry]:
        connection = self._require_connection()
        rows = connection.execute(
            """
            SELECT * FROM submissions
            WHERE state NOT IN (?, ?)
            ORDER BY sequence
            """,
            (
                SubmissionState.RECONCILED.value,
                SubmissionState.REJECTED.value,
            ),
        )
        return tuple(_submission_from_row(row) for row in rows)

    def submission_by_client_id(
        self,
        client_order_id: str,
    ) -> SubmissionJournalEntry | None:
        connection = self._require_connection()
        row = connection.execute(
            "SELECT * FROM submissions WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
        return _submission_from_row(row) if row is not None else None

    def known_client_order_ids(self) -> frozenset[str]:
        connection = self._require_connection()
        rows = connection.execute("SELECT client_order_id FROM submissions ORDER BY sequence")
        return frozenset(str(row["client_order_id"]) for row in rows)

    def submission_timestamps(self) -> Sequence[datetime]:
        connection = self._require_connection()
        rows = connection.execute("SELECT created_at FROM submissions ORDER BY sequence")
        return tuple(datetime.fromisoformat(str(row["created_at"])) for row in rows)

    def apply_reconciliation(
        self,
        *,
        account: BrokerAccountSnapshot,
        orders: Sequence[BrokerOrderSnapshot],
        positions: Sequence[BrokerPositionSnapshot],
        fills: Sequence[BrokerFillActivity],
        submission_resolutions: Mapping[str, str],
        activity_checkpoint: str | None,
        timestamp: datetime,
        report: Mapping[str, Any],
    ) -> int:
        connection = self._require_connection()
        _aware(timestamp, "timestamp")
        connection.execute("BEGIN IMMEDIATE")
        try:
            account_json, account_hash = _payload(account.as_dict())
            connection.execute(
                """
                INSERT INTO account_snapshots(captured_at, payload_json, payload_sha256)
                VALUES (?, ?, ?)
                """,
                (_iso(account.captured_at), account_json, account_hash),
            )
            for order in orders:
                payload_json, payload_hash = _payload(order.as_dict())
                existing = connection.execute(
                    "SELECT payload_json, updated_at FROM broker_orders WHERE broker_order_id = ?",
                    (order.broker_order_id,),
                ).fetchone()
                if existing is not None and _parse_datetime(
                    str(existing["updated_at"])
                ) > order.updated_at.astimezone(UTC):
                    raise BrokerJournalConflict("broker order update moved backwards")
                connection.execute(
                    """
                    INSERT INTO broker_orders(
                        broker_order_id, client_order_id, status, updated_at,
                        payload_json, payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(broker_order_id) DO UPDATE SET
                        client_order_id = excluded.client_order_id,
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        payload_json = excluded.payload_json,
                        payload_sha256 = excluded.payload_sha256
                    """,
                    (
                        order.broker_order_id,
                        order.client_order_id,
                        order.status.value,
                        _iso(order.updated_at),
                        payload_json,
                        payload_hash,
                    ),
                )
            connection.execute("DELETE FROM broker_positions")
            for position in positions:
                payload_json, payload_hash = _payload(position.as_dict())
                connection.execute(
                    """
                    INSERT INTO broker_positions(symbol, payload_json, payload_sha256)
                    VALUES (?, ?, ?)
                    """,
                    (position.symbol.upper(), payload_json, payload_hash),
                )
            for fill in fills:
                payload_json, payload_hash = _payload(fill.as_dict())
                existing = connection.execute(
                    """
                    SELECT payload_json FROM broker_fills WHERE execution_id = ?
                    """,
                    (fill.execution_id,),
                ).fetchone()
                if existing is not None:
                    existing_json = str(existing["payload_json"])
                    if existing_json == payload_json:
                        continue
                    if _is_fill_ownership_enrichment(existing_json, payload_json):
                        connection.execute(
                            """
                            UPDATE broker_fills
                            SET client_order_id = ?, payload_json = ?, payload_sha256 = ?
                            WHERE execution_id = ?
                            """,
                            (
                                fill.client_order_id,
                                payload_json,
                                payload_hash,
                                fill.execution_id,
                            ),
                        )
                        continue
                    raise BrokerJournalConflict("duplicate execution ID has different fill content")
                connection.execute(
                    """
                    INSERT INTO broker_fills(
                        execution_id, activity_id, broker_order_id, client_order_id,
                        timestamp, payload_json, payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.execution_id,
                        fill.activity_id,
                        fill.broker_order_id,
                        fill.client_order_id,
                        _iso(fill.timestamp),
                        payload_json,
                        payload_hash,
                    ),
                )
            for client_order_id, broker_order_id in submission_resolutions.items():
                row = connection.execute(
                    "SELECT * FROM submissions WHERE client_order_id = ?",
                    (client_order_id,),
                ).fetchone()
                if row is None:
                    raise BrokerJournalConflict(
                        "reconciliation resolved an unknown client order ID"
                    )
                current = _submission_from_row(row)
                if current.state is SubmissionState.REJECTED:
                    raise BrokerJournalConflict(
                        "rejected submission cannot be reconciled to a broker order"
                    )
                if (
                    current.state is SubmissionState.RECONCILED
                    and current.broker_order_id != broker_order_id
                ):
                    raise BrokerJournalConflict("reconciled submission broker order ID changed")
                connection.execute(
                    """
                    UPDATE submissions
                    SET state = ?, updated_at = ?, broker_order_id = ?, reason = NULL
                    WHERE client_order_id = ?
                    """,
                    (
                        SubmissionState.RECONCILED.value,
                        _iso(timestamp),
                        broker_order_id,
                        client_order_id,
                    ),
                )
            if activity_checkpoint is not None:
                if not activity_checkpoint.strip():
                    raise BrokerJournalError("activity checkpoint must not be empty")
                connection.execute(
                    """
                    INSERT INTO checkpoints(key, value, updated_at)
                    VALUES ('fill_activity', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (activity_checkpoint, _iso(timestamp)),
                )
            report_json, report_hash = _payload(dict(report))
            cursor = connection.execute(
                """
                INSERT INTO reconciliation_reports(
                    timestamp, payload_json, payload_sha256
                ) VALUES (?, ?, ?)
                """,
                (_iso(timestamp), report_json, report_hash),
            )
            if cursor.lastrowid is None:
                raise BrokerJournalError("SQLite did not return a reconciliation sequence")
            connection.commit()
            return int(cursor.lastrowid)
        except BrokerJournalError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise BrokerJournalError("broker reconciliation transaction failed") from exc
        except Exception:
            connection.rollback()
            raise

    def all_fills(self) -> Sequence[BrokerFillActivity]:
        connection = self._require_connection()
        rows = connection.execute(
            """
            SELECT payload_json FROM broker_fills
            ORDER BY timestamp, execution_id
            """
        )
        return tuple(_fill_from_payload(json.loads(str(row["payload_json"]))) for row in rows)

    def activity_checkpoint(self) -> str | None:
        connection = self._require_connection()
        row = connection.execute(
            "SELECT value FROM checkpoints WHERE key = 'fill_activity'"
        ).fetchone()
        return None if row is None else str(row["value"])

    def integrity_check(self) -> str:
        connection = self._require_connection()
        row = connection.execute("PRAGMA integrity_check").fetchone()
        if row is None:
            raise BrokerJournalError("SQLite did not return an integrity result")
        return str(row[0])

    def close(self) -> None:
        if self._connection is not None:
            self._connection.commit()
            self._connection.close()
            self._connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise BrokerJournalError("broker journal is closed")
        return self._connection


def _prepare_secure_database_path(path: Path) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if parent.resolve(strict=True) != parent:
            raise BrokerJournalError(
                "broker journal parent path must not contain symlinks or traversal"
            )
        parent_metadata = parent.lstat()
    except OSError as exc:
        raise BrokerJournalError("broker journal parent directory is unavailable") from exc
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise BrokerJournalError("broker journal parent must be a directory")
    if stat.S_IMODE(parent_metadata.st_mode) & 0o022:
        raise BrokerJournalError("broker journal parent must not be group- or world-writable")
    _require_owner(parent_metadata.st_uid, "broker journal parent")

    try:
        database_metadata = path.lstat()
    except FileNotFoundError:
        if not hasattr(os, "O_CLOEXEC"):
            raise BrokerJournalError("secure broker journal creation is unavailable") from None
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC
        try:
            file_descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise BrokerJournalError("broker journal could not be created securely") from exc
        os.close(file_descriptor)
        return
    except OSError as exc:
        raise BrokerJournalError("broker journal path is unavailable") from exc

    if stat.S_ISLNK(database_metadata.st_mode):
        raise BrokerJournalError("broker journal must not be a symlink")
    if not stat.S_ISREG(database_metadata.st_mode):
        raise BrokerJournalError("broker journal must be a regular file")
    _require_owner(database_metadata.st_uid, "broker journal")
    if stat.S_IMODE(database_metadata.st_mode) & 0o077:
        raise BrokerJournalError("broker journal permissions must be 0600 or stricter")
    if database_metadata.st_size > 0:
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            file_descriptor = os.open(path, flags)
            try:
                header = os.read(file_descriptor, 16)
            finally:
                os.close(file_descriptor)
        except OSError as exc:
            raise BrokerJournalError("broker journal could not be inspected securely") from exc
        if header != b"SQLite format 3\x00":
            raise BrokerJournalError("broker journal has an invalid SQLite header")


def _require_owner(owner_uid: int, label: str) -> None:
    get_euid = getattr(os, "geteuid", None)
    if get_euid is not None and owner_uid != get_euid():
        raise BrokerJournalError(f"{label} is not owned by the service user")


def _submission_from_row(row: sqlite3.Row) -> SubmissionJournalEntry:
    return SubmissionJournalEntry(
        sequence=int(row["sequence"]),
        client_order_id=str(row["client_order_id"]),
        requested_payload_sha256=str(row["requested_payload_sha256"]),
        state=SubmissionState(str(row["state"])),
        created_at=_parse_datetime(str(row["created_at"])),
        updated_at=_parse_datetime(str(row["updated_at"])),
        broker_order_id=(
            str(row["broker_order_id"]) if row["broker_order_id"] is not None else None
        ),
        reason=str(row["reason"]) if row["reason"] is not None else None,
    )


def _fill_from_payload(payload: object) -> BrokerFillActivity:
    if not isinstance(payload, dict):
        raise BrokerJournalError("stored fill payload is not an object")
    data = cast(dict[str, object], payload)
    client_order_id = data.get("client_order_id")
    fee = data.get("fee")
    return BrokerFillActivity(
        activity_id=str(data["activity_id"]),
        execution_id=str(data["execution_id"]),
        broker_order_id=str(data["broker_order_id"]),
        client_order_id=(str(client_order_id) if client_order_id is not None else None),
        symbol=str(data["symbol"]),
        side=Side(str(data["side"])),
        quantity=Decimal(str(data["quantity"])),
        price=Decimal(str(data["price"])),
        fee=Decimal(str(fee)) if fee is not None else None,
        timestamp=_parse_datetime(str(data["timestamp"])),
        raw_payload_sha256=str(data["raw_payload_sha256"]),
    )


def _is_fill_ownership_enrichment(existing_json: str, candidate_json: str) -> bool:
    existing = json.loads(existing_json)
    candidate = json.loads(candidate_json)
    if not isinstance(existing, dict) or not isinstance(candidate, dict):
        return False
    if existing.get("client_order_id") is not None:
        return False
    if candidate.get("client_order_id") is None:
        return False
    existing_without_owner = dict(existing)
    candidate_without_owner = dict(candidate)
    existing_without_owner.pop("client_order_id", None)
    candidate_without_owner.pop("client_order_id", None)
    return existing_without_owner == candidate_without_owner


def _payload(value: Mapping[str, Any]) -> tuple[str, str]:
    payload_json = _canonical_json(value)
    return payload_json, hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )


def _json_default(value: object) -> str:
    if isinstance(value, (Decimal, datetime)):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _iso(value: datetime) -> str:
    _aware(value, "timestamp")
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _aware(parsed, "stored timestamp")
    return parsed.astimezone(UTC)
