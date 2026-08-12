"""External broker contract used only by explicitly armed paper adapters."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from quantum_trader.domain.brokerage import (
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerActivityPage,
    BrokerCancelResult,
    BrokerClockSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from quantum_trader.domain.execution import ArmedExecutionContext, ExecutionMode


class ExternalBroker(Protocol):
    """Normalized broker operations required for idempotent paper execution."""

    @property
    def environment(self) -> ExecutionMode:
        """Return the explicit broker environment; URL inference is forbidden."""

    @property
    def account_sha256(self) -> str:
        """Return the non-secret fingerprint of the bound brokerage account."""

    def verify_context(self, context: ArmedExecutionContext) -> None:
        """Reject an expired, wrong-environment, wrong-account, or wrong-namespace context."""

    def get_account(self) -> BrokerAccountSnapshot:
        """Read normalized account status, cash, equity, and buying power."""

    def get_clock(self) -> BrokerClockSnapshot:
        """Read the broker-authoritative market clock and next session boundaries."""

    def list_positions(self) -> Sequence[BrokerPositionSnapshot]:
        """Read every current broker position for reconciliation."""

    def list_open_orders(self) -> Sequence[BrokerOrderSnapshot]:
        """Read every nonterminal order, including foreign strategy state."""

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        """Resolve ambiguous submissions by deterministic client ID before retrying."""

    def get_order_by_id(self, broker_order_id: str) -> BrokerOrderSnapshot | None:
        """Resolve activity ownership and terminal state by broker order ID."""

    def submit_once(
        self,
        *,
        context: ArmedExecutionContext,
        order: ApprovedBrokerOrder,
        submission_journal_sequence: int,
    ) -> BrokerOrderSnapshot:
        """Submit only after a durable positive journal sequence has been recorded."""

    def cancel_order(
        self,
        *,
        context: ArmedExecutionContext,
        broker_order_id: str,
    ) -> BrokerCancelResult:
        """Request cancellation and return observed state without assuming success."""

    def list_fill_activities(
        self,
        *,
        after: datetime | None,
        page_token: str | None,
        page_size: int,
    ) -> BrokerActivityPage:
        """Read one stable page of fill activities for transactional reconciliation."""
