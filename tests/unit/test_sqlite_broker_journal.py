from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantum_trader.adapters.sqlite_broker_journal import (
    BrokerJournalConflict,
    BrokerJournalError,
    SQLiteBrokerJournal,
)
from quantum_trader.domain.brokerage import (
    AccountStatus,
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerFillActivity,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerPositionSnapshot,
    SubmissionState,
    TimeInForce,
)
from quantum_trader.domain.execution import (
    PAPER_ACKNOWLEDGEMENT,
    ArmingRecord,
    BrokerPreflight,
    ExecutionFingerprint,
    ExecutionGate,
    ExecutionMode,
)
from quantum_trader.domain.models import OrderIntent, RiskDecision, Side

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
A = "a" * 64
B = "b" * 64
C = "c" * 64
RAW = "d" * 64


def approved_order() -> ApprovedBrokerOrder:
    fingerprint = ExecutionFingerprint(A, B, C)
    record = ArmingRecord.issue_paper(
        strategy_namespace="qtpro-paper",
        fingerprint=fingerprint,
        issued_at=NOW,
        ttl=timedelta(hours=1),
        acknowledgement=PAPER_ACKNOWLEDGEMENT,
    )
    context = ExecutionGate.arm_paper(
        requested_mode=ExecutionMode.PAPER,
        record=record,
        expected_namespace="qtpro-paper",
        expected_fingerprint=fingerprint,
        preflight=BrokerPreflight(
            environment_verified=True,
            account_verified=True,
            account_active=True,
            account_unblocked=True,
            reconciliation_complete=True,
            broker_clock_verified=True,
            market_data_fresh=True,
            durable_journal_ready=True,
            secret_source_secure=True,
        ),
        now=NOW + timedelta(minutes=1),
    )
    intent = OrderIntent.create(
        correlation_id="journal-correlation",
        timestamp=NOW,
        symbol="SPY",
        side=Side.BUY,
        quantity=4,
        reference_price=Decimal("500"),
        rationale="journal fixture",
    )
    decision = RiskDecision(
        allowed=True,
        reason="within_limits",
        approved_quantity=4,
        intent_id=intent.intent_id,
        correlation_id=intent.correlation_id,
    )
    return ApprovedBrokerOrder.from_approved_intent(
        context=context,
        intent=intent,
        decision=decision,
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        limit_price=Decimal("499.50"),
    )


def account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        environment=ExecutionMode.PAPER,
        account_sha256=C,
        status=AccountStatus.ACTIVE,
        trading_blocked=False,
        account_blocked=False,
        transfers_blocked=False,
        cash=Decimal("8000"),
        equity=Decimal("10000"),
        buying_power=Decimal("8000"),
        captured_at=NOW,
        raw_payload_sha256=RAW,
    )


def order_snapshot(order: ApprovedBrokerOrder) -> BrokerOrderSnapshot:
    return BrokerOrderSnapshot(
        broker_order_id="broker-order-1",
        client_order_id=order.client_order_id,
        status=BrokerOrderStatus.FILLED,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("4"),
        filled_quantity=Decimal("4"),
        average_fill_price=Decimal("500"),
        submitted_at=NOW,
        updated_at=NOW + timedelta(seconds=2),
        raw_payload_sha256=RAW,
    )


def position() -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        symbol="SPY",
        quantity=Decimal("4"),
        average_entry_price=Decimal("500"),
        market_price=Decimal("501"),
        market_value=Decimal("2004"),
        unrealized_pnl=Decimal("4"),
        captured_at=NOW + timedelta(seconds=3),
        raw_payload_sha256=RAW,
    )


def fill(order: ApprovedBrokerOrder) -> BrokerFillActivity:
    return BrokerFillActivity(
        activity_id="20260812143000000::execution-1",
        execution_id="20260812143000000::execution-1",
        broker_order_id="broker-order-1",
        client_order_id=order.client_order_id,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("4"),
        price=Decimal("500"),
        fee=None,
        timestamp=NOW + timedelta(seconds=2),
        raw_payload_sha256=RAW,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership, mode, and symlink contract")
def test_broker_journal_rejects_unsafe_paths_and_permissions(tmp_path: Path) -> None:
    with pytest.raises(BrokerJournalError, match="absolute"):
        SQLiteBrokerJournal(Path("relative.db"))

    writable_parent = tmp_path / "writable"
    writable_parent.mkdir(mode=0o700)
    writable_parent.chmod(0o777)
    try:
        with pytest.raises(BrokerJournalError, match="writable"):
            SQLiteBrokerJournal(writable_parent / "broker.db")
    finally:
        writable_parent.chmod(0o700)

    broad_parent = tmp_path / "broad"
    broad_parent.mkdir(mode=0o700)
    broad_database = broad_parent / "broker.db"
    broad_database.write_bytes(b"placeholder")
    broad_database.chmod(0o644)
    with pytest.raises(BrokerJournalError, match="0600"):
        SQLiteBrokerJournal(broad_database)

    corrupt_parent = tmp_path / "corrupt"
    corrupt_parent.mkdir(mode=0o700)
    corrupt_database = corrupt_parent / "broker.db"
    corrupt_database.write_bytes(b"not a sqlite database")
    corrupt_database.chmod(0o600)
    with pytest.raises(BrokerJournalError, match="invalid SQLite header"):
        SQLiteBrokerJournal(corrupt_database)

    directory_parent = tmp_path / "directory-file"
    directory_parent.mkdir(mode=0o700)
    (directory_parent / "broker.db").mkdir(mode=0o700)
    with pytest.raises(BrokerJournalError, match="regular"):
        SQLiteBrokerJournal(directory_parent / "broker.db")

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(BrokerJournalError, match="symlinks"):
        SQLiteBrokerJournal(linked_parent / "broker.db")

    symlink_parent = tmp_path / "symlink-database"
    symlink_parent.mkdir(mode=0o700)
    target = tmp_path / "target.db"
    target.write_bytes(b"target")
    target.chmod(0o600)
    (symlink_parent / "broker.db").symlink_to(target)
    with pytest.raises(BrokerJournalError, match="symlink"):
        SQLiteBrokerJournal(symlink_parent / "broker.db")


def test_pre_submit_persistence_is_idempotent_and_transition_validated(tmp_path) -> None:
    path = tmp_path / "broker.db"
    journal = SQLiteBrokerJournal(path)
    order = approved_order()

    persisted = journal.persist_approved_order(
        order=order,
        requested_payload_sha256=A,
        timestamp=NOW,
    )
    repeated = journal.persist_approved_order(
        order=order,
        requested_payload_sha256=A,
        timestamp=NOW,
    )
    assert repeated == persisted
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert journal.known_client_order_ids() == frozenset({order.client_order_id})
    assert journal.submission_timestamps() == (NOW,)

    with pytest.raises(BrokerJournalConflict, match="different approved content"):
        journal.persist_approved_order(
            order=order,
            requested_payload_sha256=B,
            timestamp=NOW,
        )

    started = journal.transition_submission(
        client_order_id=order.client_order_id,
        state=SubmissionState.STARTED,
        timestamp=NOW + timedelta(seconds=1),
    )
    assert started.state is SubmissionState.STARTED
    acknowledged = journal.transition_submission(
        client_order_id=order.client_order_id,
        state=SubmissionState.ACKNOWLEDGED,
        timestamp=NOW + timedelta(seconds=2),
        broker_order_id="broker-order-1",
    )
    assert acknowledged.broker_order_id == "broker-order-1"
    assert (
        journal.transition_submission(
            client_order_id=order.client_order_id,
            state=SubmissionState.ACKNOWLEDGED,
            timestamp=NOW + timedelta(seconds=2),
            broker_order_id="broker-order-1",
        )
        == acknowledged
    )

    with pytest.raises(BrokerJournalConflict, match="invalid submission transition"):
        journal.transition_submission(
            client_order_id=order.client_order_id,
            state=SubmissionState.STARTED,
            timestamp=NOW + timedelta(seconds=3),
        )
    with pytest.raises(BrokerJournalError, match="does not exist"):
        journal.transition_submission(
            client_order_id="qt-missing-fixture",
            state=SubmissionState.STARTED,
            timestamp=NOW,
        )

    unresolved = journal.unresolved_submissions()
    assert len(unresolved) == 1
    assert unresolved[0].state is SubmissionState.ACKNOWLEDGED
    assert journal.integrity_check() == "ok"
    journal.close()
    with pytest.raises(BrokerJournalError, match="closed"):
        journal.integrity_check()
    with pytest.raises(BrokerJournalError, match="closed"):
        journal.submission_timestamps()


def test_reconciliation_is_atomic_deduplicated_and_checkpointed(tmp_path) -> None:
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    approved = approved_order()
    journal.persist_approved_order(
        order=approved,
        requested_payload_sha256=A,
        timestamp=NOW,
    )
    journal.transition_submission(
        client_order_id=approved.client_order_id,
        state=SubmissionState.STARTED,
        timestamp=NOW + timedelta(seconds=1),
    )
    broker_order = order_snapshot(approved)
    broker_fill = fill(approved)
    checkpoint = broker_fill.activity_id

    sequence = journal.apply_reconciliation(
        account=account(),
        orders=(broker_order,),
        positions=(position(),),
        fills=(broker_fill,),
        submission_resolutions={approved.client_order_id: broker_order.broker_order_id},
        activity_checkpoint=checkpoint,
        timestamp=NOW + timedelta(seconds=4),
        report={"ready": True, "issues": []},
    )
    assert sequence == 1
    assert journal.activity_checkpoint() == checkpoint
    assert len(journal.all_fills()) == 1
    assert journal.unresolved_submissions() == ()

    second_sequence = journal.apply_reconciliation(
        account=account(),
        orders=(broker_order,),
        positions=(position(),),
        fills=(broker_fill,),
        submission_resolutions={approved.client_order_id: broker_order.broker_order_id},
        activity_checkpoint=checkpoint,
        timestamp=NOW + timedelta(seconds=5),
        report={"ready": True, "issues": []},
    )
    assert second_sequence == 2
    assert len(journal.all_fills()) == 1

    conflicting_fill = replace(
        broker_fill,
        price=Decimal("501"),
        raw_payload_sha256=hashlib.sha256(b"changed-fill").hexdigest(),
    )
    with pytest.raises(BrokerJournalConflict, match="different fill content"):
        journal.apply_reconciliation(
            account=account(),
            orders=(broker_order,),
            positions=(),
            fills=(conflicting_fill,),
            submission_resolutions={},
            activity_checkpoint="later-checkpoint",
            timestamp=NOW + timedelta(seconds=6),
            report={"ready": False},
        )
    assert journal.activity_checkpoint() == checkpoint
    assert len(journal.all_fills()) == 1

    stale_order = replace(
        broker_order,
        updated_at=NOW,
        raw_payload_sha256=hashlib.sha256(b"stale-order").hexdigest(),
    )
    with pytest.raises(BrokerJournalConflict, match="moved backwards"):
        journal.apply_reconciliation(
            account=account(),
            orders=(stale_order,),
            positions=(position(),),
            fills=(),
            submission_resolutions={},
            activity_checkpoint=checkpoint,
            timestamp=NOW + timedelta(seconds=7),
            report={"ready": False},
        )
    assert journal.integrity_check() == "ok"
    journal.close()


def test_injected_mid_reconciliation_failure_rolls_back_and_clean_retry_succeeds(
    tmp_path,
) -> None:
    path = tmp_path / "broker.db"
    journal = SQLiteBrokerJournal(path)
    approved = approved_order()
    journal.persist_approved_order(
        order=approved,
        requested_payload_sha256=A,
        timestamp=NOW,
    )
    journal.transition_submission(
        client_order_id=approved.client_order_id,
        state=SubmissionState.STARTED,
        timestamp=NOW + timedelta(seconds=1),
    )
    fault_connection = sqlite3.connect(path)
    fault_connection.execute(
        """
        CREATE TRIGGER injected_position_failure
        BEFORE INSERT ON broker_positions
        BEGIN
            SELECT RAISE(ABORT, 'injected position failure');
        END;
        """
    )
    fault_connection.commit()
    fault_connection.close()

    broker_order = order_snapshot(approved)
    broker_fill = fill(approved)
    with pytest.raises(BrokerJournalError, match="transaction failed"):
        journal.apply_reconciliation(
            account=account(),
            orders=(broker_order,),
            positions=(position(),),
            fills=(broker_fill,),
            submission_resolutions={approved.client_order_id: broker_order.broker_order_id},
            activity_checkpoint=broker_fill.activity_id,
            timestamp=NOW + timedelta(seconds=4),
            report={"ready": True, "issues": []},
        )
    assert journal.activity_checkpoint() is None
    assert journal.all_fills() == ()
    unresolved = journal.unresolved_submissions()
    assert len(unresolved) == 1
    assert unresolved[0].state is SubmissionState.STARTED
    assert journal.integrity_check() == "ok"

    fault_connection = sqlite3.connect(path)
    fault_connection.execute("DROP TRIGGER injected_position_failure")
    fault_connection.commit()
    fault_connection.close()
    sequence = journal.apply_reconciliation(
        account=account(),
        orders=(broker_order,),
        positions=(position(),),
        fills=(broker_fill,),
        submission_resolutions={approved.client_order_id: broker_order.broker_order_id},
        activity_checkpoint=broker_fill.activity_id,
        timestamp=NOW + timedelta(seconds=5),
        report={"ready": True, "issues": []},
    )
    assert sequence == 1
    assert journal.activity_checkpoint() == broker_fill.activity_id
    assert len(journal.all_fills()) == 1
    assert journal.unresolved_submissions() == ()
    journal.close()


def test_duplicate_fill_can_enrich_missing_client_ownership_once(tmp_path) -> None:
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    approved = approved_order()
    unresolved = replace(fill(approved), client_order_id=None)
    journal.apply_reconciliation(
        account=account(),
        orders=(),
        positions=(),
        fills=(unresolved,),
        submission_resolutions={},
        activity_checkpoint=unresolved.activity_id,
        timestamp=NOW + timedelta(seconds=1),
        report={"ready": False},
    )
    assert journal.all_fills()[0].client_order_id is None

    resolved = replace(unresolved, client_order_id=approved.client_order_id)
    journal.apply_reconciliation(
        account=account(),
        orders=(),
        positions=(),
        fills=(resolved,),
        submission_resolutions={},
        activity_checkpoint=resolved.activity_id,
        timestamp=NOW + timedelta(seconds=2),
        report={"ready": True},
    )
    assert journal.all_fills()[0].client_order_id == approved.client_order_id

    conflicting_owner = replace(resolved, client_order_id="manual:other.order")
    with pytest.raises(BrokerJournalConflict, match="different fill content"):
        journal.apply_reconciliation(
            account=account(),
            orders=(),
            positions=(),
            fills=(conflicting_owner,),
            submission_resolutions={},
            activity_checkpoint=resolved.activity_id,
            timestamp=NOW + timedelta(seconds=3),
            report={"ready": False},
        )
    journal.close()
