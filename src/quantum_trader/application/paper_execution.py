"""Crash-safe paper submission orchestration; no public command exposes this service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from quantum_trader.application.paper_controls import (
    PaperPreTradeAssessment,
    PaperPreTradeController,
)
from quantum_trader.application.reconciliation import PaperReconciler, ReconciliationReport
from quantum_trader.domain.brokerage import (
    ApprovedBrokerOrder,
    BrokerOrderSnapshot,
    SubmissionJournalEntry,
    SubmissionState,
)
from quantum_trader.domain.execution import ArmedExecutionContext, ExecutionMode
from quantum_trader.ports.broker_journal import BrokerJournal
from quantum_trader.ports.external_broker import (
    ExternalBroker,
    ExternalBrokerError,
    ExternalSubmissionAmbiguous,
    ExternalSubmissionRejected,
)
from quantum_trader.ports.operator_control import OperatorControlStore


class PaperExecutionError(RuntimeError):
    """Base paper orchestration failure; callers must remain paused on uncertainty."""


class PaperExecutionBlocked(PaperExecutionError):
    """Pre-trade state or recovery evidence did not permit a new submission."""


class PaperExecutionAmbiguous(PaperExecutionError):
    """A submission may exist externally and must not be blindly retried."""


class PaperExecutionRejected(PaperExecutionError):
    """The broker definitively rejected a submission before creating an order."""


class PaperExecutionBoundary(StrEnum):
    AFTER_PREFLIGHT = "after_preflight"
    AFTER_PERSIST = "after_persist"
    AFTER_STARTED = "after_started"
    AFTER_BROKER_RESPONSE = "after_broker_response"
    AFTER_ACKNOWLEDGED = "after_acknowledged"
    AFTER_RECONCILIATION = "after_reconciliation"


@dataclass(frozen=True, slots=True)
class PaperExecutionResult:
    submission: SubmissionJournalEntry
    broker_order: BrokerOrderSnapshot | None
    reconciliation: ReconciliationReport
    pretrade: PaperPreTradeAssessment | None
    recovered: bool

    @property
    def ready(self) -> bool:
        return self.submission.state is SubmissionState.RECONCILED and self.reconciliation.ready


class PaperOrderExecutor:
    """Coordinate one durable paper submission and conservative restart recovery."""

    def __init__(
        self,
        *,
        broker: ExternalBroker,
        journal: BrokerJournal,
        operator_control: OperatorControlStore,
        pretrade: PaperPreTradeController,
        reconciler: PaperReconciler,
        fault_hook: Callable[[PaperExecutionBoundary], None] | None = None,
    ) -> None:
        if broker.environment is not ExecutionMode.PAPER:
            raise ValueError("paper order execution requires a paper broker")
        self._broker = broker
        self._journal = journal
        self._operator_control = operator_control
        self._pretrade = pretrade
        self._reconciler = reconciler
        self._fault_hook = fault_hook or _no_fault

    def execute_once(
        self,
        *,
        context: ArmedExecutionContext,
        order: ApprovedBrokerOrder,
        attempt_sha256: str,
        timestamp: datetime,
    ) -> PaperExecutionResult:
        """Submit one never-before-journaled order through all durable boundaries."""

        _aware(timestamp, "timestamp")
        _sha256(attempt_sha256, "attempt_sha256")
        if self._journal.integrity_check().lower() != "ok":
            raise PaperExecutionBlocked("broker journal integrity check failed")
        if self._operator_control.integrity_check().lower() != "ok":
            raise PaperExecutionBlocked("operator-control integrity check failed")
        unresolved = tuple(self._journal.unresolved_submissions())
        if unresolved:
            self._pause(timestamp, "unresolved_submissions")
            raise PaperExecutionBlocked(
                "durable unresolved submissions require recovery before new orders"
            )
        if self._journal.submission_by_client_id(order.client_order_id) is not None:
            raise PaperExecutionBlocked(
                "client order ID already has durable state; recovery is required"
            )
        if order.strategy_namespace != context.strategy_namespace:
            raise PaperExecutionBlocked("paper context and order namespaces do not match")
        if order.account_sha256 != context.fingerprint.account_sha256:
            raise PaperExecutionBlocked("paper context and order accounts do not match")
        try:
            self._broker.verify_context(context)
        except ExternalBrokerError as exc:
            raise PaperExecutionBlocked("paper execution context is not valid") from exc

        assessment = self._pretrade.assess(order=order, timestamp=timestamp)
        if not assessment.ready:
            raise PaperExecutionBlocked("paper pre-trade assessment did not permit submission")
        self._fault_hook(PaperExecutionBoundary.AFTER_PREFLIGHT)
        self._require_operator_unpaused()

        payload_sha256 = approved_order_payload_sha256(order)
        self._journal.persist_approved_order(
            order=order,
            requested_payload_sha256=payload_sha256,
            timestamp=timestamp,
        )
        self._fault_hook(PaperExecutionBoundary.AFTER_PERSIST)
        if self._operator_control.current_state().paused:
            self._journal.transition_submission(
                client_order_id=order.client_order_id,
                state=SubmissionState.REJECTED,
                timestamp=timestamp,
                reason="operator_paused_after_persist",
            )
            raise PaperExecutionBlocked("operator pause prevented paper submission")
        started = self._journal.transition_submission(
            client_order_id=order.client_order_id,
            state=SubmissionState.STARTED,
            timestamp=timestamp,
            reason=attempt_sha256,
        )
        self._fault_hook(PaperExecutionBoundary.AFTER_STARTED)
        if self._operator_control.current_state().paused:
            self._journal.transition_submission(
                client_order_id=order.client_order_id,
                state=SubmissionState.REJECTED,
                timestamp=timestamp,
                reason="operator_paused_before_submit",
            )
            raise PaperExecutionBlocked("operator pause prevented paper submission")

        try:
            broker_order = self._broker.submit_once(
                context=context,
                order=order,
                submission_journal_sequence=started.sequence,
            )
            if broker_order.client_order_id != order.client_order_id:
                raise ExternalSubmissionAmbiguous(
                    "broker response changed deterministic client order identity"
                )
            self._fault_hook(PaperExecutionBoundary.AFTER_BROKER_RESPONSE)
            self._journal.transition_submission(
                client_order_id=order.client_order_id,
                state=SubmissionState.ACKNOWLEDGED,
                timestamp=timestamp,
                broker_order_id=broker_order.broker_order_id,
            )
            self._fault_hook(PaperExecutionBoundary.AFTER_ACKNOWLEDGED)
        except ExternalSubmissionRejected as exc:
            self._journal.transition_submission(
                client_order_id=order.client_order_id,
                state=SubmissionState.REJECTED,
                timestamp=timestamp,
                reason="broker_rejected_submission",
            )
            self._pause(timestamp, "submission_rejected")
            raise PaperExecutionRejected("paper submission was rejected") from exc
        except (ExternalSubmissionAmbiguous, ExternalBrokerError) as exc:
            self._record_ambiguous(order.client_order_id, timestamp)
            raise PaperExecutionAmbiguous(
                "paper submission outcome is ambiguous; recovery is required"
            ) from exc
        except Exception as exc:
            self._record_ambiguous(order.client_order_id, timestamp)
            raise PaperExecutionAmbiguous(
                "unexpected post-start failure left submission outcome ambiguous"
            ) from exc

        try:
            reconciliation = self._reconciler.reconcile(timestamp=timestamp)
        except Exception as exc:
            self._pause(timestamp, "post_submit_reconciliation_failed")
            raise PaperExecutionAmbiguous(
                "broker order was acknowledged but reconciliation failed"
            ) from exc
        self._fault_hook(PaperExecutionBoundary.AFTER_RECONCILIATION)
        final = self._require_submission(order.client_order_id)
        if final.state is not SubmissionState.RECONCILED:
            self._pause(timestamp, "submission_not_reconciled")
            raise PaperExecutionAmbiguous(
                "broker acknowledgement did not reach reconciled durable state"
            )
        if not reconciliation.ready:
            self._pause(timestamp, "post_submit_reconciliation_blocked")
            raise PaperExecutionBlocked(
                "submission reconciled but broker state is not ready for new exposure"
            )
        return PaperExecutionResult(
            submission=final,
            broker_order=broker_order,
            reconciliation=reconciliation,
            pretrade=assessment,
            recovered=False,
        )

    def recover_submission(
        self,
        *,
        context: ArmedExecutionContext,
        client_order_id: str,
        expected_payload_sha256: str,
        timestamp: datetime,
    ) -> PaperExecutionResult:
        """Resolve one durable crash state without ever issuing a new submission."""

        _aware(timestamp, "timestamp")
        _sha256(expected_payload_sha256, "expected_payload_sha256")
        self._pause(timestamp, "submission_recovery")
        if self._journal.integrity_check().lower() != "ok":
            raise PaperExecutionBlocked("broker journal integrity check failed")
        if self._operator_control.integrity_check().lower() != "ok":
            raise PaperExecutionBlocked("operator-control integrity check failed")
        entry = self._journal.submission_by_client_id(client_order_id)
        if entry is None:
            raise PaperExecutionBlocked("submission recovery identity is unknown")
        if entry.requested_payload_sha256 != expected_payload_sha256:
            raise PaperExecutionBlocked("submission recovery payload hash does not match")
        if entry.state is SubmissionState.REJECTED:
            raise PaperExecutionRejected("submission is durably rejected")
        self._broker.verify_context(context)

        broker_order = self._broker.get_order_by_client_id(client_order_id)
        if broker_order is None:
            if entry.state is SubmissionState.PERSISTED:
                final = self._journal.transition_submission(
                    client_order_id=client_order_id,
                    state=SubmissionState.REJECTED,
                    timestamp=timestamp,
                    reason="recovery_confirmed_not_started",
                )
                raise PaperExecutionRejected(
                    f"persisted submission was never started; state={final.state.value}"
                )
            raise PaperExecutionAmbiguous(
                "started submission is not visible by client ID; do not retry"
            )
        if broker_order.client_order_id != client_order_id:
            raise PaperExecutionAmbiguous("recovery broker identity changed")

        if entry.state is SubmissionState.STARTED:
            self._journal.transition_submission(
                client_order_id=client_order_id,
                state=SubmissionState.ACKNOWLEDGED,
                timestamp=timestamp,
                broker_order_id=broker_order.broker_order_id,
            )
        reconciliation = self._reconciler.reconcile(timestamp=timestamp)
        final = self._require_submission(client_order_id)
        if final.state is not SubmissionState.RECONCILED:
            raise PaperExecutionAmbiguous(
                "submission remained unresolved after recovery reconciliation"
            )
        return PaperExecutionResult(
            submission=final,
            broker_order=broker_order,
            reconciliation=reconciliation,
            pretrade=None,
            recovered=True,
        )

    def _require_operator_unpaused(self) -> None:
        if self._operator_control.current_state().paused:
            raise PaperExecutionBlocked("operator controls are paused")

    def _record_ambiguous(self, client_order_id: str, timestamp: datetime) -> None:
        try:
            self._journal.transition_submission(
                client_order_id=client_order_id,
                state=SubmissionState.AMBIGUOUS,
                timestamp=timestamp,
                reason="submission_outcome_ambiguous",
            )
        finally:
            self._pause(timestamp, "submission_ambiguous")

    def _pause(self, timestamp: datetime, reason_code: str) -> None:
        self._operator_control.pause(
            timestamp=timestamp,
            reason_code=reason_code,
            reason=reason_code.replace("_", " "),
        )

    def _require_submission(self, client_order_id: str) -> SubmissionJournalEntry:
        result = self._journal.submission_by_client_id(client_order_id)
        if result is None:
            raise PaperExecutionError("durable submission disappeared")
        return result


def approved_order_payload_sha256(order: ApprovedBrokerOrder) -> str:
    payload = json.dumps(
        order.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _no_fault(boundary: PaperExecutionBoundary) -> None:
    del boundary


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
