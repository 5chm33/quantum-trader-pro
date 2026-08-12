"""Durable fail-closed operator pause and one-use action storage."""

from __future__ import annotations

import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path

from quantum_trader.domain.execution import ExecutionFingerprint
from quantum_trader.domain.operator import (
    OperatorAction,
    OperatorActionRecord,
    OperatorActionState,
    OperatorApproval,
    OperatorControlState,
    hash_operator_reason,
)


class OperatorControlError(RuntimeError):
    """The durable operator-control store could not complete an operation."""


class OperatorControlConflict(OperatorControlError):
    """An approval replay or incompatible state transition was rejected."""


class SQLiteOperatorControl:
    """Full-sync operator controls; a newly initialized store is always paused."""

    def __init__(
        self,
        path: Path,
        *,
        strategy_namespace: str,
        created_at: datetime | None = None,
    ) -> None:
        if not path.is_absolute():
            raise ValueError("operator-control database path must be absolute")
        namespace = strategy_namespace.strip()
        if not namespace:
            raise ValueError("strategy_namespace must not be empty")
        timestamp = created_at or datetime.now(UTC)
        _aware(timestamp, "created_at")
        _prepare_secure_database_path(path)
        self._path = path
        self._strategy_namespace = namespace
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            path,
            isolation_level=None,
        )
        connection = self._require_connection()
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        self._initialize(timestamp)

    def _initialize(self, timestamp: datetime) -> None:
        connection = self._require_connection()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO metadata(key, value)
            VALUES ('schema_version', '1');

            CREATE TABLE IF NOT EXISTS control_states (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                paused INTEGER NOT NULL CHECK(paused IN (0, 1)),
                changed_at TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                reason_sha256 TEXT NOT NULL,
                approval_id TEXT
            );

            CREATE TABLE IF NOT EXISTS action_records (
                approval_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                state TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                summary_sha256 TEXT
            );
            """
        )
        count = int(connection.execute("SELECT COUNT(*) FROM control_states").fetchone()[0])
        if count == 0:
            connection.execute(
                """
                INSERT INTO control_states(
                    paused, changed_at, reason_code, reason_sha256, approval_id
                ) VALUES (?, ?, ?, ?, NULL)
                """,
                (
                    1,
                    timestamp.isoformat(),
                    "fail_closed_startup",
                    hash_operator_reason("new operator store starts paused"),
                ),
            )

    def current_state(self) -> OperatorControlState:
        connection = self._require_connection()
        row = connection.execute(
            "SELECT * FROM control_states ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise OperatorControlError("operator-control state is missing")
        return _state_from_row(row)

    def pause(
        self,
        *,
        timestamp: datetime,
        reason_code: str,
        reason: str,
    ) -> OperatorControlState:
        _aware(timestamp, "timestamp")
        reason_sha256 = hash_operator_reason(reason)
        probe = OperatorControlState(
            paused=True,
            sequence=0,
            changed_at=timestamp,
            reason_code=reason_code,
            reason_sha256=reason_sha256,
        )
        connection = self._require_connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                INSERT INTO control_states(
                    paused, changed_at, reason_code, reason_sha256, approval_id
                ) VALUES (?, ?, ?, ?, NULL)
                """,
                (
                    1,
                    timestamp.isoformat(),
                    probe.reason_code,
                    reason_sha256,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return self.current_state()

    def resume(
        self,
        *,
        approval: OperatorApproval,
        expected_fingerprint: ExecutionFingerprint,
        control_key: bytes,
        timestamp: datetime,
        reason: str,
    ) -> OperatorControlState:
        approval.verify(
            expected_action=OperatorAction.RESUME,
            expected_namespace=self._strategy_namespace,
            expected_fingerprint=expected_fingerprint,
            control_key=control_key,
            now=timestamp,
        )
        reason_sha256 = hash_operator_reason(reason)
        connection = self._require_connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._require_unused_approval(connection, approval.approval_id)
            if not _current_state(connection).paused:
                raise OperatorControlConflict("operator controls are already resumed")
            if self._in_progress_count(connection) != 0:
                raise OperatorControlConflict("operator action is still in progress")
            connection.execute(
                """
                INSERT INTO action_records(
                    approval_id, action, state, started_at, updated_at, summary_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.action.value,
                    OperatorActionState.COMPLETED.value,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                    reason_sha256,
                ),
            )
            connection.execute(
                """
                INSERT INTO control_states(
                    paused, changed_at, reason_code, reason_sha256, approval_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    0,
                    timestamp.isoformat(),
                    "operator_resume",
                    reason_sha256,
                    approval.approval_id,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return self.current_state()

    def begin_action(
        self,
        *,
        approval: OperatorApproval,
        expected_action: OperatorAction,
        expected_fingerprint: ExecutionFingerprint,
        control_key: bytes,
        timestamp: datetime,
    ) -> OperatorActionRecord:
        if expected_action is OperatorAction.RESUME:
            raise ValueError("resume must use the dedicated resume transition")
        approval.verify(
            expected_action=expected_action,
            expected_namespace=self._strategy_namespace,
            expected_fingerprint=expected_fingerprint,
            control_key=control_key,
            now=timestamp,
        )
        connection = self._require_connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._require_unused_approval(connection, approval.approval_id)
            if not _current_state(connection).paused:
                raise OperatorControlConflict("kill actions require paused operator controls")
            if self._in_progress_count(connection) != 0:
                raise OperatorControlConflict("another operator action is in progress")
            connection.execute(
                """
                INSERT INTO action_records(
                    approval_id, action, state, started_at, updated_at, summary_sha256
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    approval.approval_id,
                    approval.action.value,
                    OperatorActionState.IN_PROGRESS.value,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        result = self.action_record(approval.approval_id)
        if result is None:
            raise OperatorControlError("operator action disappeared after commit")
        return result

    def complete_action(
        self,
        *,
        approval_id: str,
        succeeded: bool,
        timestamp: datetime,
        summary: str,
    ) -> OperatorActionRecord:
        _aware(timestamp, "timestamp")
        summary_sha256 = hash_operator_reason(summary)
        connection = self._require_connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT * FROM action_records WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise OperatorControlConflict("operator action does not exist")
            current = _action_from_row(row)
            if current.state is not OperatorActionState.IN_PROGRESS:
                raise OperatorControlConflict("operator action is not in progress")
            state = OperatorActionState.COMPLETED if succeeded else OperatorActionState.FAILED
            connection.execute(
                """
                UPDATE action_records
                SET state = ?, updated_at = ?, summary_sha256 = ?
                WHERE approval_id = ?
                """,
                (
                    state.value,
                    timestamp.isoformat(),
                    summary_sha256,
                    approval_id,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        result = self.action_record(approval_id)
        if result is None:
            raise OperatorControlError("operator action disappeared after completion")
        return result

    def action_record(self, approval_id: str) -> OperatorActionRecord | None:
        connection = self._require_connection()
        row = connection.execute(
            "SELECT * FROM action_records WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        return _action_from_row(row) if row is not None else None

    def integrity_check(self) -> str:
        connection = self._require_connection()
        row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0])

    def close(self) -> None:
        connection = self._connection
        if connection is not None:
            connection.close()
            self._connection = None

    def _require_unused_approval(
        self,
        connection: sqlite3.Connection,
        approval_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT 1 FROM action_records WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if row is not None:
            raise OperatorControlConflict("operator approval has already been consumed")

    @staticmethod
    def _in_progress_count(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COUNT(*) FROM action_records WHERE state = ?",
            (OperatorActionState.IN_PROGRESS.value,),
        ).fetchone()
        return int(row[0])

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise OperatorControlError("operator-control store is closed")
        return self._connection


def _current_state(connection: sqlite3.Connection) -> OperatorControlState:
    row = connection.execute(
        "SELECT * FROM control_states ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise OperatorControlError("operator-control state is missing")
    return _state_from_row(row)


def _state_from_row(row: sqlite3.Row) -> OperatorControlState:
    return OperatorControlState(
        paused=bool(row["paused"]),
        sequence=int(row["sequence"]),
        changed_at=datetime.fromisoformat(str(row["changed_at"])),
        reason_code=str(row["reason_code"]),
        reason_sha256=str(row["reason_sha256"]),
        approval_id=(str(row["approval_id"]) if row["approval_id"] is not None else None),
    )


def _action_from_row(row: sqlite3.Row) -> OperatorActionRecord:
    return OperatorActionRecord(
        approval_id=str(row["approval_id"]),
        action=OperatorAction(str(row["action"])),
        state=OperatorActionState(str(row["state"])),
        started_at=datetime.fromisoformat(str(row["started_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        summary_sha256=(str(row["summary_sha256"]) if row["summary_sha256"] is not None else None),
    )


def _prepare_secure_database_path(path: Path) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if parent.resolve(strict=True) != parent:
            raise OperatorControlError(
                "operator-control parent path must not contain symlinks or traversal"
            )
        parent_metadata = parent.lstat()
    except OSError as exc:
        raise OperatorControlError("operator-control parent directory is unavailable") from exc
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise OperatorControlError("operator-control parent must be a directory")
    if stat.S_IMODE(parent_metadata.st_mode) & 0o022:
        raise OperatorControlError("operator-control parent must not be group- or world-writable")
    _require_owner(parent_metadata.st_uid, "operator-control parent")

    try:
        database_metadata = path.lstat()
    except FileNotFoundError:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC
        try:
            file_descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise OperatorControlError(
                "operator-control database could not be created securely"
            ) from exc
        os.close(file_descriptor)
        return
    except OSError as exc:
        raise OperatorControlError("operator-control database path is unavailable") from exc

    if stat.S_ISLNK(database_metadata.st_mode):
        raise OperatorControlError("operator-control database must not be a symlink")
    if not stat.S_ISREG(database_metadata.st_mode):
        raise OperatorControlError("operator-control database must be a regular file")
    _require_owner(database_metadata.st_uid, "operator-control database")
    if stat.S_IMODE(database_metadata.st_mode) & 0o077:
        raise OperatorControlError("operator-control database permissions must be 0600 or stricter")


def _require_owner(owner_uid: int, label: str) -> None:
    get_euid = getattr(os, "geteuid", None)
    if get_euid is not None and owner_uid != get_euid():
        raise OperatorControlError(f"{label} is not owned by the service user")


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
