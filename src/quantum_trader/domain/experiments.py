"""Immutable experiment-ledger contracts and research state transitions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{7,199}$")
_FAMILY = re.compile(r"^H[0-9]{2}$")


class ResearchState(StrEnum):
    """Governed candidate lifecycle states."""

    HYPOTHESIS = "hypothesis"
    DEVELOPMENT = "development"
    TEST_ELIGIBLE = "test_eligible"
    HOLDOUT_ELIGIBLE = "holdout_eligible"
    SHADOW_ELIGIBLE = "shadow_eligible"
    PAPER_ELIGIBLE = "paper_eligible"
    STRATEGY_A_PLUS = "strategy_a_plus"
    REJECTED = "rejected"


class AttemptStage(StrEnum):
    """Purpose assigned before an experiment attempt starts."""

    DEVELOPMENT = "development"
    VALIDATION = "validation"
    TEST = "test"
    ROBUSTNESS = "robustness"
    PLACEBO = "placebo"
    CAPACITY = "capacity"
    LOCKED_HOLDOUT = "locked_holdout"
    SHADOW = "shadow"
    PAPER = "paper"


class AttemptStatus(StrEnum):
    """Append-only attempt states."""

    REGISTERED = "registered"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.ABORTED}


class LedgerEventType(StrEnum):
    """Canonical experiment-ledger event taxonomy."""

    CAMPAIGN_REGISTERED = "campaign_registered"
    CANDIDATE_REGISTERED = "candidate_registered"
    CANDIDATE_STATE_CHANGED = "candidate_state_changed"
    PREREGISTRATION_FROZEN = "preregistration_frozen"
    ATTEMPT_REGISTERED = "attempt_registered"
    ATTEMPT_STARTED = "attempt_started"
    ATTEMPT_COMPLETED = "attempt_completed"
    ATTEMPT_FAILED = "attempt_failed"
    ATTEMPT_ABORTED = "attempt_aborted"
    ARTIFACT_RETAINED = "artifact_retained"
    COMPARISON_OPENED = "comparison_opened"
    HOLDOUT_SEALED = "holdout_sealed"
    HOLDOUT_OPEN_APPROVED = "holdout_open_approved"
    HOLDOUT_OPENED = "holdout_opened"
    HOLDOUT_COMPLETED = "holdout_completed"


class HoldoutStatus(StrEnum):
    """One-time campaign lockbox states."""

    SEALED = "sealed"
    OPENED = "opened"
    COMPLETED = "completed"


_ALLOWED_TRANSITIONS: dict[ResearchState, frozenset[ResearchState]] = {
    ResearchState.HYPOTHESIS: frozenset({ResearchState.DEVELOPMENT, ResearchState.REJECTED}),
    ResearchState.DEVELOPMENT: frozenset({ResearchState.TEST_ELIGIBLE, ResearchState.REJECTED}),
    ResearchState.TEST_ELIGIBLE: frozenset(
        {ResearchState.HOLDOUT_ELIGIBLE, ResearchState.REJECTED}
    ),
    ResearchState.HOLDOUT_ELIGIBLE: frozenset(
        {ResearchState.SHADOW_ELIGIBLE, ResearchState.REJECTED}
    ),
    ResearchState.SHADOW_ELIGIBLE: frozenset(
        {ResearchState.PAPER_ELIGIBLE, ResearchState.REJECTED}
    ),
    ResearchState.PAPER_ELIGIBLE: frozenset(
        {ResearchState.STRATEGY_A_PLUS, ResearchState.REJECTED}
    ),
    ResearchState.STRATEGY_A_PLUS: frozenset(),
    ResearchState.REJECTED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class CampaignRegistration:
    """Immutable identity of one research campaign."""

    campaign_id: str
    governance_policy_sha256: str
    hypothesis_catalog_sha256: str
    data_contract_manifest_sha256: str
    baseline_commit: str
    registered_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.campaign_id, "campaign_id")
        _sha256(self.governance_policy_sha256, "governance_policy_sha256")
        _sha256(self.hypothesis_catalog_sha256, "hypothesis_catalog_sha256")
        _sha256(self.data_contract_manifest_sha256, "data_contract_manifest_sha256")
        _commit(self.baseline_commit, "baseline_commit")
        _aware(self.registered_at, "registered_at")


@dataclass(frozen=True, slots=True)
class CandidateRegistration:
    """One immutable candidate specification consuming a family budget slot."""

    candidate_id: str
    campaign_id: str
    family_id: str
    candidate_index: int
    candidate_ceiling: int
    specification_sha256: str
    code_commit: str
    registered_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.candidate_id, "candidate_id")
        _identifier(self.campaign_id, "campaign_id")
        if _FAMILY.fullmatch(self.family_id) is None:
            raise ValueError("family_id must match HNN")
        if self.candidate_ceiling < 1:
            raise ValueError("candidate_ceiling must be positive")
        if not 1 <= self.candidate_index <= self.candidate_ceiling:
            raise ValueError("candidate_index exceeds its family ceiling")
        _sha256(self.specification_sha256, "specification_sha256")
        _commit(self.code_commit, "code_commit")
        _aware(self.registered_at, "registered_at")


@dataclass(frozen=True, slots=True)
class PreregistrationFreeze:
    """Immutable test-boundary identity frozen before protected attempts."""

    candidate_id: str
    protocol_id: str
    protocol_sha256: str
    data_snapshot_id: str
    data_snapshot_manifest_sha256: str
    partition_plan_sha256: str
    benchmark_set_sha256: str
    cost_model_set_sha256: str
    candidate_budget_sha256: str
    frozen_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.candidate_id, "candidate_id")
        _identifier(self.protocol_id, "protocol_id")
        _identifier(self.data_snapshot_id, "data_snapshot_id")
        for field_name in (
            "protocol_sha256",
            "data_snapshot_manifest_sha256",
            "partition_plan_sha256",
            "benchmark_set_sha256",
            "cost_model_set_sha256",
            "candidate_budget_sha256",
        ):
            _sha256(str(getattr(self, field_name)), field_name)
        _aware(self.frozen_at, "frozen_at")


@dataclass(frozen=True, slots=True)
class AttemptRegistration:
    """An attempt declared completely before its first computation."""

    attempt_id: str
    candidate_id: str
    comparison_group_id: str
    stage: AttemptStage
    protocol_id: str
    data_snapshot_id: str
    partition_id: str
    code_commit: str
    configuration_sha256: str
    benchmark_set_sha256: str
    cost_model_sha256: str
    inference_plan_sha256: str
    registered_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "attempt_id",
            "candidate_id",
            "comparison_group_id",
            "protocol_id",
            "data_snapshot_id",
            "partition_id",
        ):
            _identifier(str(getattr(self, field_name)), field_name)
        _commit(self.code_commit, "code_commit")
        for field_name in (
            "configuration_sha256",
            "benchmark_set_sha256",
            "cost_model_sha256",
            "inference_plan_sha256",
        ):
            _sha256(str(getattr(self, field_name)), field_name)
        _aware(self.registered_at, "registered_at")


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """One immutable output retained for a terminal attempt."""

    artifact_id: str
    attempt_id: str
    name: str
    sha256: str
    byte_count: int
    media_type: str
    role: str
    license_class: str
    retained_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.artifact_id, "artifact_id")
        _identifier(self.attempt_id, "attempt_id")
        if not self.name or len(self.name) > 200:
            raise ValueError("artifact name must contain 1 to 200 characters")
        _sha256(self.sha256, "artifact sha256")
        if self.byte_count < 0:
            raise ValueError("artifact byte_count must be nonnegative")
        if not self.media_type or len(self.media_type) > 120:
            raise ValueError("artifact media_type is invalid")
        if not self.role or len(self.role) > 100:
            raise ValueError("artifact role is invalid")
        if self.license_class not in {
            "open",
            "licensed_nonredistributable",
            "private",
            "synthetic",
        }:
            raise ValueError("artifact license_class is invalid")
        _aware(self.retained_at, "retained_at")


@dataclass(frozen=True, slots=True)
class HoldoutSeal:
    """A candidate-bound lockbox whose bytes remain unavailable."""

    holdout_id: str
    campaign_id: str
    candidate_id: str
    boundary_sha256: str
    provider_query_sha256: str
    bytes_retrieved: bool
    sealed_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.holdout_id, "holdout_id")
        _identifier(self.campaign_id, "campaign_id")
        _identifier(self.candidate_id, "candidate_id")
        _sha256(self.boundary_sha256, "boundary_sha256")
        _sha256(self.provider_query_sha256, "provider_query_sha256")
        if self.bytes_retrieved:
            raise ValueError("a new holdout must be sealed before its bytes are retrieved")
        _aware(self.sealed_at, "sealed_at")


@dataclass(frozen=True, slots=True)
class HoldoutApproval:
    """Explicit, expiring user authorization for one holdout and campaign."""

    approval_id: str
    campaign_id: str
    holdout_id: str
    acknowledgment_sha256: str
    conversation_receipt_sha256: str
    approved_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.approval_id, "approval_id")
        _identifier(self.campaign_id, "campaign_id")
        _identifier(self.holdout_id, "holdout_id")
        _sha256(self.acknowledgment_sha256, "acknowledgment_sha256")
        _sha256(self.conversation_receipt_sha256, "conversation_receipt_sha256")
        _aware(self.approved_at, "approved_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.approved_at:
            raise ValueError("holdout approval must expire after approval")

    def verify(self, *, campaign_id: str, holdout_id: str, now: datetime) -> None:
        _aware(now, "now")
        if self.campaign_id != campaign_id or self.holdout_id != holdout_id:
            raise ValueError("holdout approval is bound to a different campaign or lockbox")
        if now > self.expires_at:
            raise ValueError("holdout approval has expired")


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """One canonical, hash-chained append-only experiment record."""

    sequence: int
    event_id: str
    campaign_id: str
    event_type: LedgerEventType
    subject_id: str
    occurred_at: datetime
    actor: str
    payload: dict[str, JsonValue]
    payload_sha256: str
    previous_event_sha256: str
    event_sha256: str

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("ledger sequence must be positive")
        _identifier(self.event_id, "event_id")
        _identifier(self.campaign_id, "campaign_id")
        _identifier(self.subject_id, "subject_id")
        _aware(self.occurred_at, "occurred_at")
        if not self.actor or len(self.actor) > 100:
            raise ValueError("ledger actor is invalid")
        _sha256(self.payload_sha256, "payload_sha256")
        _sha256(self.previous_event_sha256, "previous_event_sha256")
        _sha256(self.event_sha256, "event_sha256")


def validate_state_transition(current: ResearchState, target: ResearchState) -> None:
    """Reject every state jump not declared by frozen governance."""

    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid research state transition: {current.value} -> {target.value}")


def canonical_json(value: JsonValue) -> str:
    """Return the one accepted JSON serialization for hashes and storage."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_json(value: JsonValue) -> str:
    """Hash canonical JSON bytes."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def ledger_event_hash(
    *,
    sequence: int,
    event_id: str,
    campaign_id: str,
    event_type: LedgerEventType,
    subject_id: str,
    occurred_at: datetime,
    actor: str,
    payload_sha256: str,
    previous_event_sha256: str,
) -> str:
    """Hash every immutable event identity field and the previous chain head."""

    _aware(occurred_at, "occurred_at")
    value: dict[str, JsonValue] = {
        "sequence": sequence,
        "event_id": event_id,
        "campaign_id": campaign_id,
        "event_type": event_type.value,
        "subject_id": subject_id,
        "occurred_at": occurred_at.astimezone(UTC).isoformat(),
        "actor": actor,
        "payload_sha256": payload_sha256,
        "previous_event_sha256": previous_event_sha256,
    }
    return sha256_json(value)


def _identifier(value: str, field_name: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} has an invalid immutable identifier")


def _sha256(value: str, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _commit(value: str, field_name: str) -> None:
    if _COMMIT.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a 40-character Git commit")


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
