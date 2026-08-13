"""Port for immutable, budget-aware, preregistered research evidence."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Protocol

from quantum_trader.domain.experiments import (
    ArtifactRecord,
    AttemptRegistration,
    AttemptStatus,
    CampaignRegistration,
    CandidateRegistration,
    HoldoutApproval,
    HoldoutSeal,
    LedgerEvent,
    PreregistrationFreeze,
    ResearchState,
)


class ExperimentLedger(Protocol):
    """Append-only research governance and evidence persistence."""

    def register_campaign(self, campaign: CampaignRegistration, *, actor: str) -> LedgerEvent: ...

    def register_candidate(
        self, candidate: CandidateRegistration, *, actor: str
    ) -> LedgerEvent: ...

    def freeze_preregistration(
        self,
        freeze: PreregistrationFreeze,
        *,
        actor: str,
    ) -> LedgerEvent: ...

    def register_attempt(self, attempt: AttemptRegistration, *, actor: str) -> LedgerEvent: ...

    def start_attempt(
        self,
        attempt_id: str,
        *,
        actor: str,
        started_at: datetime,
    ) -> LedgerEvent: ...

    def complete_attempt(
        self,
        attempt_id: str,
        *,
        actor: str,
        completed_at: datetime,
        result_summary_sha256: str,
        artifacts: Sequence[ArtifactRecord],
    ) -> LedgerEvent: ...

    def fail_attempt(
        self,
        attempt_id: str,
        *,
        actor: str,
        failed_at: datetime,
        reason_code: str,
        reason_sha256: str,
    ) -> LedgerEvent: ...

    def abort_attempt(
        self,
        attempt_id: str,
        *,
        actor: str,
        aborted_at: datetime,
        reason_code: str,
        reason_sha256: str,
    ) -> LedgerEvent: ...

    def open_comparison(
        self,
        comparison_group_id: str,
        *,
        actor: str,
        opened_at: datetime,
    ) -> LedgerEvent: ...

    def transition_candidate(
        self,
        candidate_id: str,
        *,
        actor: str,
        target: ResearchState,
        occurred_at: datetime,
        gate_evidence_sha256: str,
    ) -> LedgerEvent: ...

    def seal_holdout(self, seal: HoldoutSeal, *, actor: str) -> LedgerEvent: ...

    def approve_holdout(self, approval: HoldoutApproval, *, actor: str) -> LedgerEvent: ...

    def open_holdout(
        self,
        holdout_id: str,
        *,
        actor: str,
        opened_at: datetime,
        approval_id: str,
        data_snapshot_id: str,
        data_snapshot_manifest_sha256: str,
    ) -> LedgerEvent: ...

    def complete_holdout(
        self,
        holdout_id: str,
        *,
        actor: str,
        completed_at: datetime,
        result_manifest_sha256: str,
        passed: bool,
    ) -> LedgerEvent: ...

    def current_candidate_state(self, candidate_id: str) -> ResearchState: ...

    def attempt_status(self, attempt_id: str) -> AttemptStatus: ...

    def iter_events(self, *, campaign_id: str | None = None) -> Iterator[LedgerEvent]: ...

    def verify_integrity(self) -> None: ...

    def close(self) -> None: ...
