from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from quantum_trader.domain.execution import ExecutionFingerprint, ExecutionMode
from quantum_trader.domain.operator import (
    ACKNOWLEDGEMENTS,
    OperatorAction,
    OperatorActionRecord,
    OperatorActionState,
    OperatorApproval,
    OperatorControlState,
    hash_operator_reason,
)

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
KEY = b"k" * 32
FINGERPRINT = ExecutionFingerprint("a" * 64, "b" * 64, "c" * 64)
NONCE = "d" * 32


def approval(action: OperatorAction = OperatorAction.RESUME) -> OperatorApproval:
    return OperatorApproval.issue(
        action=action,
        strategy_namespace="qtpro-paper",
        fingerprint=FINGERPRINT,
        issued_at=NOW,
        ttl=timedelta(minutes=5),
        nonce=NONCE,
        acknowledgement=ACKNOWLEDGEMENTS[action],
        control_key=KEY,
    )


def test_operator_approval_is_action_specific_expiring_and_hmac_bound() -> None:
    record = approval(OperatorAction.CANCEL_OWNED_ORDERS)
    record.verify(
        expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
        expected_namespace="qtpro-paper",
        expected_fingerprint=FINGERPRINT,
        control_key=KEY,
        now=NOW + timedelta(minutes=1),
    )
    serialized = record.as_dict()
    assert serialized["environment"] == "paper"
    assert ACKNOWLEDGEMENTS[OperatorAction.CANCEL_OWNED_ORDERS] not in str(serialized)
    assert KEY.hex() not in str(serialized)

    with pytest.raises(ValueError, match="action does not match"):
        record.verify(
            expected_action=OperatorAction.FLATTEN_OWNED_POSITIONS,
            expected_namespace="qtpro-paper",
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="namespace"):
        record.verify(
            expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
            expected_namespace="other",
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="code fingerprint"):
        record.verify(
            expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
            expected_namespace="qtpro-paper",
            expected_fingerprint=replace(FINGERPRINT, code_sha256="e" * 64),
            control_key=KEY,
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="signature"):
        replace(record, signature_sha256="f" * 64).verify(
            expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
            expected_namespace="qtpro-paper",
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="currently active"):
        record.verify(
            expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
            expected_namespace="qtpro-paper",
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            now=NOW + timedelta(minutes=5),
        )


def test_operator_approval_creation_rejects_weak_or_ambiguous_input() -> None:
    with pytest.raises(ValueError, match="acknowledgement"):
        OperatorApproval.issue(
            action=OperatorAction.RESUME,
            strategy_namespace="qtpro-paper",
            fingerprint=FINGERPRINT,
            issued_at=NOW,
            ttl=timedelta(minutes=1),
            nonce=NONCE,
            acknowledgement="resume",
            control_key=KEY,
        )
    with pytest.raises(ValueError, match="10 minutes"):
        OperatorApproval.issue(
            action=OperatorAction.RESUME,
            strategy_namespace="qtpro-paper",
            fingerprint=FINGERPRINT,
            issued_at=NOW,
            ttl=timedelta(minutes=11),
            nonce=NONCE,
            acknowledgement=ACKNOWLEDGEMENTS[OperatorAction.RESUME],
            control_key=KEY,
        )
    with pytest.raises(ValueError, match="nonce"):
        OperatorApproval.issue(
            action=OperatorAction.RESUME,
            strategy_namespace="qtpro-paper",
            fingerprint=FINGERPRINT,
            issued_at=NOW,
            ttl=timedelta(minutes=1),
            nonce="bad",
            acknowledgement=ACKNOWLEDGEMENTS[OperatorAction.RESUME],
            control_key=KEY,
        )
    with pytest.raises(ValueError, match="32 bytes"):
        OperatorApproval.issue(
            action=OperatorAction.RESUME,
            strategy_namespace="qtpro-paper",
            fingerprint=FINGERPRINT,
            issued_at=NOW,
            ttl=timedelta(minutes=1),
            nonce=NONCE,
            acknowledgement=ACKNOWLEDGEMENTS[OperatorAction.RESUME],
            control_key=b"short",
        )
    with pytest.raises(ValueError, match="paper-only"):
        replace(approval(), environment=ExecutionMode.LIVE)


def test_operator_state_and_action_records_enforce_terminal_contracts() -> None:
    state = OperatorControlState(
        paused=True,
        sequence=0,
        changed_at=NOW,
        reason_code="fail_closed_startup",
        reason_sha256=hash_operator_reason("startup"),
    )
    assert state.as_dict()["paused"] is True
    with pytest.raises(ValueError, match="sequence"):
        replace(state, sequence=-1)
    with pytest.raises(ValueError, match="reason_code"):
        replace(state, reason_code="bad code!")
    with pytest.raises(ValueError, match="reason"):
        hash_operator_reason(" ")

    active = OperatorActionRecord(
        approval_id="a" * 64,
        action=OperatorAction.CANCEL_OWNED_ORDERS,
        state=OperatorActionState.IN_PROGRESS,
        started_at=NOW,
        updated_at=NOW,
    )
    assert active.as_dict()["state"] == "in_progress"
    with pytest.raises(ValueError, match="terminal"):
        replace(active, state=OperatorActionState.COMPLETED)
    with pytest.raises(ValueError, match="nonterminal"):
        replace(active, summary_sha256="b" * 64)
    with pytest.raises(ValueError, match="precedes"):
        replace(active, updated_at=NOW - timedelta(seconds=1))
