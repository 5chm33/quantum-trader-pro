"""Durable journal contract for paper submissions and broker reconciliation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from quantum_trader.domain.brokerage import (
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerFillActivity,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    SubmissionJournalEntry,
    SubmissionState,
)


class BrokerJournal(Protocol):
    """Persist all external side-effect intent and reconciliation state transactionally."""

    def persist_approved_order(
        self,
        *,
        order: ApprovedBrokerOrder,
        requested_payload_sha256: str,
        timestamp: datetime,
    ) -> SubmissionJournalEntry:
        """Durably record an approved payload before any network submission."""

    def transition_submission(
        self,
        *,
        client_order_id: str,
        state: SubmissionState,
        timestamp: datetime,
        broker_order_id: str | None = None,
        reason: str | None = None,
    ) -> SubmissionJournalEntry:
        """Apply one validated durable submission-state transition."""

    def unresolved_submissions(self) -> Sequence[SubmissionJournalEntry]:
        """Return submissions not yet reconciled to a terminal local state."""

    def known_client_order_ids(self) -> frozenset[str]:
        """Return every client order ID durably known to the local journal."""

    def submission_timestamps(self) -> Sequence[datetime]:
        """Return durable pre-submit timestamps in journal sequence order."""

    def apply_reconciliation(
        self,
        *,
        account: BrokerAccountSnapshot,
        orders: Sequence[BrokerOrderSnapshot],
        positions: Sequence[BrokerPositionSnapshot],
        fills: Sequence[BrokerFillActivity],
        submission_resolutions: Mapping[str, str],
        activity_checkpoint: str | None,
        timestamp: datetime,
        report: Mapping[str, Any],
    ) -> int:
        """Atomically update projections and append a reconciliation report.

        The transaction also deduplicates fills and resolves known submissions.
        """

    def all_fills(self) -> Sequence[BrokerFillActivity]:
        """Return every idempotently retained broker fill in execution order."""

    def activity_checkpoint(self) -> str | None:
        """Return the last fully committed activity page token."""

    def integrity_check(self) -> str:
        """Return SQLite-style integrity status or an equivalent result."""

    def close(self) -> None:
        """Flush and close the journal."""
