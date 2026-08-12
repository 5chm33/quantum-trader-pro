from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantum_trader.domain.brokerage import (
    AccountStatus,
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerActivityPage,
    BrokerCancelResult,
    BrokerClockSnapshot,
    BrokerFillActivity,
    BrokerOrderSnapshot,
    BrokerOrderStateMachine,
    BrokerOrderStatus,
    BrokerOrderType,
    SubmissionJournalEntry,
    SubmissionState,
    TimeInForce,
    TransitionDisposition,
    deterministic_client_order_id,
)
from quantum_trader.domain.execution import (
    PAPER_ACKNOWLEDGEMENT,
    ArmingRecord,
    BrokerPreflight,
    ExecutionFingerprint,
    ExecutionGate,
    ExecutionMode,
    GateState,
)
from quantum_trader.domain.models import OrderIntent, RiskDecision, Side

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def fingerprint() -> ExecutionFingerprint:
    return ExecutionFingerprint(
        code_sha256=DIGEST_A,
        configuration_sha256=DIGEST_B,
        account_sha256=DIGEST_C,
    )


def ready_preflight() -> BrokerPreflight:
    return BrokerPreflight(
        environment_verified=True,
        account_verified=True,
        account_active=True,
        account_unblocked=True,
        reconciliation_complete=True,
        broker_clock_verified=True,
        market_data_fresh=True,
        durable_journal_ready=True,
        secret_source_secure=True,
    )


def paper_context():
    record = ArmingRecord.issue_paper(
        strategy_namespace="qtpro-test",
        fingerprint=fingerprint(),
        issued_at=NOW,
        ttl=timedelta(hours=2),
        acknowledgement=PAPER_ACKNOWLEDGEMENT,
    )
    context = ExecutionGate.arm_paper(
        requested_mode=ExecutionMode.PAPER,
        record=record,
        expected_namespace="qtpro-test",
        expected_fingerprint=fingerprint(),
        preflight=ready_preflight(),
        now=NOW + timedelta(minutes=1),
    )
    return record, context


def approved_intent() -> tuple[OrderIntent, RiskDecision]:
    intent = OrderIntent.create(
        correlation_id="corr-1",
        timestamp=NOW,
        symbol="SPY",
        side=Side.BUY,
        quantity=10,
        reference_price=Decimal("500"),
        rationale="test",
    )
    decision = RiskDecision(
        allowed=True,
        reason="within_limits",
        approved_quantity=8,
        intent_id=intent.intent_id,
        correlation_id=intent.correlation_id,
    )
    return intent, decision


def test_paper_arming_is_expiring_bound_and_live_unavailable() -> None:
    record, context = paper_context()

    assert record.state_at(NOW - timedelta(seconds=1)) is GateState.DISARMED
    assert record.state_at(NOW + timedelta(minutes=1)) is GateState.ARMED
    assert record.state_at(NOW + timedelta(hours=2)) is GateState.EXPIRED
    assert context.environment is ExecutionMode.PAPER
    assert PAPER_ACKNOWLEDGEMENT not in str(record.as_dict())
    assert ExecutionGate.require_simulation("simulation") is ExecutionMode.SIMULATION

    with pytest.raises(ValueError, match="unavailable"):
        ExecutionGate.arm_paper(
            requested_mode=ExecutionMode.LIVE,
            record=record,
            expected_namespace="qtpro-test",
            expected_fingerprint=fingerprint(),
            preflight=ready_preflight(),
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="unavailable"):
        ExecutionGate.require_live()
    with pytest.raises(ValueError, match="only offline simulation"):
        ExecutionGate.require_simulation("paper")


def test_arming_rejects_bad_ack_expiry_fingerprint_namespace_and_preflight() -> None:
    with pytest.raises(ValueError, match="acknowledgement"):
        ArmingRecord.issue_paper(
            strategy_namespace="qtpro-test",
            fingerprint=fingerprint(),
            issued_at=NOW,
            ttl=timedelta(hours=1),
            acknowledgement="yes",
        )
    with pytest.raises(ValueError, match="24 hours"):
        ArmingRecord.issue_paper(
            strategy_namespace="qtpro-test",
            fingerprint=fingerprint(),
            issued_at=NOW,
            ttl=timedelta(hours=25),
            acknowledgement=PAPER_ACKNOWLEDGEMENT,
        )
    record, _ = paper_context()
    with pytest.raises(ValueError, match="not currently active"):
        ExecutionGate.arm_paper(
            requested_mode="paper",
            record=record,
            expected_namespace="qtpro-test",
            expected_fingerprint=fingerprint(),
            preflight=ready_preflight(),
            now=NOW + timedelta(hours=3),
        )
    with pytest.raises(ValueError, match="namespace"):
        ExecutionGate.arm_paper(
            requested_mode="paper",
            record=record,
            expected_namespace="other",
            expected_fingerprint=fingerprint(),
            preflight=ready_preflight(),
            now=NOW + timedelta(minutes=1),
        )
    changed = ExecutionFingerprint(
        code_sha256=DIGEST_D,
        configuration_sha256=DIGEST_B,
        account_sha256=DIGEST_C,
    )
    with pytest.raises(ValueError, match="code fingerprint"):
        ExecutionGate.arm_paper(
            requested_mode="paper",
            record=record,
            expected_namespace="qtpro-test",
            expected_fingerprint=changed,
            preflight=ready_preflight(),
            now=NOW + timedelta(minutes=1),
        )
    blocked = BrokerPreflight(
        environment_verified=True,
        account_verified=True,
        account_active=False,
        account_unblocked=True,
        reconciliation_complete=False,
        broker_clock_verified=True,
        market_data_fresh=False,
        durable_journal_ready=True,
        secret_source_secure=True,
        unexplained_orders=1,
    )
    assert blocked.ready is False
    assert blocked.failures() == (
        "account_not_active",
        "reconciliation_incomplete",
        "market_data_stale",
        "unexplained_orders",
    )
    with pytest.raises(ValueError, match="paper preflight failed"):
        ExecutionGate.arm_paper(
            requested_mode="paper",
            record=record,
            expected_namespace="qtpro-test",
            expected_fingerprint=fingerprint(),
            preflight=blocked,
            now=NOW + timedelta(minutes=1),
        )


def test_deterministic_client_id_and_approved_order_require_risk_approval() -> None:
    _, context = paper_context()
    intent, decision = approved_intent()
    first = deterministic_client_order_id(
        strategy_namespace=context.strategy_namespace,
        account_sha256=context.fingerprint.account_sha256,
        intent=intent,
        approved_quantity=decision.approved_quantity,
    )
    second = deterministic_client_order_id(
        strategy_namespace=context.strategy_namespace,
        account_sha256=context.fingerprint.account_sha256,
        intent=intent,
        approved_quantity=decision.approved_quantity,
    )
    assert first == second
    assert first.startswith("qt-qtpro-test-")
    assert len(first) <= 48

    order = ApprovedBrokerOrder.from_approved_intent(
        context=context,
        intent=intent,
        decision=decision,
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        limit_price=Decimal("499.50"),
        extended_hours=True,
    )
    assert order.client_order_id == first
    assert order.quantity == 8
    assert order.as_dict()["account_sha256"] == DIGEST_C

    denied = RiskDecision(
        allowed=False,
        reason="denied",
        approved_quantity=0,
        intent_id=intent.intent_id,
        correlation_id=intent.correlation_id,
    )
    with pytest.raises(ValueError, match="denied"):
        ApprovedBrokerOrder.from_approved_intent(
            context=context,
            intent=intent,
            decision=denied,
            order_type=BrokerOrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            limit_price=None,
        )
    with pytest.raises(ValueError, match="extended-hours"):
        ApprovedBrokerOrder.from_approved_intent(
            context=context,
            intent=intent,
            decision=decision,
            order_type=BrokerOrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            limit_price=None,
            extended_hours=True,
        )


def test_account_clock_order_fill_and_cancel_state_invariants() -> None:
    active = BrokerAccountSnapshot(
        environment=ExecutionMode.PAPER,
        account_sha256=DIGEST_C,
        status=AccountStatus.ACTIVE,
        trading_blocked=False,
        account_blocked=False,
        transfers_blocked=False,
        cash=Decimal("10000"),
        equity=Decimal("12000"),
        buying_power=Decimal("10000"),
        captured_at=NOW,
        raw_payload_sha256=DIGEST_D,
    )
    assert active.permits_new_exposure is True
    blocked = BrokerAccountSnapshot(
        environment=ExecutionMode.PAPER,
        account_sha256=DIGEST_C,
        status=AccountStatus.ACTIVE,
        trading_blocked=True,
        account_blocked=False,
        transfers_blocked=False,
        cash=Decimal("10000"),
        equity=Decimal("12000"),
        buying_power=Decimal("10000"),
        captured_at=NOW,
        raw_payload_sha256=DIGEST_D,
    )
    assert blocked.permits_new_exposure is False

    clock = BrokerClockSnapshot(
        is_open=True,
        timestamp=NOW,
        next_open=NOW + timedelta(days=1),
        next_close=NOW + timedelta(hours=6),
        raw_payload_sha256=DIGEST_D,
    )
    assert clock.is_open is True

    partial = BrokerOrderSnapshot(
        broker_order_id="broker-1",
        client_order_id="qt-qtpro-test-0123456789abcdef01234567",
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("10"),
        filled_quantity=Decimal("4"),
        average_fill_price=Decimal("500"),
        submitted_at=NOW,
        updated_at=NOW + timedelta(seconds=1),
        raw_payload_sha256=DIGEST_D,
    )
    assert partial.remaining_quantity == Decimal("6")
    assert partial.status.terminal is False
    with pytest.raises(ValueError, match="filled status"):
        BrokerOrderSnapshot(
            broker_order_id="broker-1",
            client_order_id=partial.client_order_id,
            status=BrokerOrderStatus.FILLED,
            symbol="SPY",
            side=Side.BUY,
            quantity=Decimal("10"),
            filled_quantity=Decimal("4"),
            average_fill_price=Decimal("500"),
            submitted_at=NOW,
            updated_at=NOW,
            raw_payload_sha256=DIGEST_D,
        )

    activity = BrokerFillActivity(
        activity_id="activity-1",
        execution_id="execution-1",
        broker_order_id="broker-1",
        client_order_id=partial.client_order_id,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("4"),
        price=Decimal("500"),
        fee=Decimal("0.04"),
        timestamp=NOW + timedelta(seconds=1),
        raw_payload_sha256=DIGEST_D,
    )
    page = BrokerActivityPage(
        activities=(activity,),
        next_page_token=None,
        raw_payload_sha256=DIGEST_D,
    )
    assert page.activities == (activity,)
    with pytest.raises(ValueError, match="duplicate execution"):
        BrokerActivityPage(
            activities=(activity, activity),
            next_page_token=None,
            raw_payload_sha256=DIGEST_D,
        )

    pending_cancel = BrokerCancelResult(
        broker_order_id="broker-1",
        requested_at=NOW,
        accepted=True,
        observed_status=BrokerOrderStatus.PENDING_CANCEL,
        raw_payload_sha256=DIGEST_D,
    )
    assert pending_cancel.verified_terminal is False
    canceled = BrokerCancelResult(
        broker_order_id="broker-1",
        requested_at=NOW,
        accepted=True,
        observed_status=BrokerOrderStatus.CANCELED,
        raw_payload_sha256=DIGEST_D,
    )
    assert canceled.verified_terminal is True


def test_submission_journal_and_order_state_machine_fail_closed() -> None:
    client_order_id = "qt-qtpro-test-0123456789abcdef01234567"
    persisted = SubmissionJournalEntry(
        sequence=1,
        client_order_id=client_order_id,
        requested_payload_sha256=DIGEST_A,
        state=SubmissionState.PERSISTED,
        created_at=NOW,
        updated_at=NOW,
    )
    assert persisted.as_dict()["broker_order_id"] is None
    acknowledged = SubmissionJournalEntry(
        sequence=1,
        client_order_id=client_order_id,
        requested_payload_sha256=DIGEST_A,
        state=SubmissionState.ACKNOWLEDGED,
        created_at=NOW,
        updated_at=NOW + timedelta(seconds=1),
        broker_order_id="broker-1",
    )
    assert acknowledged.broker_order_id == "broker-1"
    with pytest.raises(ValueError, match="requires broker_order_id"):
        SubmissionJournalEntry(
            sequence=1,
            client_order_id=client_order_id,
            requested_payload_sha256=DIGEST_A,
            state=SubmissionState.RECONCILED,
            created_at=NOW,
            updated_at=NOW,
        )
    with pytest.raises(ValueError, match="requires a reason"):
        SubmissionJournalEntry(
            sequence=1,
            client_order_id=client_order_id,
            requested_payload_sha256=DIGEST_A,
            state=SubmissionState.AMBIGUOUS,
            created_at=NOW,
            updated_at=NOW,
        )

    new = BrokerOrderSnapshot(
        broker_order_id="broker-1",
        client_order_id=client_order_id,
        status=BrokerOrderStatus.NEW,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("10"),
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        submitted_at=NOW,
        updated_at=NOW,
        raw_payload_sha256=DIGEST_A,
    )
    duplicate = BrokerOrderSnapshot(
        broker_order_id="broker-1",
        client_order_id=client_order_id,
        status=BrokerOrderStatus.NEW,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("10"),
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        submitted_at=NOW,
        updated_at=NOW + timedelta(seconds=1),
        raw_payload_sha256=DIGEST_B,
    )
    assert (
        BrokerOrderStateMachine.require_transition(new, duplicate)
        is TransitionDisposition.DUPLICATE
    )
    partial = BrokerOrderSnapshot(
        broker_order_id="broker-1",
        client_order_id=client_order_id,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("10"),
        filled_quantity=Decimal("4"),
        average_fill_price=Decimal("500"),
        submitted_at=NOW,
        updated_at=NOW + timedelta(seconds=2),
        raw_payload_sha256=DIGEST_C,
    )
    assert BrokerOrderStateMachine.require_transition(new, partial) is TransitionDisposition.APPLIED
    cancel_pending = BrokerOrderSnapshot(
        broker_order_id="broker-1",
        client_order_id=client_order_id,
        status=BrokerOrderStatus.PENDING_CANCEL,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("10"),
        filled_quantity=Decimal("4"),
        average_fill_price=Decimal("500"),
        submitted_at=NOW,
        updated_at=NOW + timedelta(seconds=3),
        raw_payload_sha256=DIGEST_D,
    )
    final_fill = BrokerOrderSnapshot(
        broker_order_id="broker-1",
        client_order_id=client_order_id,
        status=BrokerOrderStatus.FILLED,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("10"),
        filled_quantity=Decimal("10"),
        average_fill_price=Decimal("500.25"),
        submitted_at=NOW,
        updated_at=NOW + timedelta(seconds=4),
        raw_payload_sha256=DIGEST_A,
    )
    assert (
        BrokerOrderStateMachine.require_transition(partial, cancel_pending)
        is TransitionDisposition.APPLIED
    )
    assert (
        BrokerOrderStateMachine.require_transition(cancel_pending, final_fill)
        is TransitionDisposition.APPLIED
    )
    late_cancel = BrokerOrderSnapshot(
        broker_order_id="broker-1",
        client_order_id=client_order_id,
        status=BrokerOrderStatus.PENDING_CANCEL,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("10"),
        filled_quantity=Decimal("10"),
        average_fill_price=Decimal("500.25"),
        submitted_at=NOW,
        updated_at=NOW + timedelta(seconds=5),
        raw_payload_sha256=DIGEST_B,
    )
    with pytest.raises(ValueError, match="terminal"):
        BrokerOrderStateMachine.require_transition(final_fill, late_cancel)
    regressed = BrokerOrderSnapshot(
        broker_order_id="broker-1",
        client_order_id=client_order_id,
        status=BrokerOrderStatus.NEW,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("10"),
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        submitted_at=NOW,
        updated_at=NOW + timedelta(seconds=3),
        raw_payload_sha256=DIGEST_B,
    )
    with pytest.raises(ValueError, match="moved backwards"):
        BrokerOrderStateMachine.require_transition(partial, regressed)
