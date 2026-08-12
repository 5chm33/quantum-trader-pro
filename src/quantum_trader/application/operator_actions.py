"""Approval-bound paper kill actions; no live or position-flatten command exists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quantum_trader.application.reconciliation import (
    PaperReconciler,
    ReconciliationReport,
)
from quantum_trader.domain.brokerage import is_owned_client_order_id
from quantum_trader.domain.execution import ArmedExecutionContext, ExecutionFingerprint
from quantum_trader.domain.operator import OperatorAction, OperatorApproval
from quantum_trader.ports.external_broker import ExternalBroker
from quantum_trader.ports.operator_control import OperatorControlStore


class OperatorActionError(RuntimeError):
    """An operator kill action failed closed while the system remained paused."""


@dataclass(frozen=True, slots=True)
class CancelOwnedOrdersResult:
    examined_open_orders: int
    owned_open_orders: int
    foreign_open_orders: int
    verified_terminal: int
    residual_owned_orders: int
    reconciliation: ReconciliationReport

    @property
    def succeeded(self) -> bool:
        return self.verified_terminal == self.owned_open_orders and self.residual_owned_orders == 0


class PaperOperatorActions:
    """Paper-only kill actions that require pause plus one-use operator approval."""

    def __init__(
        self,
        *,
        broker: ExternalBroker,
        operator_control: OperatorControlStore,
        reconciler: PaperReconciler,
        strategy_namespace: str,
    ) -> None:
        namespace = strategy_namespace.strip()
        if not namespace:
            raise ValueError("strategy_namespace must not be empty")
        self._broker = broker
        self._operator_control = operator_control
        self._reconciler = reconciler
        self._strategy_namespace = namespace

    def pause(
        self,
        *,
        timestamp: datetime,
        reason_code: str,
        reason: str,
    ) -> None:
        """Immediately disable all new paper assessments; no approval is required."""

        self._operator_control.pause(
            timestamp=timestamp,
            reason_code=reason_code,
            reason=reason,
        )

    def resume_paper_assessments(
        self,
        *,
        context: ArmedExecutionContext,
        approval: OperatorApproval,
        expected_fingerprint: ExecutionFingerprint,
        control_key: bytes,
        timestamp: datetime,
    ) -> ReconciliationReport:
        """Resume paper assessments only after a fresh complete reconciliation."""

        if self._operator_control.integrity_check().lower() != "ok":
            raise OperatorActionError("operator-control integrity check failed")
        if context.strategy_namespace != self._strategy_namespace:
            raise OperatorActionError("paper context strategy namespace does not match")
        if context.fingerprint != expected_fingerprint:
            raise OperatorActionError("paper context fingerprint does not match")
        self._broker.verify_context(context)
        reconciliation = self._reconciler.reconcile(timestamp=timestamp)
        if not reconciliation.ready:
            raise OperatorActionError("reconciliation is not ready for resume")
        if not reconciliation.account_permits_new_exposure:
            raise OperatorActionError("paper account does not permit new exposure")
        state = self._operator_control.resume(
            approval=approval,
            expected_fingerprint=expected_fingerprint,
            control_key=control_key,
            timestamp=timestamp,
            reason="paper context, store integrity, and reconciliation passed",
        )
        if state.paused:
            raise OperatorActionError("operator controls remained paused after resume")
        return reconciliation

    def cancel_owned_orders(
        self,
        *,
        context: ArmedExecutionContext,
        approval: OperatorApproval,
        expected_fingerprint: ExecutionFingerprint,
        control_key: bytes,
        timestamp: datetime,
    ) -> CancelOwnedOrdersResult:
        """Cancel only bot-owned paper orders and remain paused afterward."""

        self._operator_control.pause(
            timestamp=timestamp,
            reason_code="cancel_requested",
            reason="cancel-owned-orders action requested",
        )
        action_started = False
        try:
            self._operator_control.begin_action(
                approval=approval,
                expected_action=OperatorAction.CANCEL_OWNED_ORDERS,
                expected_fingerprint=expected_fingerprint,
                control_key=control_key,
                timestamp=timestamp,
            )
            action_started = True
            if context.strategy_namespace != self._strategy_namespace:
                raise OperatorActionError("paper context strategy namespace does not match")
            if context.fingerprint != expected_fingerprint:
                raise OperatorActionError("paper context fingerprint does not match")
            self._broker.verify_context(context)

            before = tuple(self._broker.list_open_orders())
            owned = tuple(
                order
                for order in before
                if is_owned_client_order_id(
                    order.client_order_id,
                    self._strategy_namespace,
                )
            )
            foreign_count = len(before) - len(owned)
            terminal_count = 0
            for order in owned:
                cancel_result = self._broker.cancel_order(
                    context=context,
                    broker_order_id=order.broker_order_id,
                )
                if cancel_result.broker_order_id != order.broker_order_id:
                    raise OperatorActionError("broker cancellation identity changed")
                if not cancel_result.verified_terminal:
                    raise OperatorActionError(
                        "broker did not verify a terminal cancellation outcome"
                    )
                terminal_count += 1

            after = tuple(self._broker.list_open_orders())
            residual_owned = sum(
                is_owned_client_order_id(
                    order.client_order_id,
                    self._strategy_namespace,
                )
                for order in after
            )
            if residual_owned:
                raise OperatorActionError("bot-owned orders remained after cancellation")
            reconciliation = self._reconciler.reconcile(timestamp=timestamp)
            outcome = CancelOwnedOrdersResult(
                examined_open_orders=len(before),
                owned_open_orders=len(owned),
                foreign_open_orders=foreign_count,
                verified_terminal=terminal_count,
                residual_owned_orders=residual_owned,
                reconciliation=reconciliation,
            )
            if not outcome.succeeded:
                raise OperatorActionError("cancel-owned-orders outcome was incomplete")
            self._operator_control.complete_action(
                approval_id=approval.approval_id,
                succeeded=True,
                timestamp=timestamp,
                summary=(
                    "cancel_owned_orders_completed:"
                    f"owned={len(owned)}:foreign={foreign_count}:"
                    f"terminal={terminal_count}:residual={residual_owned}:"
                    f"reconciliation_ready={reconciliation.ready}"
                ),
            )
            return outcome
        except Exception as exc:
            if action_started:
                try:
                    self._operator_control.complete_action(
                        approval_id=approval.approval_id,
                        succeeded=False,
                        timestamp=timestamp,
                        summary=f"cancel_owned_orders_failed:{type(exc).__name__}",
                    )
                except Exception as completion_error:
                    raise OperatorActionError(
                        "cancel action and durable failure recording both failed"
                    ) from completion_error
            if isinstance(exc, OperatorActionError):
                raise
            raise OperatorActionError("cancel-owned-orders action failed closed") from exc
