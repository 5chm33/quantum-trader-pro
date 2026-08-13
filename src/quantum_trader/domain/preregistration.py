"""Immutable preregistration plans for walk-forward research campaigns.

This layer names the candidate budget, chronological folds, regime partitions, permanent
comparators, and a new sealed lockbox before a protected research attempt can be registered.
It does not retrieve holdout bytes, run an experiment, mutate the experiment ledger, or grant
an approval to open any holdout.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise

from quantum_trader.domain.experiments import (
    CampaignRegistration,
    CandidateRegistration,
    HoldoutSeal,
    PreregistrationFreeze,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{7,199}$")
_FAMILY = re.compile(r"^H[0-9]{2}$")
_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")
_ZERO = timedelta(0)


class PreregistrationError(ValueError):
    """Raised when a planned campaign boundary is incomplete, overlapping, or mutable."""


class PermanentBaseline(StrEnum):
    """Comparators that every preregistered research candidate must retain."""

    EQUAL_WEIGHT = "equal_weight"
    TREND_ONLY = "trend_only"
    CASH = "cash"


class RegimeLabel(StrEnum):
    """Ex-ante descriptive labels for stability reporting, never a post-hoc selection field."""

    CALM = "calm"
    STRESSED = "stressed"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    RISING_RATE = "rising_rate"
    FALLING_RATE = "falling_rate"


@dataclass(frozen=True, slots=True)
class CandidateFamilyBudget:
    """One immutable HNN candidate ceiling mirrored by each ledger candidate registration."""

    family_id: str
    candidate_ceiling: int

    def __post_init__(self) -> None:
        if _FAMILY.fullmatch(self.family_id) is None:
            raise PreregistrationError("family_id must match HNN")
        if self.candidate_ceiling < 1:
            raise PreregistrationError("candidate_ceiling must be positive")


@dataclass(frozen=True, slots=True)
class CandidateBudgetPlan:
    """Complete, canonically ordered set of candidate-family ceilings for one campaign."""

    budget_id: str
    budget_version: str
    families: tuple[CandidateFamilyBudget, ...]

    def __post_init__(self) -> None:
        _identifier(self.budget_id, "budget_id")
        _version(self.budget_version, "budget_version")
        if not self.families:
            raise PreregistrationError("candidate budget requires at least one family")
        family_ids = tuple(item.family_id for item in self.families)
        if family_ids != tuple(sorted(family_ids)) or len(set(family_ids)) != len(family_ids):
            raise PreregistrationError(
                "candidate budget families must be unique and canonically ordered"
            )

    @property
    def sha256(self) -> str:
        """Return the deterministic digest bound into a ledger preregistration freeze."""

        return _sha256_payload(
            {
                "budget_id": self.budget_id,
                "budget_version": self.budget_version,
                "families": [
                    {"family_id": item.family_id, "candidate_ceiling": item.candidate_ceiling}
                    for item in self.families
                ],
            }
        )

    def validate_candidate(self, candidate: CandidateRegistration) -> None:
        """Verify a ledger candidate consumes a declared family slot at its declared ceiling."""

        matched = next(
            (item for item in self.families if item.family_id == candidate.family_id), None
        )
        if matched is None:
            raise PreregistrationError("candidate family is absent from preregistered budget")
        if candidate.candidate_ceiling != matched.candidate_ceiling:
            raise PreregistrationError("candidate ceiling differs from preregistered family budget")
        if candidate.candidate_index > matched.candidate_ceiling:
            raise PreregistrationError("candidate index exceeds preregistered family budget")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """One embargoed chronological train/validation/test fold with no implied data access."""

    fold_id: str
    train_start_at: datetime
    train_end_at: datetime
    validation_start_at: datetime
    validation_end_at: datetime
    test_start_at: datetime
    test_end_at: datetime
    embargo: timedelta

    def __post_init__(self) -> None:
        _identifier(self.fold_id, "fold_id")
        fields = (
            "train_start_at",
            "train_end_at",
            "validation_start_at",
            "validation_end_at",
            "test_start_at",
            "test_end_at",
        )
        normalized = {
            field_name: _utc(getattr(self, field_name), field_name) for field_name in fields
        }
        for field_name, value in normalized.items():
            object.__setattr__(self, field_name, value)
        if self.embargo <= _ZERO:
            raise PreregistrationError("walk-forward fold embargo must be positive")
        if not self.train_start_at < self.train_end_at:
            raise PreregistrationError("walk-forward train window must be nonempty")
        if not self.validation_start_at < self.validation_end_at:
            raise PreregistrationError("walk-forward validation window must be nonempty")
        if not self.test_start_at < self.test_end_at:
            raise PreregistrationError("walk-forward test window must be nonempty")
        if self.train_end_at + self.embargo > self.validation_start_at:
            raise PreregistrationError("walk-forward train and validation windows violate embargo")
        if self.validation_end_at + self.embargo > self.test_start_at:
            raise PreregistrationError("walk-forward validation and test windows violate embargo")


@dataclass(frozen=True, slots=True)
class WalkForwardPlan:
    """A non-overlapping fold plan frozen before evaluation; test windows are not holdouts."""

    plan_id: str
    plan_version: str
    folds: tuple[WalkForwardFold, ...]

    def __post_init__(self) -> None:
        _identifier(self.plan_id, "plan_id")
        _version(self.plan_version, "plan_version")
        if len(self.folds) < 2:
            raise PreregistrationError("walk-forward plan requires at least two folds")
        fold_ids = tuple(item.fold_id for item in self.folds)
        if fold_ids != tuple(sorted(fold_ids)) or len(set(fold_ids)) != len(fold_ids):
            raise PreregistrationError("walk-forward folds must use unique canonical sorted ids")
        for previous, current in pairwise(self.folds):
            if previous.test_end_at > current.train_start_at:
                raise PreregistrationError("walk-forward folds must not overlap chronologically")

    @property
    def sha256(self) -> str:
        """Return the canonical partition digest without exposing any data bytes."""

        return _sha256_payload(
            {
                "plan_id": self.plan_id,
                "plan_version": self.plan_version,
                "folds": [
                    {
                        "fold_id": fold.fold_id,
                        "train_start_at": fold.train_start_at.isoformat(),
                        "train_end_at": fold.train_end_at.isoformat(),
                        "validation_start_at": fold.validation_start_at.isoformat(),
                        "validation_end_at": fold.validation_end_at.isoformat(),
                        "test_start_at": fold.test_start_at.isoformat(),
                        "test_end_at": fold.test_end_at.isoformat(),
                        "embargo_seconds": int(fold.embargo.total_seconds()),
                    }
                    for fold in self.folds
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class RegimeWindow:
    """One ex-ante labeled time window, retaining the classifier configuration digest."""

    regime_id: str
    label: RegimeLabel
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.regime_id, "regime_id")
        object.__setattr__(self, "start_at", _utc(self.start_at, "start_at"))
        object.__setattr__(self, "end_at", _utc(self.end_at, "end_at"))
        if self.start_at >= self.end_at:
            raise PreregistrationError("regime window must be nonempty")


@dataclass(frozen=True, slots=True)
class RegimePlan:
    """Non-overlapping, ordered ex-ante regime windows for stability reporting."""

    regime_plan_id: str
    classifier_version: str
    classifier_specification_sha256: str
    regimes: tuple[RegimeWindow, ...]

    def __post_init__(self) -> None:
        _identifier(self.regime_plan_id, "regime_plan_id")
        _version(self.classifier_version, "classifier_version")
        _sha256(self.classifier_specification_sha256, "classifier_specification_sha256")
        if len(self.regimes) < 2:
            raise PreregistrationError("regime plan requires at least two regimes")
        regime_ids = tuple(item.regime_id for item in self.regimes)
        if regime_ids != tuple(sorted(regime_ids)) or len(set(regime_ids)) != len(regime_ids):
            raise PreregistrationError("regime ids must be unique and canonically ordered")
        labels = tuple(item.label for item in self.regimes)
        if len(set(labels)) != len(labels):
            raise PreregistrationError("regime labels must be unique within one regime plan")
        chronological = tuple(sorted(self.regimes, key=lambda item: item.start_at))
        for previous, current in pairwise(chronological):
            if previous.end_at > current.start_at:
                raise PreregistrationError("regime windows must not overlap")

    @property
    def sha256(self) -> str:
        """Return a canonical digest binding regime labels and the classifier specification."""

        return _sha256_payload(
            {
                "regime_plan_id": self.regime_plan_id,
                "classifier_version": self.classifier_version,
                "classifier_specification_sha256": self.classifier_specification_sha256,
                "regimes": [
                    {
                        "regime_id": item.regime_id,
                        "label": item.label.value,
                        "start_at": item.start_at.isoformat(),
                        "end_at": item.end_at.isoformat(),
                    }
                    for item in self.regimes
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class NewCampaignLockbox:
    """A new, separate, sealed lockbox; its bytes remain unavailable by construction."""

    seal: HoldoutSeal
    requires_explicit_user_approval: bool
    legacy_v1_holdout_policy_sha256: str

    def __post_init__(self) -> None:
        if self.seal.bytes_retrieved:
            raise PreregistrationError(
                "new campaign lockbox must remain sealed before byte retrieval"
            )
        if not self.requires_explicit_user_approval:
            raise PreregistrationError("new campaign lockbox must require explicit user approval")
        _sha256(self.legacy_v1_holdout_policy_sha256, "legacy_v1_holdout_policy_sha256")

    @property
    def sha256(self) -> str:
        """Return a receipt digest without including or retrieving lockbox bytes."""

        return _sha256_payload(
            {
                "holdout_id": self.seal.holdout_id,
                "campaign_id": self.seal.campaign_id,
                "candidate_id": self.seal.candidate_id,
                "boundary_sha256": self.seal.boundary_sha256,
                "provider_query_sha256": self.seal.provider_query_sha256,
                "bytes_retrieved": self.seal.bytes_retrieved,
                "requires_explicit_user_approval": self.requires_explicit_user_approval,
                "legacy_v1_holdout_policy_sha256": self.legacy_v1_holdout_policy_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class CampaignPreregistrationPlan:
    """A complete static campaign protocol that can be bound to one ledger candidate freeze."""

    protocol_id: str
    protocol_version: str
    campaign: CampaignRegistration
    data_snapshot_id: str
    data_snapshot_manifest_sha256: str
    benchmark_set_sha256: str
    cost_model_set_sha256: str
    inference_plan_sha256: str
    permanent_baselines: tuple[PermanentBaseline, ...]
    candidate_budget: CandidateBudgetPlan
    walk_forward: WalkForwardPlan
    regimes: RegimePlan
    lockbox: NewCampaignLockbox
    code_commit: str
    created_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.protocol_id, "protocol_id")
        _version(self.protocol_version, "protocol_version")
        _identifier(self.data_snapshot_id, "data_snapshot_id")
        for field_name in (
            "data_snapshot_manifest_sha256",
            "benchmark_set_sha256",
            "cost_model_set_sha256",
            "inference_plan_sha256",
        ):
            _sha256(str(getattr(self, field_name)), field_name)
        if self.permanent_baselines != tuple(
            sorted(self.permanent_baselines, key=lambda item: item.value)
        ):
            raise PreregistrationError("permanent baselines must use canonical sorted order")
        if self.permanent_baselines != (
            PermanentBaseline.CASH,
            PermanentBaseline.EQUAL_WEIGHT,
            PermanentBaseline.TREND_ONLY,
        ):
            raise PreregistrationError(
                "all permanent cash, equal-weight, and trend-only baselines are required"
            )
        if self.lockbox.seal.campaign_id != self.campaign.campaign_id:
            raise PreregistrationError(
                "new campaign lockbox must be bound to preregistration campaign"
            )
        _commit(self.code_commit, "code_commit")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))

    @property
    def partition_plan_sha256(self) -> str:
        """Bind walk-forward and regime plans into the ledger partition digest."""

        return _sha256_payload(
            {
                "walk_forward_sha256": self.walk_forward.sha256,
                "regime_plan_sha256": self.regimes.sha256,
            }
        )

    @property
    def protocol_sha256(self) -> str:
        """Return a complete immutable protocol receipt for later ledger evidence."""

        return _sha256_payload(
            {
                "protocol_id": self.protocol_id,
                "protocol_version": self.protocol_version,
                "campaign_id": self.campaign.campaign_id,
                "data_snapshot_id": self.data_snapshot_id,
                "data_snapshot_manifest_sha256": self.data_snapshot_manifest_sha256,
                "benchmark_set_sha256": self.benchmark_set_sha256,
                "cost_model_set_sha256": self.cost_model_set_sha256,
                "inference_plan_sha256": self.inference_plan_sha256,
                "permanent_baselines": [item.value for item in self.permanent_baselines],
                "candidate_budget_sha256": self.candidate_budget.sha256,
                "partition_plan_sha256": self.partition_plan_sha256,
                "lockbox_sha256": self.lockbox.sha256,
                "code_commit": self.code_commit,
                "created_at": self.created_at.isoformat(),
            }
        )

    def freeze_candidate(
        self, candidate: CandidateRegistration, *, frozen_at: datetime
    ) -> PreregistrationFreeze:
        """Create a ledger-compatible immutable freeze after campaign and budget validation."""

        if candidate.campaign_id != self.campaign.campaign_id:
            raise PreregistrationError("candidate belongs to a different preregistration campaign")
        if candidate.code_commit != self.code_commit:
            raise PreregistrationError(
                "candidate code commit differs from preregistered code commit"
            )
        self.candidate_budget.validate_candidate(candidate)
        return PreregistrationFreeze(
            candidate_id=candidate.candidate_id,
            protocol_id=self.protocol_id,
            protocol_sha256=self.protocol_sha256,
            data_snapshot_id=self.data_snapshot_id,
            data_snapshot_manifest_sha256=self.data_snapshot_manifest_sha256,
            partition_plan_sha256=self.partition_plan_sha256,
            benchmark_set_sha256=self.benchmark_set_sha256,
            cost_model_set_sha256=self.cost_model_set_sha256,
            candidate_budget_sha256=self.candidate_budget.sha256,
            frozen_at=_utc(frozen_at, "frozen_at"),
        )


def _sha256_payload(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _identifier(value: str, field_name: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise PreregistrationError(f"{field_name} is invalid")


def _version(value: str, field_name: str) -> None:
    if _VERSION.fullmatch(value) is None:
        raise PreregistrationError(f"{field_name} is invalid")


def _sha256(value: str, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise PreregistrationError(f"{field_name} must be a lowercase SHA-256 digest")


def _commit(value: str, field_name: str) -> None:
    if _COMMIT.fullmatch(value) is None:
        raise PreregistrationError(f"{field_name} must be a 40-character Git commit")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PreregistrationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
