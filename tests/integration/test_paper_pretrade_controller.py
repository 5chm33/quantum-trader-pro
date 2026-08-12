from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quantum_trader.application.paper_controls import (
    PaperControlAssessmentError,
    PaperPreTradeController,
)
from quantum_trader.application.reconciliation import (
    IssueSeverity,
    ReconciliationIssue,
    ReconciliationReport,
)
from quantum_trader.domain.brokerage import (
    AccountStatus,
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerClockSnapshot,
    BrokerOrderType,
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
from quantum_trader.domain.market_controls import (
    AssetTradingSnapshot,
    LatestQuoteSnapshot,
    MarketCalendarDay,
    PaperControlLimits,
)
from quantum_trader.domain.models import OrderIntent, RiskDecision, Side
from quantum_trader.domain.operator import OperatorControlState

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
EASTERN = ZoneInfo("America/New_York")
ACCOUNT_SHA = "a" * 64
RAW = "b" * 64


def approved_order() -> ApprovedBrokerOrder:
    fingerprint = ExecutionFingerprint("c" * 64, "d" * 64, ACCOUNT_SHA)
    record = ArmingRecord.issue_paper(
        strategy_namespace="qtpro-paper",
        fingerprint=fingerprint,
        issued_at=NOW - timedelta(minutes=5),
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
        now=NOW - timedelta(minutes=4),
    )
    intent = OrderIntent.create(
        correlation_id="controller-order",
        timestamp=NOW - timedelta(seconds=2),
        symbol="SPY",
        side=Side.BUY,
        quantity=10,
        reference_price=Decimal("100"),
        rationale="controller fixture",
    )
    decision = RiskDecision(
        allowed=True,
        reason="within_limits",
        approved_quantity=10,
        intent_id=intent.intent_id,
        correlation_id=intent.correlation_id,
    )
    return ApprovedBrokerOrder.from_approved_intent(
        context=context,
        intent=intent,
        decision=decision,
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        limit_price=Decimal("101"),
    )


def account(*, account_sha256: str = ACCOUNT_SHA) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        environment=ExecutionMode.PAPER,
        account_sha256=account_sha256,
        status=AccountStatus.ACTIVE,
        trading_blocked=False,
        account_blocked=False,
        transfers_blocked=False,
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        buying_power=Decimal("10000"),
        captured_at=NOW - timedelta(seconds=1),
        raw_payload_sha256=RAW,
    )


def clock() -> BrokerClockSnapshot:
    return BrokerClockSnapshot(
        is_open=True,
        timestamp=NOW - timedelta(seconds=1),
        next_open=NOW + timedelta(days=1),
        next_close=datetime(2026, 8, 12, 16, 0, tzinfo=EASTERN),
        raw_payload_sha256=RAW,
    )


def calendar() -> MarketCalendarDay:
    return MarketCalendarDay(
        trade_date=date(2026, 8, 12),
        regular_open=datetime(2026, 8, 12, 9, 30, tzinfo=EASTERN),
        regular_close=datetime(2026, 8, 12, 16, 0, tzinfo=EASTERN),
        session_open=datetime(2026, 8, 12, 4, 0, tzinfo=EASTERN),
        session_close=datetime(2026, 8, 12, 20, 0, tzinfo=EASTERN),
        raw_payload_sha256=RAW,
    )


def asset() -> AssetTradingSnapshot:
    return AssetTradingSnapshot(
        symbol="SPY",
        asset_class="us_equity",
        status="active",
        tradable=True,
        fractionable=True,
        marginable=True,
        shortable=True,
        borrow_status="easy_to_borrow",
        captured_at=NOW - timedelta(seconds=1),
        raw_payload_sha256=RAW,
    )


def quote() -> LatestQuoteSnapshot:
    return LatestQuoteSnapshot(
        symbol="SPY",
        bid_price=Decimal("100.00"),
        bid_size=10,
        ask_price=Decimal("100.02"),
        ask_size=12,
        timestamp=NOW - timedelta(seconds=1),
        feed="iex",
        raw_payload_sha256=RAW,
    )


def reconciliation(*, ready: bool = True) -> ReconciliationReport:
    issues = ()
    if not ready:
        issues = (
            ReconciliationIssue(
                code="foreign_open_order",
                severity=IssueSeverity.CRITICAL,
                subject_sha256=RAW,
            ),
        )
    return ReconciliationReport(
        timestamp=NOW - timedelta(seconds=1),
        ready=ready,
        account_sha256=ACCOUNT_SHA,
        account_permits_new_exposure=True,
        open_order_count=0,
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
class FakeReconciler:
    report: ReconciliationReport
    calls: list[datetime] = field(default_factory=list)

    def reconcile(self, *, timestamp: datetime) -> ReconciliationReport:
        self.calls.append(timestamp)
        return self.report


@dataclass(slots=True)
class FakeBroker:
    account_snapshot: BrokerAccountSnapshot
    environment: ExecutionMode = ExecutionMode.PAPER
    account_sha256: str = ACCOUNT_SHA

    def get_account(self) -> BrokerAccountSnapshot:
        return self.account_snapshot

    def get_clock(self) -> BrokerClockSnapshot:
        return clock()

    @staticmethod
    def list_positions():
        return ()

    @staticmethod
    def list_open_orders():
        return ()


@dataclass(slots=True)
class FakeOperatorControl:
    paused: bool = False

    def current_state(self) -> OperatorControlState:
        return OperatorControlState(
            paused=self.paused,
            sequence=1,
            changed_at=NOW - timedelta(seconds=1),
            reason_code="test_state",
            reason_sha256=RAW,
        )


@dataclass(slots=True)
class FakeJournal:
    timestamps: tuple[datetime, ...] = ()

    def submission_timestamps(self):
        return self.timestamps


@dataclass(slots=True)
class FakeControlData:
    calendar_day: MarketCalendarDay | None
    quote_snapshot: LatestQuoteSnapshot = field(default_factory=quote)

    @staticmethod
    def get_asset(symbol: str) -> AssetTradingSnapshot:
        assert symbol == "SPY"
        return asset()

    def get_calendar_day(self, trade_date: date) -> MarketCalendarDay | None:
        assert trade_date == date(2026, 8, 12)
        return self.calendar_day

    def get_latest_quote(self, symbol: str) -> LatestQuoteSnapshot:
        assert symbol == "SPY"
        return self.quote_snapshot


def controller(
    *,
    broker: FakeBroker | None = None,
    journal: FakeJournal | None = None,
    controls: FakeControlData | None = None,
    reconciler: FakeReconciler | None = None,
    operator_control: FakeOperatorControl | None = None,
) -> PaperPreTradeController:
    return PaperPreTradeController(
        broker=broker or FakeBroker(account()),  # type: ignore[arg-type]
        journal=journal or FakeJournal(),  # type: ignore[arg-type]
        control_data=controls or FakeControlData(calendar()),
        reconciler=reconciler or FakeReconciler(reconciliation()),
        operator_control=operator_control or FakeOperatorControl(),
        strategy_namespace="qtpro-paper",
        limits=PaperControlLimits(),
    )


def test_controller_assembles_a_complete_fresh_allowed_assessment() -> None:
    reconciler = FakeReconciler(reconciliation())
    assessment = controller(reconciler=reconciler).assess(
        order=approved_order(),
        timestamp=NOW,
    )
    assert assessment.ready is True
    assert assessment.decision.allowed is True
    assert assessment.decision.candidate_notional == Decimal("1010")
    assert reconciler.calls == [NOW]


def test_paused_operator_switch_stops_before_reconciliation_or_market_reads() -> None:
    reconciler = FakeReconciler(reconciliation())
    assessment = controller(
        reconciler=reconciler,
        operator_control=FakeOperatorControl(paused=True),
    ).assess(order=approved_order(), timestamp=NOW)
    assert assessment.ready is False
    assert assessment.reconciliation is None
    assert assessment.decision.reasons == ("operator_paused",)
    assert reconciler.calls == []


def test_controller_stops_before_market_reads_when_reconciliation_is_not_ready() -> None:
    report = reconciliation(ready=False)
    assessment = controller(reconciler=FakeReconciler(report)).assess(
        order=approved_order(),
        timestamp=NOW,
    )
    assert assessment.ready is False
    assert assessment.decision.reasons == ("reconciliation_not_ready",)


def test_controller_denies_holidays_and_stale_quotes() -> None:
    holiday = controller(controls=FakeControlData(None)).assess(
        order=approved_order(),
        timestamp=NOW,
    )
    assert holiday.decision.reasons == ("market_calendar_unavailable",)

    stale = replace(quote(), timestamp=NOW - timedelta(seconds=6))
    assessment = controller(controls=FakeControlData(calendar(), quote_snapshot=stale)).assess(
        order=approved_order(), timestamp=NOW
    )
    assert assessment.ready is False
    assert "quote_stale" in assessment.decision.reasons


def test_controller_rejects_account_drift_live_broker_and_naive_timestamp() -> None:
    with pytest.raises(PaperControlAssessmentError, match="changed after"):
        controller(
            broker=FakeBroker(account(account_sha256="f" * 64)),
        ).assess(order=approved_order(), timestamp=NOW)

    with pytest.raises(ValueError, match="paper broker"):
        controller(
            broker=FakeBroker(account(), environment=ExecutionMode.LIVE),
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        controller().assess(
            order=approved_order(),
            timestamp=datetime.combine(date(2026, 8, 12), time(14, 30)),
        )


def test_controller_uses_durable_submission_history_for_burst_limits() -> None:
    timestamps = tuple(NOW - timedelta(seconds=30) for _ in range(5))
    assessment = controller(journal=FakeJournal(timestamps)).assess(
        order=approved_order(),
        timestamp=NOW,
    )
    assert assessment.ready is False
    assert "order_rate_limit" in assessment.decision.reasons
