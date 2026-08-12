from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quantum_trader.adapters.sqlite_operator_control import (
    OperatorControlConflict,
    OperatorControlError,
    SQLiteOperatorControl,
)
from quantum_trader.domain.execution import ExecutionFingerprint
from quantum_trader.domain.operator import (
    ACKNOWLEDGEMENTS,
    OperatorAction,
    OperatorActionState,
    OperatorApproval,
)

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
KEY = b"k" * 32
FINGERPRINT = ExecutionFingerprint("a" * 64, "b" * 64, "c" * 64)


def approval(action: OperatorAction, nonce_character: str) -> OperatorApproval:
    return OperatorApproval.issue(
        action=action,
        strategy_namespace="qtpro-paper",
        fingerprint=FINGERPRINT,
        issued_at=NOW,
        ttl=timedelta(minutes=5),
        nonce=nonce_character * 32,
        acknowledgement=ACKNOWLEDGEMENTS[action],
        control_key=KEY,
    )


def store(tmp_path: Path) -> SQLiteOperatorControl:
    return SQLiteOperatorControl(
        (tmp_path / "operator.db").resolve(),
        strategy_namespace="qtpro-paper",
        created_at=NOW,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership, mode, and symlink contract")
def test_operator_database_rejects_unsafe_paths_and_permissions(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        SQLiteOperatorControl(
            Path("relative.db"),
            strategy_namespace="qtpro-paper",
            created_at=NOW,
        )

    writable_parent = tmp_path / "writable"
    writable_parent.mkdir(mode=0o700)
    writable_parent.chmod(0o777)
    try:
        with pytest.raises(OperatorControlError, match="writable"):
            SQLiteOperatorControl(
                writable_parent / "operator.db",
                strategy_namespace="qtpro-paper",
                created_at=NOW,
            )
    finally:
        writable_parent.chmod(0o700)

    broad_parent = tmp_path / "broad"
    broad_parent.mkdir(mode=0o700)
    broad_database = broad_parent / "operator.db"
    broad_database.write_bytes(b"placeholder")
    broad_database.chmod(0o644)
    with pytest.raises(OperatorControlError, match="0600"):
        SQLiteOperatorControl(
            broad_database,
            strategy_namespace="qtpro-paper",
            created_at=NOW,
        )

    directory_parent = tmp_path / "directory-file"
    directory_parent.mkdir(mode=0o700)
    (directory_parent / "operator.db").mkdir(mode=0o700)
    with pytest.raises(OperatorControlError, match="regular"):
        SQLiteOperatorControl(
            directory_parent / "operator.db",
            strategy_namespace="qtpro-paper",
            created_at=NOW,
        )

    symlink_parent_target = tmp_path / "real-parent"
    symlink_parent_target.mkdir(mode=0o700)
    symlink_parent = tmp_path / "linked-parent"
    symlink_parent.symlink_to(symlink_parent_target, target_is_directory=True)
    with pytest.raises(OperatorControlError, match="symlinks"):
        SQLiteOperatorControl(
            symlink_parent / "operator.db",
            strategy_namespace="qtpro-paper",
            created_at=NOW,
        )

    symlink_database_parent = tmp_path / "symlink-database"
    symlink_database_parent.mkdir(mode=0o700)
    target = tmp_path / "target.db"
    target.write_bytes(b"target")
    target.chmod(0o600)
    (symlink_database_parent / "operator.db").symlink_to(target)
    with pytest.raises(OperatorControlError, match="symlink"):
        SQLiteOperatorControl(
            symlink_database_parent / "operator.db",
            strategy_namespace="qtpro-paper",
            created_at=NOW,
        )


def test_new_store_is_mode_0600_integral_and_paused_by_default(tmp_path: Path) -> None:
    control = store(tmp_path)
    state = control.current_state()
    assert state.paused is True
    assert state.reason_code == "fail_closed_startup"
    assert state.sequence == 1
    assert control.integrity_check() == "ok"
    assert stat.S_IMODE((tmp_path / "operator.db").stat().st_mode) == 0o600

    reason = "operator saw unexpected account activity"
    paused = control.pause(
        timestamp=NOW + timedelta(seconds=1),
        reason_code="manual_pause",
        reason=reason,
    )
    assert paused.paused is True
    assert paused.sequence == 2
    assert reason.encode() not in (tmp_path / "operator.db").read_bytes()


def test_resume_consumes_one_approval_and_replay_is_rejected(tmp_path: Path) -> None:
    control = store(tmp_path)
    resume = approval(OperatorAction.RESUME, "1")
    state = control.resume(
        approval=resume,
        expected_fingerprint=FINGERPRINT,
        control_key=KEY,
        timestamp=NOW + timedelta(minutes=1),
        reason="preflight and reconciliation passed",
    )
    assert state.paused is False
    assert state.approval_id == resume.approval_id
    record = control.action_record(resume.approval_id)
    assert record is not None
    assert record.state is OperatorActionState.COMPLETED

    with pytest.raises(OperatorControlConflict, match="consumed"):
        control.resume(
            approval=resume,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW + timedelta(minutes=2),
            reason="replay",
        )


def test_kill_action_requires_pause_blocks_resume_and_has_one_terminal_update(
    tmp_path: Path,
) -> None:
    control = store(tmp_path)
    cancel = approval(OperatorAction.CANCEL_OWNED_ORDERS, "2")
    started = control.begin_action(
        approval=cancel,
        expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
        expected_fingerprint=FINGERPRINT,
        control_key=KEY,
        timestamp=NOW + timedelta(minutes=1),
    )
    assert started.state is OperatorActionState.IN_PROGRESS

    resume = approval(OperatorAction.RESUME, "3")
    with pytest.raises(OperatorControlConflict, match="in progress"):
        control.resume(
            approval=resume,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW + timedelta(minutes=1, seconds=1),
            reason="must wait",
        )

    completed = control.complete_action(
        approval_id=cancel.approval_id,
        succeeded=True,
        timestamp=NOW + timedelta(minutes=1, seconds=2),
        summary="all owned paper orders terminal",
    )
    assert completed.state is OperatorActionState.COMPLETED
    assert completed.summary_sha256 is not None
    with pytest.raises(OperatorControlConflict, match="not in progress"):
        control.complete_action(
            approval_id=cancel.approval_id,
            succeeded=False,
            timestamp=NOW + timedelta(minutes=1, seconds=3),
            summary="duplicate terminal update",
        )
    with pytest.raises(OperatorControlConflict, match="consumed"):
        control.begin_action(
            approval=cancel,
            expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW + timedelta(minutes=2),
        )


def test_kill_action_requires_paused_state_and_correct_action(tmp_path: Path) -> None:
    control = store(tmp_path)
    resume = approval(OperatorAction.RESUME, "4")
    control.resume(
        approval=resume,
        expected_fingerprint=FINGERPRINT,
        control_key=KEY,
        timestamp=NOW + timedelta(minutes=1),
        reason="resume",
    )
    cancel = approval(OperatorAction.CANCEL_OWNED_ORDERS, "5")
    with pytest.raises(OperatorControlConflict, match="paused"):
        control.begin_action(
            approval=cancel,
            expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW + timedelta(minutes=2),
        )

    control.pause(
        timestamp=NOW + timedelta(minutes=2, seconds=1),
        reason_code="manual_pause",
        reason="pause",
    )
    with pytest.raises(ValueError, match="dedicated resume"):
        control.begin_action(
            approval=approval(OperatorAction.RESUME, "6"),
            expected_action=OperatorAction.RESUME,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW + timedelta(minutes=3),
        )
    with pytest.raises(ValueError, match="signature"):
        control.begin_action(
            approval=cancel,
            expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
            expected_fingerprint=FINGERPRINT,
            control_key=b"z" * 32,
            timestamp=NOW + timedelta(minutes=3),
        )
    assert control.current_state().paused is True


def test_failed_action_is_terminal_and_closed_store_rejects_access(tmp_path: Path) -> None:
    control = store(tmp_path)
    flatten = approval(OperatorAction.FLATTEN_OWNED_POSITIONS, "7")
    control.begin_action(
        approval=flatten,
        expected_action=OperatorAction.FLATTEN_OWNED_POSITIONS,
        expected_fingerprint=FINGERPRINT,
        control_key=KEY,
        timestamp=NOW + timedelta(minutes=1),
    )
    failed = control.complete_action(
        approval_id=flatten.approval_id,
        succeeded=False,
        timestamp=NOW + timedelta(minutes=1, seconds=1),
        summary="flatten remains intentionally unimplemented",
    )
    assert failed.state is OperatorActionState.FAILED
    assert control.action_record("f" * 64) is None

    control.close()
    with pytest.raises(OperatorControlError, match="closed"):
        control.current_state()
    with pytest.raises(OperatorControlError, match="closed"):
        control.integrity_check()


def test_in_progress_kill_action_survives_restart_and_fails_closed(tmp_path: Path) -> None:
    path = (tmp_path / "operator.db").resolve()
    control = store(tmp_path)
    cancel = approval(OperatorAction.CANCEL_OWNED_ORDERS, "9")
    started = control.begin_action(
        approval=cancel,
        expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
        expected_fingerprint=FINGERPRINT,
        control_key=KEY,
        timestamp=NOW + timedelta(minutes=1),
    )
    assert started.state is OperatorActionState.IN_PROGRESS
    control.close()

    reopened = SQLiteOperatorControl(
        path,
        strategy_namespace="qtpro-paper",
        created_at=NOW + timedelta(minutes=2),
    )
    retained = reopened.action_record(cancel.approval_id)
    assert retained is not None
    assert retained.state is OperatorActionState.IN_PROGRESS
    assert reopened.current_state().paused is True
    with pytest.raises(OperatorControlConflict, match="in progress"):
        reopened.resume(
            approval=approval(OperatorAction.RESUME, "8"),
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW + timedelta(minutes=2),
            reason="restart must not resume around an incomplete action",
        )
    with pytest.raises(OperatorControlConflict, match="consumed"):
        reopened.begin_action(
            approval=cancel,
            expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW + timedelta(minutes=2),
        )
    failed = reopened.complete_action(
        approval_id=cancel.approval_id,
        succeeded=False,
        timestamp=NOW + timedelta(minutes=2, seconds=1),
        summary="operator process restarted during kill action",
    )
    assert failed.state is OperatorActionState.FAILED
    assert reopened.current_state().paused is True
    with pytest.raises(OperatorControlConflict, match="not in progress"):
        reopened.complete_action(
            approval_id=cancel.approval_id,
            succeeded=False,
            timestamp=NOW + timedelta(minutes=2, seconds=2),
            summary="duplicate completion",
        )
    reopened.close()
