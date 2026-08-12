from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantum_trader.adapters.sqlite_broker_journal import SQLiteBrokerJournal
from quantum_trader.adapters.sqlite_operator_control import SQLiteOperatorControl
from quantum_trader.application.paper_controls import PaperPreTradeAssessment
from quantum_trader.application.paper_execution import (
    PaperExecutionAmbiguous,
    PaperExecutionBlocked,
    PaperExecutionBoundary,
    PaperExecutionRejected,
    PaperOrderExecutor,
    approved_order_payload_sha256,
)
from quantum_trader.application.reconciliation import ReconciliationReport
from quantum_trader.domain.brokerage import (
    AccountStatus,
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    BrokerOrderType,
    SubmissionState,
    TimeInForce,
    owned_client_order_prefix,
)
from quantum_trader.domain.execution import (
    ArmedExecutionContext,
    ExecutionFingerprint,
    ExecutionMode,
)
from quantum_trader.domain.market_controls import PaperControlDecision
from quantum_trader.domain.models import Side
from quantum_trader.domain.operator import (
    ACKNOWLEDGEMENTS,
    OperatorAction,
    OperatorApproval,
)
from quantum_trader.ports.external_broker import (
    ExternalSubmissionAmbiguous,
    ExternalSubmissionRejected,
)

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
NAMESPACE = "qtpro-paper"
ACCOUNT_SHA = "a" * 64
RAW = "b" * 64
ATTEMPT = "c" * 64
CONTROL_KEY = b"k" * 32
FINGERPRINT = ExecutionFingerprint("d" * 64, "e" * 64, ACCOUNT_SHA)


class HardCrash(BaseException):
    pass


def context() -> ArmedExecutionContext:
    return ArmedExecutionContext(
        record_id="failure-injection-context",
        environment=ExecutionMode.PAPER,
        strategy_namespace=NAMESPACE,
        fingerprint=FINGERPRINT,
        armed_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )


def approved_order() -> ApprovedBrokerOrder:
    return ApprovedBrokerOrder(
        client_order_id=f"{owned_client_order_prefix(NAMESPACE)}{'1' * 24}",
        intent_id="intent-failure-injection",
        correlation_id="correlation-failure-injection",
        symbol="SPY",
        side=Side.BUY,
        quantity=1,
        order_type=BrokerOrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        reference_price=Decimal("500"),
        limit_price=None,
        extended_hours=False,
        strategy_namespace=NAMESPACE,
        account_sha256=ACCOUNT_SHA,
        created_at=NOW - timedelta(seconds=1),
    )


def broker_order(order: ApprovedBrokerOrder) -> BrokerOrderSnapshot:
    return BrokerOrderSnapshot(
        broker_order_id="broker-failure-injection",
        client_order_id=order.client_order_id,
        status=BrokerOrderStatus.NEW,
        symbol=order.symbol,
        side=order.side,
        quantity=Decimal(order.quantity),
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        submitted_at=NOW,
        updated_at=NOW,
        raw_payload_sha256=RAW,
    )


def account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        environment=ExecutionMode.PAPER,
        account_sha256=ACCOUNT_SHA,
        status=AccountStatus.ACTIVE,
        trading_blocked=False,
        account_blocked=False,
        transfers_blocked=False,
        cash=Decimal("100000"),
        equity=Decimal("100000"),
        buying_power=Decimal("100000"),
        captured_at=NOW,
        raw_payload_sha256=RAW,
    )


def reconciliation_report(*, ready: bool = True) -> ReconciliationReport:
    return ReconciliationReport(
        timestamp=NOW,
        ready=ready,
        account_sha256=ACCOUNT_SHA,
        account_permits_new_exposure=True,
        open_order_count=1,
        position_count=0,
        activity_count=0,
        new_execution_count=0,
        resolved_submission_count=1,
        unresolved_submission_count=0,
        activity_checkpoint_sha256=None,
        expected_positions={},
        broker_positions={},
        issues=(),
    )


@dataclass(slots=True)
class FakeBroker:
    created_order: BrokerOrderSnapshot | None = None
    submit_mode: str = "success"
    submit_invocations: int = 0
    external_side_effects: int = 0
    verified_contexts: int = 0
    environment: ExecutionMode = ExecutionMode.PAPER
    account_sha256: str = ACCOUNT_SHA

    def verify_context(self, armed_context: ArmedExecutionContext) -> None:
        assert armed_context.environment is ExecutionMode.PAPER
        assert armed_context.fingerprint.account_sha256 == self.account_sha256
        self.verified_contexts += 1

    def submit_once(
        self,
        *,
        context: ArmedExecutionContext,
        order: ApprovedBrokerOrder,
        submission_journal_sequence: int,
    ) -> BrokerOrderSnapshot:
        self.verify_context(context)
        assert submission_journal_sequence > 0
        self.submit_invocations += 1
        if self.submit_mode == "rejected":
            raise ExternalSubmissionRejected("fixture rejection")
        if self.created_order is None:
            self.created_order = broker_order(order)
            self.external_side_effects += 1
        if self.submit_mode == "ambiguous":
            raise ExternalSubmissionAmbiguous("fixture ambiguity")
        return self.created_order

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        if self.created_order is None:
            return None
        if self.created_order.client_order_id != client_order_id:
            return None
        return self.created_order


@dataclass(slots=True)
class FakePreTrade:
    operator_control: SQLiteOperatorControl
    calls: int = 0
    allowed: bool = True

    def assess(
        self,
        *,
        order: ApprovedBrokerOrder,
        timestamp: datetime,
    ) -> PaperPreTradeAssessment:
        del order, timestamp
        self.calls += 1
        reasons = () if self.allowed else ("fixture_blocked",)
        return PaperPreTradeAssessment(
            reconciliation=reconciliation_report() if self.allowed else None,
            operator_state=self.operator_control.current_state(),
            decision=PaperControlDecision(
                allowed=self.allowed,
                reasons=reasons,
                candidate_notional=Decimal("500"),
                committed_open_buy_notional=Decimal("0"),
                projected_gross_exposure=Decimal("500"),
                projected_symbol_exposure=Decimal("500"),
                projected_cash=Decimal("99500"),
                recent_order_count=0,
                session_order_count=0,
            ),
        )


@dataclass(slots=True)
class FakeReconciler:
    journal: SQLiteBrokerJournal
    broker: FakeBroker
    fail: bool = False
    calls: int = 0

    def reconcile(self, *, timestamp: datetime) -> ReconciliationReport:
        self.calls += 1
        if self.fail:
            raise RuntimeError("injected reconciliation failure")
        created = self.broker.created_order
        if created is None:
            raise RuntimeError("fixture broker order is missing")
        report = reconciliation_report()
        self.journal.apply_reconciliation(
            account=account(),
            orders=(created,),
            positions=(),
            fills=(),
            submission_resolutions={created.client_order_id: created.broker_order_id},
            activity_checkpoint=None,
            timestamp=timestamp,
            report=report.as_dict(),
        )
        return report


@dataclass(slots=True)
class CrashAt:
    target: PaperExecutionBoundary
    observed: list[PaperExecutionBoundary] = field(default_factory=list)

    def __call__(self, boundary: PaperExecutionBoundary) -> None:
        self.observed.append(boundary)
        if boundary is self.target:
            raise HardCrash(boundary.value)


def operator_store(tmp_path: Path) -> SQLiteOperatorControl:
    store = SQLiteOperatorControl(
        (tmp_path / "operator.db").resolve(),
        strategy_namespace=NAMESPACE,
        created_at=NOW - timedelta(minutes=2),
    )
    approval = OperatorApproval.issue(
        action=OperatorAction.RESUME,
        strategy_namespace=NAMESPACE,
        fingerprint=FINGERPRINT,
        issued_at=NOW - timedelta(minutes=1),
        ttl=timedelta(minutes=5),
        nonce="f" * 32,
        acknowledgement=ACKNOWLEDGEMENTS[OperatorAction.RESUME],
        control_key=CONTROL_KEY,
    )
    store.resume(
        approval=approval,
        expected_fingerprint=FINGERPRINT,
        control_key=CONTROL_KEY,
        timestamp=NOW - timedelta(seconds=30),
        reason="failure-injection fixture ready",
    )
    return store


def executor(
    *,
    journal: SQLiteBrokerJournal,
    operator_control: SQLiteOperatorControl,
    broker: FakeBroker,
    reconciler: FakeReconciler,
    fault_hook: CrashAt | None = None,
    pretrade: FakePreTrade | None = None,
) -> PaperOrderExecutor:
    return PaperOrderExecutor(
        broker=broker,  # type: ignore[arg-type]
        journal=journal,
        operator_control=operator_control,
        pretrade=pretrade or FakePreTrade(operator_control),  # type: ignore[arg-type]
        reconciler=reconciler,  # type: ignore[arg-type]
        fault_hook=fault_hook,
    )


def test_successful_execution_is_journaled_acknowledged_and_reconciled_once(
    tmp_path: Path,
) -> None:
    order = approved_order()
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    controls = operator_store(tmp_path)
    broker = FakeBroker()
    reconciler = FakeReconciler(journal, broker)
    result = executor(
        journal=journal,
        operator_control=controls,
        broker=broker,
        reconciler=reconciler,
    ).execute_once(
        context=context(),
        order=order,
        attempt_sha256=ATTEMPT,
        timestamp=NOW,
    )
    assert result.ready is True
    assert result.submission.state is SubmissionState.RECONCILED
    assert broker.external_side_effects == 1
    assert broker.submit_invocations == 1
    assert reconciler.calls == 1
    assert approved_order_payload_sha256(order) == result.submission.requested_payload_sha256

    with pytest.raises(PaperExecutionBlocked, match="durable state"):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=reconciler,
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256="9" * 64,
            timestamp=NOW + timedelta(seconds=1),
        )
    assert broker.external_side_effects == 1


def test_crash_after_preflight_leaves_no_durable_or_external_side_effect(
    tmp_path: Path,
) -> None:
    order = approved_order()
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    controls = operator_store(tmp_path)
    broker = FakeBroker()
    reconciler = FakeReconciler(journal, broker)
    with pytest.raises(HardCrash):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=reconciler,
            fault_hook=CrashAt(PaperExecutionBoundary.AFTER_PREFLIGHT),
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256=ATTEMPT,
            timestamp=NOW,
        )
    assert journal.submission_by_client_id(order.client_order_id) is None
    assert broker.external_side_effects == 0


def test_crash_after_persist_requires_recovery_and_never_submits(tmp_path: Path) -> None:
    order = approved_order()
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    controls = operator_store(tmp_path)
    broker = FakeBroker()
    reconciler = FakeReconciler(journal, broker)
    with pytest.raises(HardCrash):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=reconciler,
            fault_hook=CrashAt(PaperExecutionBoundary.AFTER_PERSIST),
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256=ATTEMPT,
            timestamp=NOW,
        )
    entry = journal.submission_by_client_id(order.client_order_id)
    assert entry is not None
    assert entry.state is SubmissionState.PERSISTED
    with pytest.raises(PaperExecutionBlocked, match="unresolved"):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=reconciler,
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256="8" * 64,
            timestamp=NOW + timedelta(seconds=1),
        )
    assert broker.external_side_effects == 0
    with pytest.raises(PaperExecutionRejected, match="never started"):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=reconciler,
        ).recover_submission(
            context=context(),
            client_order_id=order.client_order_id,
            expected_payload_sha256=approved_order_payload_sha256(order),
            timestamp=NOW + timedelta(seconds=2),
        )
    final = journal.submission_by_client_id(order.client_order_id)
    assert final is not None
    assert final.state is SubmissionState.REJECTED
    assert controls.current_state().paused is True


def test_crash_after_started_without_broker_visibility_stays_ambiguous(
    tmp_path: Path,
) -> None:
    order = approved_order()
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    controls = operator_store(tmp_path)
    broker = FakeBroker()
    reconciler = FakeReconciler(journal, broker)
    with pytest.raises(HardCrash):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=reconciler,
            fault_hook=CrashAt(PaperExecutionBoundary.AFTER_STARTED),
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256=ATTEMPT,
            timestamp=NOW,
        )
    with pytest.raises(PaperExecutionAmbiguous, match="do not retry"):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=reconciler,
        ).recover_submission(
            context=context(),
            client_order_id=order.client_order_id,
            expected_payload_sha256=approved_order_payload_sha256(order),
            timestamp=NOW + timedelta(seconds=1),
        )
    assert broker.submit_invocations == 0
    assert controls.current_state().paused is True


@pytest.mark.parametrize(
    "boundary",
    [
        PaperExecutionBoundary.AFTER_BROKER_RESPONSE,
        PaperExecutionBoundary.AFTER_ACKNOWLEDGED,
        PaperExecutionBoundary.AFTER_RECONCILIATION,
    ],
)
def test_post_submit_crashes_recover_by_lookup_without_second_side_effect(
    tmp_path: Path,
    boundary: PaperExecutionBoundary,
) -> None:
    order = approved_order()
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    controls = operator_store(tmp_path)
    broker = FakeBroker()
    reconciler = FakeReconciler(journal, broker)
    with pytest.raises(HardCrash):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=reconciler,
            fault_hook=CrashAt(boundary),
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256=ATTEMPT,
            timestamp=NOW,
        )
    assert broker.external_side_effects == 1
    recovered = executor(
        journal=journal,
        operator_control=controls,
        broker=broker,
        reconciler=reconciler,
    ).recover_submission(
        context=context(),
        client_order_id=order.client_order_id,
        expected_payload_sha256=approved_order_payload_sha256(order),
        timestamp=NOW + timedelta(seconds=1),
    )
    assert recovered.ready is True
    assert recovered.recovered is True
    assert broker.external_side_effects == 1
    assert broker.submit_invocations == 1
    assert controls.current_state().paused is True


def test_known_rejection_is_terminal_and_ambiguous_submit_stays_paused(
    tmp_path: Path,
) -> None:
    order = approved_order()
    rejected_journal = SQLiteBrokerJournal(tmp_path / "rejected.db")
    rejected_controls = operator_store(tmp_path / "rejected")
    rejected_broker = FakeBroker(submit_mode="rejected")
    rejected_reconciler = FakeReconciler(rejected_journal, rejected_broker)
    with pytest.raises(PaperExecutionRejected):
        executor(
            journal=rejected_journal,
            operator_control=rejected_controls,
            broker=rejected_broker,
            reconciler=rejected_reconciler,
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256=ATTEMPT,
            timestamp=NOW,
        )
    rejected = rejected_journal.submission_by_client_id(order.client_order_id)
    assert rejected is not None
    assert rejected.state is SubmissionState.REJECTED
    assert rejected_broker.external_side_effects == 0
    assert rejected_controls.current_state().paused is True

    ambiguous_journal = SQLiteBrokerJournal(tmp_path / "ambiguous.db")
    ambiguous_controls = operator_store(tmp_path / "ambiguous")
    ambiguous_broker = FakeBroker(submit_mode="ambiguous")
    ambiguous_reconciler = FakeReconciler(ambiguous_journal, ambiguous_broker)
    with pytest.raises(PaperExecutionAmbiguous):
        executor(
            journal=ambiguous_journal,
            operator_control=ambiguous_controls,
            broker=ambiguous_broker,
            reconciler=ambiguous_reconciler,
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256=ATTEMPT,
            timestamp=NOW,
        )
    ambiguous = ambiguous_journal.submission_by_client_id(order.client_order_id)
    assert ambiguous is not None
    assert ambiguous.state is SubmissionState.AMBIGUOUS
    assert ambiguous_broker.external_side_effects == 1
    assert ambiguous_controls.current_state().paused is True


def test_post_acknowledgment_reconciliation_failure_pauses_and_requires_recovery(
    tmp_path: Path,
) -> None:
    order = approved_order()
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    controls = operator_store(tmp_path)
    broker = FakeBroker()
    failing_reconciler = FakeReconciler(journal, broker, fail=True)
    with pytest.raises(PaperExecutionAmbiguous, match="reconciliation failed"):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=failing_reconciler,
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256=ATTEMPT,
            timestamp=NOW,
        )
    entry = journal.submission_by_client_id(order.client_order_id)
    assert entry is not None
    assert entry.state is SubmissionState.ACKNOWLEDGED
    assert controls.current_state().paused is True
    assert broker.external_side_effects == 1


def test_process_restart_recovers_post_response_crash_without_resubmission(
    tmp_path: Path,
) -> None:
    order = approved_order()
    broker_path = tmp_path / "broker.db"
    operator_path = (tmp_path / "operator.db").resolve()
    journal = SQLiteBrokerJournal(broker_path)
    controls = operator_store(tmp_path)
    broker = FakeBroker()
    reconciler = FakeReconciler(journal, broker)
    with pytest.raises(HardCrash):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=reconciler,
            fault_hook=CrashAt(PaperExecutionBoundary.AFTER_BROKER_RESPONSE),
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256=ATTEMPT,
            timestamp=NOW,
        )
    assert broker.external_side_effects == 1
    journal.close()
    controls.close()

    reopened_journal = SQLiteBrokerJournal(broker_path)
    reopened_controls = SQLiteOperatorControl(
        operator_path,
        strategy_namespace=NAMESPACE,
        created_at=NOW + timedelta(seconds=1),
    )
    recovered = executor(
        journal=reopened_journal,
        operator_control=reopened_controls,
        broker=broker,
        reconciler=FakeReconciler(reopened_journal, broker),
    ).recover_submission(
        context=context(),
        client_order_id=order.client_order_id,
        expected_payload_sha256=approved_order_payload_sha256(order),
        timestamp=NOW + timedelta(seconds=2),
    )
    assert recovered.ready is True
    assert recovered.submission.state is SubmissionState.RECONCILED
    assert broker.submit_invocations == 1
    assert broker.external_side_effects == 1
    assert reopened_controls.current_state().paused is True
    reopened_journal.close()
    reopened_controls.close()


@dataclass(slots=True)
class PauseAt:
    target: PaperExecutionBoundary
    operator_control: SQLiteOperatorControl

    def __call__(self, boundary: PaperExecutionBoundary) -> None:
        if boundary is self.target:
            self.operator_control.pause(
                timestamp=NOW,
                reason_code="injected_operator_pause",
                reason="failure-injection pause",
            )


@pytest.mark.parametrize(
    ("boundary", "expected_state"),
    [
        (PaperExecutionBoundary.AFTER_PREFLIGHT, None),
        (PaperExecutionBoundary.AFTER_PERSIST, SubmissionState.REJECTED),
        (PaperExecutionBoundary.AFTER_STARTED, SubmissionState.REJECTED),
    ],
)
def test_operator_pause_at_each_pre_submit_boundary_prevents_side_effect(
    tmp_path: Path,
    boundary: PaperExecutionBoundary,
    expected_state: SubmissionState | None,
) -> None:
    order = approved_order()
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    controls = operator_store(tmp_path)
    broker = FakeBroker()
    reconciler = FakeReconciler(journal, broker)
    with pytest.raises(PaperExecutionBlocked, match="pause"):
        executor(
            journal=journal,
            operator_control=controls,
            broker=broker,
            reconciler=reconciler,
            fault_hook=PauseAt(boundary, controls),  # type: ignore[arg-type]
        ).execute_once(
            context=context(),
            order=order,
            attempt_sha256=ATTEMPT,
            timestamp=NOW,
        )
    entry = journal.submission_by_client_id(order.client_order_id)
    assert (entry.state if entry is not None else None) is expected_state
    assert broker.submit_invocations == 0
    assert broker.external_side_effects == 0
    assert controls.current_state().paused is True
