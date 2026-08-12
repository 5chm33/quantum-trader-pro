from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantum_trader.adapters.sqlite_broker_journal import SQLiteBrokerJournal
from quantum_trader.application.reconciliation import PaperReconciler, ReconciliationError
from quantum_trader.domain.brokerage import (
    AccountStatus,
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerActivityPage,
    BrokerCancelResult,
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
RAW = "e" * 64


def context() -> ArmedExecutionContext:
    fingerprint = ExecutionFingerprint(A, B, C)
    record = ArmingRecord.issue_paper(
        strategy_namespace="qtpro-paper",
        fingerprint=fingerprint,
        issued_at=NOW,
        ttl=timedelta(hours=1),
        acknowledgement=PAPER_ACKNOWLEDGEMENT,
    )
    return ExecutionGate.arm_paper(
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


def approved_order() -> ApprovedBrokerOrder:
    intent = OrderIntent.create(
        correlation_id="reconciliation-correlation",
        timestamp=NOW,
        symbol="SPY",
        side=Side.BUY,
        quantity=4,
        reference_price=Decimal("500"),
        rationale="reconciliation fixture",
    )
    decision = RiskDecision(
        allowed=True,
        reason="within_limits",
        approved_quantity=4,
        intent_id=intent.intent_id,
        correlation_id=intent.correlation_id,
    )
    return ApprovedBrokerOrder.from_approved_intent(
        context=context(),
        intent=intent,
        decision=decision,
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        limit_price=Decimal("499.50"),
    )


def account(*, cash: Decimal = Decimal("8000"), account_sha256: str = C) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        environment=ExecutionMode.PAPER,
        account_sha256=account_sha256,
        status=AccountStatus.ACTIVE,
        trading_blocked=False,
        account_blocked=False,
        transfers_blocked=False,
        cash=cash,
        equity=Decimal("10000"),
        buying_power=Decimal("8000"),
        captured_at=NOW,
        raw_payload_sha256=RAW,
    )


def broker_order(
    order: ApprovedBrokerOrder,
    *,
    status: BrokerOrderStatus = BrokerOrderStatus.FILLED,
    client_order_id: str | None = None,
    broker_order_id: str = "broker-order-1",
) -> BrokerOrderSnapshot:
    filled = Decimal(order.quantity) if status is BrokerOrderStatus.FILLED else Decimal("0")
    return BrokerOrderSnapshot(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id or order.client_order_id,
        status=status,
        symbol=order.symbol,
        side=order.side,
        quantity=Decimal(order.quantity),
        filled_quantity=filled,
        average_fill_price=Decimal("500") if filled else None,
        submitted_at=NOW,
        updated_at=NOW + timedelta(seconds=2),
        raw_payload_sha256=RAW,
    )


def position(quantity: Decimal = Decimal("4"), *, symbol: str = "SPY") -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        symbol=symbol,
        quantity=quantity,
        average_entry_price=Decimal("500"),
        market_price=Decimal("501"),
        market_value=quantity * Decimal("501"),
        unrealized_pnl=quantity,
        captured_at=NOW + timedelta(seconds=3),
        raw_payload_sha256=RAW,
    )


def fill(order: ApprovedBrokerOrder, *, client_order_id: str | None = None) -> BrokerFillActivity:
    return BrokerFillActivity(
        activity_id="20260812143000000::execution-1",
        execution_id="20260812143000000::execution-1",
        broker_order_id="broker-order-1",
        client_order_id=client_order_id,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal("4"),
        price=Decimal("500"),
        fee=None,
        timestamp=NOW + timedelta(seconds=2),
        raw_payload_sha256=RAW,
    )


@dataclass(slots=True)
class FakeBroker:
    account_snapshot: BrokerAccountSnapshot
    positions: tuple[BrokerPositionSnapshot, ...] = ()
    open_orders: tuple[BrokerOrderSnapshot, ...] = ()
    orders_by_client: dict[str, BrokerOrderSnapshot] = field(default_factory=dict)
    orders_by_id: dict[str, BrokerOrderSnapshot] = field(default_factory=dict)
    activity_pages: dict[str | None, BrokerActivityPage] = field(default_factory=dict)
    environment: ExecutionMode = ExecutionMode.PAPER
    account_sha256: str = C
    requested_page_tokens: list[str | None] = field(default_factory=list)

    def get_account(self) -> BrokerAccountSnapshot:
        return self.account_snapshot

    def get_clock(self):
        raise AssertionError("clock is not part of phase-nine reconciliation")

    def list_positions(self):
        return self.positions

    def list_open_orders(self):
        return self.open_orders

    def get_order_by_client_id(self, client_order_id: str):
        return self.orders_by_client.get(client_order_id)

    def get_order_by_id(self, broker_order_id: str):
        return self.orders_by_id.get(broker_order_id)

    def submit_once(
        self,
        *,
        context: ArmedExecutionContext,
        order: ApprovedBrokerOrder,
        submission_journal_sequence: int,
    ):
        del context, order, submission_journal_sequence
        raise AssertionError("reconciliation must never submit an order")

    def cancel_order(
        self,
        *,
        context: ArmedExecutionContext,
        broker_order_id: str,
    ) -> BrokerCancelResult:
        del context, broker_order_id
        raise AssertionError("reconciliation must never cancel an order")

    def list_fill_activities(
        self,
        *,
        after: datetime | None,
        page_token: str | None,
        page_size: int,
    ) -> BrokerActivityPage:
        assert after == NOW - timedelta(days=1)
        assert page_size in {1, 100}
        self.requested_page_tokens.append(page_token)
        return self.activity_pages.get(
            page_token,
            BrokerActivityPage(activities=(), next_page_token=None, raw_payload_sha256=RAW),
        )


def test_reconciliation_resolves_submission_enriches_fill_and_is_idempotent(tmp_path) -> None:
    approved = approved_order()
    normalized_order = broker_order(approved)
    unresolved_fill = fill(approved)
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
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
    page = BrokerActivityPage(
        activities=(unresolved_fill,),
        next_page_token=None,
        raw_payload_sha256=RAW,
    )
    broker = FakeBroker(
        account_snapshot=account(),
        positions=(position(),),
        orders_by_client={approved.client_order_id: normalized_order},
        orders_by_id={normalized_order.broker_order_id: normalized_order},
        activity_pages={None: page},
    )
    reconciler = PaperReconciler(
        broker=broker,
        journal=journal,
        strategy_namespace="qtpro-paper",
        activity_after=NOW - timedelta(days=1),
    )

    report = reconciler.reconcile(timestamp=NOW + timedelta(minutes=2))
    assert report.ready is True
    assert report.resolved_submission_count == 1
    assert report.unresolved_submission_count == 0
    assert report.new_execution_count == 1
    assert report.expected_positions == {"SPY": Decimal("4")}
    assert report.broker_positions == {"SPY": Decimal("4")}
    assert journal.unresolved_submissions() == ()
    persisted_fill = journal.all_fills()[0]
    assert persisted_fill.client_order_id == approved.client_order_id
    assert journal.activity_checkpoint() == unresolved_fill.activity_id

    second = reconciler.reconcile(timestamp=NOW + timedelta(minutes=3))
    assert second.ready is True
    assert second.activity_count == 0
    assert second.new_execution_count == 0
    assert broker.requested_page_tokens == [None, unresolved_fill.activity_id]
    assert len(journal.all_fills()) == 1
    journal.close()


def test_reconciliation_disarms_foreign_orders_unknown_states_and_position_drift(
    tmp_path,
) -> None:
    approved = approved_order()
    foreign = broker_order(
        approved,
        status=BrokerOrderStatus.UNKNOWN,
        client_order_id="manual:desk.order-1",
        broker_order_id="foreign-order-1",
    )
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    broker = FakeBroker(
        account_snapshot=account(cash=Decimal("-1")),
        positions=(position(quantity=Decimal("2")),),
        open_orders=(foreign,),
    )
    report = PaperReconciler(
        broker=broker,
        journal=journal,
        strategy_namespace="qtpro-paper",
        activity_after=NOW - timedelta(days=1),
    ).reconcile(timestamp=NOW + timedelta(minutes=2))

    assert report.ready is False
    codes = {issue.code for issue in report.issues}
    assert {
        "foreign_open_order",
        "unknown_order_status",
        "position_mismatch",
        "account_not_permitted",
    } <= codes
    assert report.account_permits_new_exposure is False
    journal.close()


def test_reconciliation_detects_unexplained_owned_orders_and_missing_activity_order(
    tmp_path,
) -> None:
    approved = approved_order()
    unexplained = broker_order(approved, status=BrokerOrderStatus.NEW)
    missing_order_fill = fill(approved)
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    broker = FakeBroker(
        account_snapshot=account(),
        open_orders=(unexplained,),
        activity_pages={
            None: BrokerActivityPage(
                activities=(missing_order_fill,),
                next_page_token=None,
                raw_payload_sha256=RAW,
            )
        },
    )
    report = PaperReconciler(
        broker=broker,
        journal=journal,
        strategy_namespace="qtpro-paper",
        activity_after=NOW - timedelta(days=1),
    ).reconcile(timestamp=NOW + timedelta(minutes=2))

    assert report.ready is False
    codes = {issue.code for issue in report.issues}
    assert "unexplained_owned_order" in codes
    assert "activity_order_missing" not in codes
    assert journal.all_fills()[0].client_order_id == approved.client_order_id
    journal.close()

    journal = SQLiteBrokerJournal(tmp_path / "missing.db")
    broker = FakeBroker(
        account_snapshot=account(),
        activity_pages={
            None: BrokerActivityPage(
                activities=(missing_order_fill,),
                next_page_token=None,
                raw_payload_sha256=RAW,
            )
        },
    )
    report = PaperReconciler(
        broker=broker,
        journal=journal,
        strategy_namespace="qtpro-paper",
        activity_after=NOW - timedelta(days=1),
    ).reconcile(timestamp=NOW + timedelta(minutes=2))
    assert "activity_order_missing" in {issue.code for issue in report.issues}
    journal.close()


def test_reconciliation_rejects_stalled_pagination_and_wrong_broker_identity(tmp_path) -> None:
    journal = SQLiteBrokerJournal(tmp_path / "broker.db")
    stalled_cursor = "-".join(("same", "cursor"))
    stalled_page = BrokerActivityPage(
        activities=(),
        next_page_token=stalled_cursor,
        raw_payload_sha256=RAW,
    )
    broker = FakeBroker(
        account_snapshot=account(),
        activity_pages={None: stalled_page, stalled_cursor: stalled_page},
    )
    reconciler = PaperReconciler(
        broker=broker,
        journal=journal,
        strategy_namespace="qtpro-paper",
        activity_after=NOW - timedelta(days=1),
        page_size=1,
    )
    with pytest.raises(ReconciliationError, match="did not advance"):
        reconciler.reconcile(timestamp=NOW + timedelta(minutes=2))
    assert journal.activity_checkpoint() is None

    wrong_account_broker = FakeBroker(
        account_snapshot=account(account_sha256=D),
        account_sha256=C,
    )
    with pytest.raises(ReconciliationError, match="fingerprint changed"):
        PaperReconciler(
            broker=wrong_account_broker,
            journal=journal,
            strategy_namespace="qtpro-paper",
            activity_after=NOW - timedelta(days=1),
        ).reconcile(timestamp=NOW + timedelta(minutes=2))

    live_broker = replace(broker, environment=ExecutionMode.LIVE)
    with pytest.raises(ValueError, match="paper broker"):
        PaperReconciler(
            broker=live_broker,
            journal=journal,
            strategy_namespace="qtpro-paper",
            activity_after=NOW - timedelta(days=1),
        )
    journal.close()
