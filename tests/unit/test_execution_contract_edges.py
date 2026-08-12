from __future__ import annotations

from dataclasses import replace
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
    BrokerPositionSnapshot,
    SubmissionJournalEntry,
    SubmissionState,
    TimeInForce,
    deterministic_client_order_id,
)
from quantum_trader.domain.execution import (
    PAPER_ACKNOWLEDGEMENT,
    ArmedExecutionContext,
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
D = "d" * 64
CLIENT_ID = "qt-edge-0123456789abcdef01234567"


def fingerprint() -> ExecutionFingerprint:
    return ExecutionFingerprint(A, B, C)


def record() -> ArmingRecord:
    return ArmingRecord.issue_paper(
        strategy_namespace="edge",
        fingerprint=fingerprint(),
        issued_at=NOW,
        ttl=timedelta(hours=1),
        acknowledgement=PAPER_ACKNOWLEDGEMENT,
    )


def preflight(**changes: object) -> BrokerPreflight:
    values: dict[str, object] = {
        "environment_verified": True,
        "account_verified": True,
        "account_active": True,
        "account_unblocked": True,
        "reconciliation_complete": True,
        "broker_clock_verified": True,
        "market_data_fresh": True,
        "durable_journal_ready": True,
        "secret_source_secure": True,
    }
    values.update(changes)
    return BrokerPreflight(**values)  # type: ignore[arg-type]


def context() -> ArmedExecutionContext:
    return ExecutionGate.arm_paper(
        requested_mode="paper",
        record=record(),
        expected_namespace="edge",
        expected_fingerprint=fingerprint(),
        preflight=preflight(),
        now=NOW + timedelta(minutes=1),
    )


def intent_decision() -> tuple[OrderIntent, RiskDecision]:
    intent = OrderIntent.create(
        correlation_id="corr-edge",
        timestamp=NOW,
        symbol="SPY",
        side=Side.BUY,
        quantity=4,
        reference_price=Decimal("500"),
        rationale="edge",
    )
    decision = RiskDecision(
        allowed=True,
        reason="approved",
        approved_quantity=4,
        intent_id=intent.intent_id,
        correlation_id=intent.correlation_id,
    )
    return intent, decision


def order_snapshot(
    *,
    status: BrokerOrderStatus = BrokerOrderStatus.NEW,
    filled: Decimal = Decimal("0"),
    average: Decimal | None = None,
    updated: datetime = NOW,
) -> BrokerOrderSnapshot:
    return BrokerOrderSnapshot(
        broker_order_id="broker-edge",
        client_order_id=CLIENT_ID,
        status=status,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("10"),
        filled_quantity=filled,
        average_fill_price=average,
        submitted_at=NOW,
        updated_at=updated,
        raw_payload_sha256=A,
    )


def test_execution_constructors_reject_invalid_time_digest_and_identity() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ArmingRecord.issue_paper(
            strategy_namespace="edge",
            fingerprint=fingerprint(),
            issued_at=datetime(2026, 8, 12),
            ttl=timedelta(hours=1),
            acknowledgement=PAPER_ACKNOWLEDGEMENT,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        ExecutionFingerprint("bad", B, C)
    with pytest.raises(ValueError, match="strategy_namespace"):
        ArmingRecord.issue_paper(
            strategy_namespace="bad namespace!",
            fingerprint=fingerprint(),
            issued_at=NOW,
            ttl=timedelta(hours=1),
            acknowledgement=PAPER_ACKNOWLEDGEMENT,
        )
    with pytest.raises(ValueError, match="only for paper"):
        ArmingRecord(
            record_id="x",
            environment=ExecutionMode.SIMULATION,
            strategy_namespace="edge",
            fingerprint=fingerprint(),
            issued_at=NOW,
            expires_at=NOW + timedelta(hours=1),
            acknowledgement_sha256=A,
        )
    with pytest.raises(ValueError, match="after issuance"):
        ArmingRecord(
            record_id="x",
            environment=ExecutionMode.PAPER,
            strategy_namespace="edge",
            fingerprint=fingerprint(),
            issued_at=NOW,
            expires_at=NOW,
            acknowledgement_sha256=A,
        )
    with pytest.raises(ValueError, match="record_id"):
        ArmingRecord(
            record_id="",
            environment=ExecutionMode.PAPER,
            strategy_namespace="edge",
            fingerprint=fingerprint(),
            issued_at=NOW,
            expires_at=NOW + timedelta(hours=1),
            acknowledgement_sha256=A,
        )
    with pytest.raises(ValueError, match="must not be negative"):
        preflight(unresolved_submissions=-1)
    assert preflight().as_dict()["ready"] is True


def test_execution_gate_rejects_unknown_modes_and_all_fingerprint_drift() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        ExecutionGate.require_simulation("unknown")
    with pytest.raises(ValueError, match="unsupported"):
        ExecutionGate.arm_paper(
            requested_mode="unknown",
            record=record(),
            expected_namespace="edge",
            expected_fingerprint=fingerprint(),
            preflight=preflight(),
            now=NOW,
        )
    with pytest.raises(ValueError, match="requires requested mode"):
        ExecutionGate.arm_paper(
            requested_mode="simulation",
            record=record(),
            expected_namespace="edge",
            expected_fingerprint=fingerprint(),
            preflight=preflight(),
            now=NOW,
        )
    for field_name, changed, expected_message in (
        ("configuration_sha256", D, "configuration fingerprint"),
        ("account_sha256", D, "account fingerprint"),
    ):
        changed_fingerprint = replace(fingerprint(), **{field_name: changed})
        with pytest.raises(ValueError, match=expected_message):
            ExecutionGate.arm_paper(
                requested_mode="paper",
                record=record(),
                expected_namespace="edge",
                expected_fingerprint=changed_fingerprint,
                preflight=preflight(),
                now=NOW,
            )
    armed = context()
    assert armed.as_dict()["environment"] == "paper"
    with pytest.raises(ValueError, match="only paper"):
        ArmedExecutionContext(
            record_id="x",
            environment=ExecutionMode.LIVE,
            strategy_namespace="edge",
            fingerprint=fingerprint(),
            armed_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
    with pytest.raises(ValueError, match="remain valid"):
        ArmedExecutionContext(
            record_id="x",
            environment=ExecutionMode.PAPER,
            strategy_namespace="edge",
            fingerprint=fingerprint(),
            armed_at=NOW,
            expires_at=NOW,
        )


def test_account_clock_and_position_contracts_reject_impossible_state() -> None:
    account = BrokerAccountSnapshot(
        environment=ExecutionMode.PAPER,
        account_sha256=C,
        status=AccountStatus.ACTIVE,
        trading_blocked=False,
        account_blocked=False,
        transfers_blocked=False,
        cash=Decimal("1"),
        equity=Decimal("2"),
        buying_power=Decimal("1"),
        captured_at=NOW,
        raw_payload_sha256=A,
    )
    assert account.as_dict()["permits_new_exposure"] is True
    with pytest.raises(ValueError, match="paper or live"):
        replace(account, environment=ExecutionMode.SIMULATION)
    margin_account = replace(account, cash=Decimal("-1"))
    assert margin_account.permits_new_exposure is False
    with pytest.raises(ValueError, match="finite"):
        replace(account, cash=Decimal("NaN"))

    with pytest.raises(ValueError, match="future next_close"):
        BrokerClockSnapshot(
            is_open=True,
            timestamp=NOW,
            next_open=NOW + timedelta(days=1),
            next_close=NOW,
            raw_payload_sha256=A,
        )
    with pytest.raises(ValueError, match="next_open in the past"):
        BrokerClockSnapshot(
            is_open=False,
            timestamp=NOW,
            next_open=NOW - timedelta(seconds=1),
            next_close=NOW + timedelta(days=1),
            raw_payload_sha256=A,
        )

    position = BrokerPositionSnapshot(
        symbol="SPY",
        quantity=Decimal("2"),
        average_entry_price=Decimal("490"),
        market_price=Decimal("500"),
        market_value=Decimal("1000"),
        unrealized_pnl=Decimal("20"),
        captured_at=NOW,
        raw_payload_sha256=A,
    )
    assert position.as_dict()["symbol"] == "SPY"
    with pytest.raises(ValueError, match="symbol"):
        replace(position, symbol="")
    with pytest.raises(ValueError, match="average_entry_price"):
        replace(position, average_entry_price=Decimal("0"))


def test_order_and_fill_contract_edges() -> None:
    intent, decision = intent_decision()
    with pytest.raises(ValueError, match="does not belong"):
        ApprovedBrokerOrder.from_approved_intent(
            context=context(),
            intent=intent,
            decision=replace(decision, correlation_id="different"),
            order_type=BrokerOrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            limit_price=None,
        )
    with pytest.raises(ValueError, match="requires limit_price"):
        ApprovedBrokerOrder.from_approved_intent(
            context=context(),
            intent=intent,
            decision=decision,
            order_type=BrokerOrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=None,
        )
    with pytest.raises(ValueError, match="must not define"):
        ApprovedBrokerOrder.from_approved_intent(
            context=context(),
            intent=intent,
            decision=decision,
            order_type=BrokerOrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            limit_price=Decimal("500"),
        )
    with pytest.raises(ValueError, match="positive"):
        deterministic_client_order_id(
            strategy_namespace="edge",
            account_sha256=C,
            intent=intent,
            approved_quantity=0,
        )

    valid = order_snapshot()
    assert valid.as_dict()["remaining_quantity"] == "10"
    with pytest.raises(ValueError, match="broker_order_id"):
        replace(valid, broker_order_id="")
    with pytest.raises(ValueError, match="must not exceed"):
        replace(valid, filled_quantity=Decimal("11"), average_fill_price=Decimal("500"))
    with pytest.raises(ValueError, match="requires average_fill_price"):
        replace(valid, status=BrokerOrderStatus.PARTIALLY_FILLED, filled_quantity=Decimal("1"))
    with pytest.raises(ValueError, match="must not have"):
        replace(valid, average_fill_price=Decimal("500"))
    with pytest.raises(ValueError, match="must not precede"):
        replace(valid, updated_at=NOW - timedelta(seconds=1))

    fill = BrokerFillActivity(
        activity_id="a1",
        execution_id="e1",
        broker_order_id="broker-edge",
        client_order_id=CLIENT_ID,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("500"),
        fee=Decimal("0"),
        timestamp=NOW,
        raw_payload_sha256=A,
    )
    assert fill.as_dict()["execution_id"] == "e1"
    with pytest.raises(ValueError, match="execution_id"):
        replace(fill, execution_id="")
    with pytest.raises(ValueError, match="fee"):
        replace(fill, fee=Decimal("-1"))
    with pytest.raises(ValueError, match="SHA-256"):
        BrokerActivityPage(activities=(fill,), next_page_token=None, raw_payload_sha256="bad")

    cancel = BrokerCancelResult(
        broker_order_id="broker-edge",
        requested_at=NOW,
        accepted=False,
        observed_status=BrokerOrderStatus.NEW,
        raw_payload_sha256=A,
    )
    assert cancel.verified_terminal is False
    with pytest.raises(ValueError, match="broker_order_id"):
        replace(cancel, broker_order_id="")


def test_journal_and_transition_identity_guards() -> None:
    journal = SubmissionJournalEntry(
        sequence=1,
        client_order_id=CLIENT_ID,
        requested_payload_sha256=A,
        state=SubmissionState.PERSISTED,
        created_at=NOW,
        updated_at=NOW,
    )
    with pytest.raises(ValueError, match="positive"):
        replace(journal, sequence=0)
    with pytest.raises(ValueError, match="allowed only"):
        replace(journal, broker_order_id="broker-edge")
    with pytest.raises(ValueError, match="must not precede"):
        replace(journal, updated_at=NOW - timedelta(seconds=1))

    previous = order_snapshot(updated=NOW + timedelta(seconds=1))
    current = order_snapshot(
        status=BrokerOrderStatus.PENDING_CANCEL,
        updated=NOW + timedelta(seconds=2),
    )
    with pytest.raises(ValueError, match="broker_order_id changed"):
        BrokerOrderStateMachine.require_transition(
            previous, replace(current, broker_order_id="other")
        )
    with pytest.raises(ValueError, match="client_order_id changed"):
        BrokerOrderStateMachine.require_transition(
            previous,
            replace(current, client_order_id="qt-other-0123456789abcdef01234567"),
        )
    with pytest.raises(ValueError, match="symbol or side"):
        BrokerOrderStateMachine.require_transition(previous, replace(current, side=Side.SELL))
    with pytest.raises(ValueError, match="quantity changed"):
        BrokerOrderStateMachine.require_transition(
            previous, replace(current, quantity=Decimal("9"))
        )
    with pytest.raises(ValueError, match="out-of-order"):
        BrokerOrderStateMachine.require_transition(
            previous,
            replace(current, updated_at=NOW),
        )
    invalid = replace(
        current,
        status=BrokerOrderStatus.ACCEPTED_FOR_BIDDING,
    )
    with pytest.raises(ValueError, match="invalid broker order transition"):
        BrokerOrderStateMachine.require_transition(previous, invalid)
