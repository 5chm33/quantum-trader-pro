"""Durable operator pause, approval, and kill-action contract."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from quantum_trader.domain.execution import ExecutionFingerprint
from quantum_trader.domain.operator import (
    OperatorAction,
    OperatorActionRecord,
    OperatorApproval,
    OperatorControlState,
)


class OperatorControlReader(Protocol):
    def current_state(self) -> OperatorControlState:
        """Return the latest durable pause state."""


class OperatorControlStore(OperatorControlReader, Protocol):
    def pause(
        self,
        *,
        timestamp: datetime,
        reason_code: str,
        reason: str,
    ) -> OperatorControlState:
        """Immediately disable new paper orders without requiring approval."""

    def resume(
        self,
        *,
        approval: OperatorApproval,
        expected_fingerprint: ExecutionFingerprint,
        control_key: bytes,
        timestamp: datetime,
        reason: str,
    ) -> OperatorControlState:
        """Consume one verified resume approval and enable paper assessments."""

    def begin_action(
        self,
        *,
        approval: OperatorApproval,
        expected_action: OperatorAction,
        expected_fingerprint: ExecutionFingerprint,
        control_key: bytes,
        timestamp: datetime,
    ) -> OperatorActionRecord:
        """Consume one cancel or flatten approval while the system is paused."""

    def complete_action(
        self,
        *,
        approval_id: str,
        succeeded: bool,
        timestamp: datetime,
        summary: str,
    ) -> OperatorActionRecord:
        """Finalize one in-progress operator action with a hashed summary."""

    def action_record(self, approval_id: str) -> OperatorActionRecord | None:
        """Return one durable operator action record when present."""

    def integrity_check(self) -> str:
        """Return SQLite-style integrity status or an equivalent result."""

    def close(self) -> None:
        """Flush and close the operator-control store."""
