from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from quantum_trader.adapters.sqlite_broker_journal import SQLiteBrokerJournal
from quantum_trader.adapters.sqlite_operator_control import SQLiteOperatorControl
from quantum_trader.application.paper_controls import PaperPreTradeAssessment
from quantum_trader.application.paper_execution import (
    PaperExecutionBoundary,
    PaperOrderExecutor,
    approved_order_payload_sha256,
)
from quantum_trader.application.reconciliation import PaperReconciler, ReconciliationReport
from quantum_trader.domain.brokerage import (
    AccountStatus,
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerActivityPage,
    BrokerCancelResult,
    BrokerClockSnapshot,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerPositionSnapshot,
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
from quantum_trader.domain.market_controls import PaperControlDecision
from quantum_trader.domain.models import OrderIntent, RiskDecision, Side
from quantum_trader.domain.operator import ACKNOWLEDGEMENTS, OperatorAction, OperatorApproval

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
NAMESPACE = "qtpro-paper"
CODE_SHA256 = "a" * 64
CONFIG_SHA256 = "b" * 64
ACCOUNT_SHA256 = "c" * 64
RAW_SHA256 = "d" * 64
ATTEMPT_SHA256 = "e" * 64
CONTROL_KEY = b"k" * 32


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def context() -> ArmedExecutionContext:
    fingerprint = ExecutionFingerprint(CODE_SHA256, CONFIG_SHA256, ACCOUNT_SHA256)
    record = ArmingRecord.issue_paper(
        strategy_namespace=NAMESPACE,
        fingerprint=fingerprint,
        issued_at=NOW - timedelta(minutes=5),
        ttl=timedelta(hours=1),
        acknowledgement=PAPER_ACKNOWLEDGEMENT,
    )
    return ExecutionGate.arm_paper(
        requested_mode=ExecutionMode.PAPER,
        record=record,
        expected_namespace=NAMESPACE,
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
        now=NOW,
    )


def approved_order() -> ApprovedBrokerOrder:
    intent = OrderIntent.create(
        correlation_id="literal-process-crash",
        timestamp=NOW - timedelta(seconds=2),
        symbol="SPY",
        side=Side.BUY,
        quantity=1,
        reference_price=Decimal("500"),
        rationale="literal subprocess crash fixture",
    )
    decision = RiskDecision(
        allowed=True,
        reason="within_limits",
        approved_quantity=1,
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


def account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        environment=ExecutionMode.PAPER,
        account_sha256=ACCOUNT_SHA256,
        status=AccountStatus.ACTIVE,
        trading_blocked=False,
        account_blocked=False,
        transfers_blocked=False,
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        buying_power=Decimal("10000"),
        captured_at=NOW,
        raw_payload_sha256=RAW_SHA256,
    )


def order_snapshot(order: ApprovedBrokerOrder) -> BrokerOrderSnapshot:
    payload = {
        "broker_order_id": "broker-process-1",
        "client_order_id": order.client_order_id,
        "status": "new",
    }
    return BrokerOrderSnapshot(
        broker_order_id="broker-process-1",
        client_order_id=order.client_order_id,
        status=BrokerOrderStatus.NEW,
        symbol=order.symbol,
        side=order.side,
        quantity=Decimal(order.quantity),
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        submitted_at=NOW,
        updated_at=NOW,
        raw_payload_sha256=_canonical_sha256(payload),
        limit_price=order.limit_price,
    )


@dataclass(slots=True)
class FilePaperBroker:
    path: Path
    environment: ExecutionMode = ExecutionMode.PAPER
    account_sha256: str = ACCOUNT_SHA256

    def verify_context(self, armed_context: ArmedExecutionContext) -> None:
        if armed_context.environment is not ExecutionMode.PAPER:
            raise RuntimeError("fixture broker accepts only paper context")
        if armed_context.fingerprint.account_sha256 != self.account_sha256:
            raise RuntimeError("fixture account fingerprint changed")

    def get_account(self) -> BrokerAccountSnapshot:
        return account()

    def get_clock(self) -> BrokerClockSnapshot:
        raise AssertionError("clock is not used by this acceptance worker")

    def list_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
        return ()

    def list_open_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
        retained = self._read()
        return () if retained is None else (retained,)

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        retained = self._read()
        if retained is None or retained.client_order_id != client_order_id:
            return None
        return retained

    def get_order_by_id(self, broker_order_id: str) -> BrokerOrderSnapshot | None:
        retained = self._read()
        if retained is None or retained.broker_order_id != broker_order_id:
            return None
        return retained

    def submit_once(
        self,
        *,
        context: ArmedExecutionContext,
        order: ApprovedBrokerOrder,
        submission_journal_sequence: int,
    ) -> BrokerOrderSnapshot:
        self.verify_context(context)
        if submission_journal_sequence <= 0:
            raise RuntimeError("durable journal sequence is required")
        if self.path.exists():
            raise RuntimeError("duplicate fake broker submission")
        snapshot = order_snapshot(order)
        payload = {
            "submission_count": 1,
            "order": snapshot.as_dict(),
        }
        self.path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        self.path.chmod(0o600)
        descriptor = os.open(self.path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return snapshot

    def cancel_order(
        self,
        *,
        context: ArmedExecutionContext,
        broker_order_id: str,
    ) -> BrokerCancelResult:
        del context, broker_order_id
        raise AssertionError("cancel is not used by this acceptance worker")

    def list_fill_activities(
        self,
        *,
        after: datetime | None,
        page_token: str | None,
        page_size: int,
    ) -> BrokerActivityPage:
        del after, page_token, page_size
        return BrokerActivityPage(
            activities=(),
            next_page_token=None,
            raw_payload_sha256=RAW_SHA256,
        )

    def _read(self) -> BrokerOrderSnapshot | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        order_payload = payload["order"]
        return BrokerOrderSnapshot(
            broker_order_id=str(order_payload["broker_order_id"]),
            client_order_id=str(order_payload["client_order_id"]),
            status=BrokerOrderStatus(str(order_payload["status"])),
            symbol=str(order_payload["symbol"]),
            side=Side(str(order_payload["side"])),
            quantity=Decimal(str(order_payload["quantity"])),
            filled_quantity=Decimal(str(order_payload["filled_quantity"])),
            average_fill_price=(
                Decimal(str(order_payload["average_fill_price"]))
                if order_payload["average_fill_price"] is not None
                else None
            ),
            submitted_at=datetime.fromisoformat(str(order_payload["submitted_at"])),
            updated_at=datetime.fromisoformat(str(order_payload["updated_at"])),
            raw_payload_sha256=str(order_payload["raw_payload_sha256"]),
            limit_price=(
                Decimal(str(order_payload["limit_price"]))
                if order_payload["limit_price"] is not None
                else None
            ),
        )


@dataclass(slots=True)
class ReadyPreTrade:
    operator_control: SQLiteOperatorControl

    def assess(
        self,
        *,
        order: ApprovedBrokerOrder,
        timestamp: datetime,
    ) -> PaperPreTradeAssessment:
        del order
        return PaperPreTradeAssessment(
            reconciliation=ready_report(timestamp),
            operator_state=self.operator_control.current_state(),
            decision=PaperControlDecision(
                allowed=True,
                reasons=(),
                candidate_notional=Decimal("499.50"),
                committed_open_buy_notional=Decimal("0"),
                projected_gross_exposure=Decimal("499.50"),
                projected_symbol_exposure=Decimal("499.50"),
                projected_cash=Decimal("9500.50"),
                recent_order_count=0,
                session_order_count=0,
            ),
        )


@dataclass(slots=True)
class ExitAfterBrokerResponse:
    def __call__(self, boundary: PaperExecutionBoundary) -> None:
        if boundary is PaperExecutionBoundary.AFTER_BROKER_RESPONSE:
            os._exit(137)


def ready_report(timestamp: datetime) -> ReconciliationReport:
    return ReconciliationReport(
        timestamp=timestamp,
        ready=True,
        account_sha256=ACCOUNT_SHA256,
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
        issues=(),
    )


def paths(root: Path) -> tuple[Path, Path, Path, Path]:
    return (
        root / "broker.db",
        root / "operator.db",
        root / "fake-broker.json",
        root / "result.json",
    )


def crash(root: Path) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    broker_path, operator_path, fake_broker_path, _ = paths(root)
    journal = SQLiteBrokerJournal(broker_path)
    operator_control = SQLiteOperatorControl(
        operator_path,
        strategy_namespace=NAMESPACE,
        created_at=NOW - timedelta(minutes=10),
    )
    resume = OperatorApproval.issue(
        action=OperatorAction.RESUME,
        strategy_namespace=NAMESPACE,
        fingerprint=context().fingerprint,
        issued_at=NOW - timedelta(minutes=2),
        ttl=timedelta(minutes=5),
        nonce="f" * 32,
        acknowledgement=ACKNOWLEDGEMENTS[OperatorAction.RESUME],
        control_key=CONTROL_KEY,
    )
    operator_control.resume(
        approval=resume,
        expected_fingerprint=context().fingerprint,
        control_key=CONTROL_KEY,
        timestamp=NOW - timedelta(minutes=1),
        reason="literal subprocess acceptance fixture",
    )
    broker = FilePaperBroker(fake_broker_path)
    reconciler = PaperReconciler(
        broker=broker,
        journal=journal,
        strategy_namespace=NAMESPACE,
        activity_after=NOW - timedelta(days=1),
    )
    PaperOrderExecutor(
        broker=broker,
        journal=journal,
        operator_control=operator_control,
        pretrade=ReadyPreTrade(operator_control),  # type: ignore[arg-type]
        reconciler=reconciler,
        fault_hook=ExitAfterBrokerResponse(),
    ).execute_once(
        context=context(),
        order=approved_order(),
        attempt_sha256=ATTEMPT_SHA256,
        timestamp=NOW,
    )
    raise AssertionError("hard exit hook did not terminate the process")


def recover(root: Path) -> None:
    broker_path, operator_path, fake_broker_path, result_path = paths(root)
    order = approved_order()
    journal = SQLiteBrokerJournal(broker_path)
    operator_control = SQLiteOperatorControl(
        operator_path,
        strategy_namespace=NAMESPACE,
        created_at=NOW + timedelta(minutes=1),
    )
    broker = FilePaperBroker(fake_broker_path)
    reconciler = PaperReconciler(
        broker=broker,
        journal=journal,
        strategy_namespace=NAMESPACE,
        activity_after=NOW - timedelta(days=1),
    )
    recovered = PaperOrderExecutor(
        broker=broker,
        journal=journal,
        operator_control=operator_control,
        pretrade=ReadyPreTrade(operator_control),  # type: ignore[arg-type]
        reconciler=reconciler,
    ).recover_submission(
        context=context(),
        client_order_id=order.client_order_id,
        expected_payload_sha256=approved_order_payload_sha256(order),
        timestamp=NOW + timedelta(minutes=1),
    )
    retained = json.loads(fake_broker_path.read_text(encoding="utf-8"))
    submission = journal.submission_by_client_id(order.client_order_id)
    if submission is None:
        raise RuntimeError("submission disappeared after process recovery")
    if recovered.broker_order is None:
        raise RuntimeError("broker order was not recovered")
    result = {
        "ready": recovered.ready,
        "submission_state": submission.state.value,
        "submission_count": retained["submission_count"],
        "operator_paused": operator_control.current_state().paused,
        "broker_order_id": recovered.broker_order.broker_order_id,
    }
    result_path.write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    result_path.chmod(0o600)
    journal.close()
    operator_control.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("crash", "recover"))
    parser.add_argument("root", type=Path)
    arguments = parser.parse_args()
    if arguments.mode == "crash":
        crash(arguments.root.resolve())
    else:
        recover(arguments.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
