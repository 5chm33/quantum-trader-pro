from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantum_trader.adapters.sqlite_operator_control import SQLiteOperatorControl
from quantum_trader.application.operator_actions import (
    OperatorActionError,
    PaperOperatorActions,
)
from quantum_trader.application.reconciliation import (
    IssueSeverity,
    ReconciliationIssue,
    ReconciliationReport,
)
from quantum_trader.domain.brokerage import (
    BrokerCancelResult,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    is_owned_client_order_id,
    owned_client_order_prefix,
)
from quantum_trader.domain.execution import (
    ArmedExecutionContext,
    ExecutionFingerprint,
    ExecutionMode,
)
from quantum_trader.domain.models import Side
from quantum_trader.domain.operator import (
    ACKNOWLEDGEMENTS,
    OperatorAction,
    OperatorActionState,
    OperatorApproval,
)

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
KEY = b"k" * 32
FINGERPRINT = ExecutionFingerprint("a" * 64, "b" * 64, "c" * 64)
RAW = "d" * 64
NAMESPACE = "qtpro-paper"


def context() -> ArmedExecutionContext:
    return ArmedExecutionContext(
        record_id="paper-context",
        environment=ExecutionMode.PAPER,
        strategy_namespace=NAMESPACE,
        fingerprint=FINGERPRINT,
        armed_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )


def approval(action: OperatorAction, nonce_character: str = "1") -> OperatorApproval:
    return OperatorApproval.issue(
        action=action,
        strategy_namespace=NAMESPACE,
        fingerprint=FINGERPRINT,
        issued_at=NOW - timedelta(minutes=1),
        ttl=timedelta(minutes=5),
        nonce=nonce_character * 32,
        acknowledgement=ACKNOWLEDGEMENTS[action],
        control_key=KEY,
    )


def order(*, owned: bool, suffix: str) -> BrokerOrderSnapshot:
    client_id = (
        f"{owned_client_order_prefix(NAMESPACE)}{suffix * 24}"
        if owned
        else f"manual-order-{suffix}"
    )
    return BrokerOrderSnapshot(
        broker_order_id=f"broker-{suffix}",
        client_order_id=client_id,
        status=BrokerOrderStatus.NEW,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("1"),
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        submitted_at=NOW - timedelta(minutes=2),
        updated_at=NOW - timedelta(minutes=1),
        raw_payload_sha256=RAW,
        limit_price=Decimal("500"),
    )


def report(
    *,
    ready: bool = True,
    account_permits_new_exposure: bool = True,
) -> ReconciliationReport:
    issues = ()
    if not ready:
        issues = (
            ReconciliationIssue(
                code="reconciliation_blocked",
                severity=IssueSeverity.CRITICAL,
                subject_sha256=RAW,
            ),
        )
    return ReconciliationReport(
        timestamp=NOW,
        ready=ready,
        account_sha256=FINGERPRINT.account_sha256,
        account_permits_new_exposure=account_permits_new_exposure,
        open_order_count=1,
        position_count=0,
        activity_count=0,
        new_execution_count=0,
        resolved_submission_count=0,
        unresolved_submission_count=0,
        activity_checkpoint_sha256=None,
        expected_positions={},
        broker_positions={},
        issues=issues,
    )


@dataclass(slots=True)
class FakeBroker:
    orders: list[BrokerOrderSnapshot]
    terminal_cancellation: bool = True
    canceled_ids: list[str] = field(default_factory=list)
    verified_contexts: list[ArmedExecutionContext] = field(default_factory=list)
    environment: ExecutionMode = ExecutionMode.PAPER
    account_sha256: str = FINGERPRINT.account_sha256

    def verify_context(self, armed_context: ArmedExecutionContext) -> None:
        assert armed_context.environment is ExecutionMode.PAPER
        assert armed_context.expires_at > NOW
        self.verified_contexts.append(armed_context)

    def list_open_orders(self):
        return tuple(self.orders)

    def cancel_order(
        self,
        *,
        context: ArmedExecutionContext,
        broker_order_id: str,
    ) -> BrokerCancelResult:
        self.verify_context(context)
        self.canceled_ids.append(broker_order_id)
        status = (
            BrokerOrderStatus.CANCELED
            if self.terminal_cancellation
            else BrokerOrderStatus.PENDING_CANCEL
        )
        if self.terminal_cancellation:
            self.orders = [item for item in self.orders if item.broker_order_id != broker_order_id]
        return BrokerCancelResult(
            broker_order_id=broker_order_id,
            requested_at=NOW,
            accepted=True,
            observed_status=status,
            raw_payload_sha256=RAW,
        )


@dataclass(slots=True)
class FakeReconciler:
    result: ReconciliationReport = field(default_factory=report)
    calls: list[datetime] = field(default_factory=list)

    def reconcile(self, *, timestamp: datetime) -> ReconciliationReport:
        self.calls.append(timestamp)
        return self.result


def control_store(tmp_path: Path) -> SQLiteOperatorControl:
    return SQLiteOperatorControl(
        (tmp_path / "operator.db").resolve(),
        strategy_namespace=NAMESPACE,
        created_at=NOW - timedelta(minutes=2),
    )


def test_resume_requires_valid_approval_context_and_ready_reconciliation(
    tmp_path: Path,
) -> None:
    broker = FakeBroker([])
    store = control_store(tmp_path)
    reconciler = FakeReconciler()
    actions = PaperOperatorActions(
        broker=broker,  # type: ignore[arg-type]
        operator_control=store,
        reconciler=reconciler,  # type: ignore[arg-type]
        strategy_namespace=NAMESPACE,
    )
    resume_approval = approval(OperatorAction.RESUME, "8")
    result = actions.resume_paper_assessments(
        context=context(),
        approval=resume_approval,
        expected_fingerprint=FINGERPRINT,
        control_key=KEY,
        timestamp=NOW,
    )
    assert result.ready is True
    assert store.current_state().paused is False
    assert broker.verified_contexts == [context()]
    record = store.action_record(resume_approval.approval_id)
    assert record is not None
    assert record.state is OperatorActionState.COMPLETED


def test_resume_failure_leaves_controls_paused_and_approval_unconsumed(
    tmp_path: Path,
) -> None:
    broker = FakeBroker([])
    store = control_store(tmp_path)
    blocked = FakeReconciler(report(ready=False))
    actions = PaperOperatorActions(
        broker=broker,  # type: ignore[arg-type]
        operator_control=store,
        reconciler=blocked,  # type: ignore[arg-type]
        strategy_namespace=NAMESPACE,
    )
    resume_approval = approval(OperatorAction.RESUME, "9")
    with pytest.raises(OperatorActionError, match="not ready"):
        actions.resume_paper_assessments(
            context=context(),
            approval=resume_approval,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW,
        )
    assert store.current_state().paused is True
    assert store.action_record(resume_approval.approval_id) is None

    no_exposure = PaperOperatorActions(
        broker=broker,  # type: ignore[arg-type]
        operator_control=store,
        reconciler=FakeReconciler(report(account_permits_new_exposure=False)),  # type: ignore[arg-type]
        strategy_namespace=NAMESPACE,
    )
    with pytest.raises(OperatorActionError, match="new exposure"):
        no_exposure.resume_paper_assessments(
            context=context(),
            approval=resume_approval,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW,
        )
    assert store.current_state().paused is True


def test_cancel_kill_switch_cancels_only_owned_orders_and_remains_paused(
    tmp_path: Path,
) -> None:
    owned = order(owned=True, suffix="1")
    foreign = order(owned=False, suffix="2")
    broker = FakeBroker([owned, foreign])
    store = control_store(tmp_path)
    reconciler = FakeReconciler()
    actions = PaperOperatorActions(
        broker=broker,  # type: ignore[arg-type]
        operator_control=store,
        reconciler=reconciler,  # type: ignore[arg-type]
        strategy_namespace=NAMESPACE,
    )
    cancel_approval = approval(OperatorAction.CANCEL_OWNED_ORDERS)
    result = actions.cancel_owned_orders(
        context=context(),
        approval=cancel_approval,
        expected_fingerprint=FINGERPRINT,
        control_key=KEY,
        timestamp=NOW,
    )
    assert result.succeeded is True
    assert result.owned_open_orders == 1
    assert result.foreign_open_orders == 1
    assert result.verified_terminal == 1
    assert broker.canceled_ids == [owned.broker_order_id]
    assert broker.orders == [foreign]
    assert not is_owned_client_order_id(foreign.client_order_id, NAMESPACE)
    assert reconciler.calls == [NOW]
    assert store.current_state().paused is True
    action_record = store.action_record(cancel_approval.approval_id)
    assert action_record is not None
    assert action_record.state is OperatorActionState.COMPLETED


def test_nonterminal_cancel_is_recorded_failed_and_system_stays_paused(
    tmp_path: Path,
) -> None:
    owned = order(owned=True, suffix="3")
    broker = FakeBroker([owned], terminal_cancellation=False)
    store = control_store(tmp_path)
    actions = PaperOperatorActions(
        broker=broker,  # type: ignore[arg-type]
        operator_control=store,
        reconciler=FakeReconciler(),  # type: ignore[arg-type]
        strategy_namespace=NAMESPACE,
    )
    cancel_approval = approval(OperatorAction.CANCEL_OWNED_ORDERS, "2")
    with pytest.raises(OperatorActionError, match="terminal"):
        actions.cancel_owned_orders(
            context=context(),
            approval=cancel_approval,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW,
        )
    assert broker.orders == [owned]
    assert store.current_state().paused is True
    action_record = store.action_record(cancel_approval.approval_id)
    assert action_record is not None
    assert action_record.state is OperatorActionState.FAILED


def test_invalid_approval_still_pauses_before_any_broker_side_effect(
    tmp_path: Path,
) -> None:
    owned = order(owned=True, suffix="4")
    broker = FakeBroker([owned])
    store = control_store(tmp_path)
    resume_approval = approval(OperatorAction.RESUME, "3")
    actions = PaperOperatorActions(
        broker=broker,  # type: ignore[arg-type]
        operator_control=store,
        reconciler=FakeReconciler(),  # type: ignore[arg-type]
        strategy_namespace=NAMESPACE,
    )
    with pytest.raises(OperatorActionError, match="failed closed"):
        actions.cancel_owned_orders(
            context=context(),
            approval=resume_approval,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW,
        )
    assert store.current_state().paused is True
    assert broker.canceled_ids == []
    assert broker.verified_contexts == []
    assert store.action_record(resume_approval.approval_id) is None


def test_context_fingerprint_mismatch_is_durably_failed_before_cancel(
    tmp_path: Path,
) -> None:
    owned = order(owned=True, suffix="5")
    broker = FakeBroker([owned])
    store = control_store(tmp_path)
    actions = PaperOperatorActions(
        broker=broker,  # type: ignore[arg-type]
        operator_control=store,
        reconciler=FakeReconciler(),  # type: ignore[arg-type]
        strategy_namespace=NAMESPACE,
    )
    cancel_approval = approval(OperatorAction.CANCEL_OWNED_ORDERS, "4")
    wrong_context = ArmedExecutionContext(
        record_id="wrong-context",
        environment=ExecutionMode.PAPER,
        strategy_namespace=NAMESPACE,
        fingerprint=ExecutionFingerprint("f" * 64, "b" * 64, "c" * 64),
        armed_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(OperatorActionError, match="fingerprint"):
        actions.cancel_owned_orders(
            context=wrong_context,
            approval=cancel_approval,
            expected_fingerprint=FINGERPRINT,
            control_key=KEY,
            timestamp=NOW,
        )
    assert broker.canceled_ids == []
    action_record = store.action_record(cancel_approval.approval_id)
    assert action_record is not None
    assert action_record.state is OperatorActionState.FAILED
