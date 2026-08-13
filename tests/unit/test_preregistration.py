from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from quantum_trader.domain.experiments import (
    CampaignRegistration,
    CandidateRegistration,
    HoldoutSeal,
)
from quantum_trader.domain.preregistration import (
    CampaignPreregistrationPlan,
    CandidateBudgetPlan,
    CandidateFamilyBudget,
    NewCampaignLockbox,
    PermanentBaseline,
    PreregistrationError,
    RegimeLabel,
    RegimePlan,
    RegimeWindow,
    WalkForwardFold,
    WalkForwardPlan,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_COMMIT_A = "a" * 40
_COMMIT_B = "b" * 40
_BASE = datetime(2020, 1, 1, tzinfo=UTC)


def _campaign(*, campaign_id: str = "campaign-v2") -> CampaignRegistration:
    return CampaignRegistration(
        campaign_id=campaign_id,
        governance_policy_sha256=_SHA_A,
        hypothesis_catalog_sha256=_SHA_A,
        data_contract_manifest_sha256=_SHA_A,
        baseline_commit=_COMMIT_A,
        registered_at=_BASE,
    )


def _candidate(
    *,
    candidate_id: str = "candidate-001",
    campaign_id: str = "campaign-v2",
    family_id: str = "H01",
    candidate_index: int = 1,
    candidate_ceiling: int = 2,
    code_commit: str = _COMMIT_A,
) -> CandidateRegistration:
    return CandidateRegistration(
        candidate_id=candidate_id,
        campaign_id=campaign_id,
        family_id=family_id,
        candidate_index=candidate_index,
        candidate_ceiling=candidate_ceiling,
        specification_sha256=_SHA_A,
        code_commit=code_commit,
        registered_at=_BASE,
    )


def _budget() -> CandidateBudgetPlan:
    return CandidateBudgetPlan(
        budget_id="budget-v2",
        budget_version="budget-v2",
        families=(
            CandidateFamilyBudget(family_id="H01", candidate_ceiling=2),
            CandidateFamilyBudget(family_id="H02", candidate_ceiling=1),
        ),
    )


def _fold(*, fold_id: str, start_days: int) -> WalkForwardFold:
    start = _BASE + timedelta(days=start_days)
    return WalkForwardFold(
        fold_id=fold_id,
        train_start_at=start,
        train_end_at=start + timedelta(days=2),
        validation_start_at=start + timedelta(days=3),
        validation_end_at=start + timedelta(days=5),
        test_start_at=start + timedelta(days=6),
        test_end_at=start + timedelta(days=8),
        embargo=timedelta(days=1),
    )


def _walk_forward() -> WalkForwardPlan:
    return WalkForwardPlan(
        plan_id="walk-forward-v2",
        plan_version="walk-forward-v2",
        folds=(
            _fold(fold_id="fold-001", start_days=0),
            _fold(fold_id="fold-002", start_days=9),
        ),
    )


def _regimes() -> RegimePlan:
    return RegimePlan(
        regime_plan_id="regime-plan-v2",
        classifier_version="regime-v2",
        classifier_specification_sha256=_SHA_B,
        regimes=(
            RegimeWindow(
                regime_id="regime-001",
                label=RegimeLabel.CALM,
                start_at=_BASE,
                end_at=_BASE + timedelta(days=20),
            ),
            RegimeWindow(
                regime_id="regime-002",
                label=RegimeLabel.STRESSED,
                start_at=_BASE + timedelta(days=20),
                end_at=_BASE + timedelta(days=40),
            ),
        ),
    )


def _lockbox(
    *, campaign_id: str = "campaign-v2", candidate_id: str = "candidate-001"
) -> NewCampaignLockbox:
    return NewCampaignLockbox(
        seal=HoldoutSeal(
            holdout_id="lockbox-v2",
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            boundary_sha256=_SHA_A,
            provider_query_sha256=_SHA_B,
            bytes_retrieved=False,
            sealed_at=_BASE,
        ),
        requires_explicit_user_approval=True,
        legacy_v1_holdout_policy_sha256=_SHA_A,
    )


def _plan(**overrides: object) -> CampaignPreregistrationPlan:
    values: dict[str, object] = {
        "protocol_id": "protocol-v2",
        "protocol_version": "protocol-v2",
        "campaign": _campaign(),
        "data_snapshot_id": "snapshot-v2",
        "data_snapshot_manifest_sha256": _SHA_A,
        "benchmark_set_sha256": _SHA_A,
        "cost_model_set_sha256": _SHA_A,
        "inference_plan_sha256": _SHA_B,
        "permanent_baselines": (
            PermanentBaseline.CASH,
            PermanentBaseline.EQUAL_WEIGHT,
            PermanentBaseline.TREND_ONLY,
        ),
        "candidate_budget": _budget(),
        "walk_forward": _walk_forward(),
        "regimes": _regimes(),
        "lockbox": _lockbox(),
        "code_commit": _COMMIT_A,
        "created_at": _BASE,
    }
    values.update(overrides)
    return CampaignPreregistrationPlan(**values)  # type: ignore[arg-type]


def test_preregistration_plan_is_deterministic_and_freezes_a_ledger_compatible_candidate() -> None:
    plan = _plan()
    repeated = _plan()
    candidate = _candidate()
    freeze = plan.freeze_candidate(candidate, frozen_at=_BASE + timedelta(days=1))
    assert plan.protocol_sha256 == repeated.protocol_sha256
    assert plan.partition_plan_sha256 == repeated.partition_plan_sha256
    assert plan.candidate_budget.sha256 == repeated.candidate_budget.sha256
    assert plan.lockbox.seal.bytes_retrieved is False
    assert plan.lockbox.requires_explicit_user_approval is True
    assert freeze.candidate_id == candidate.candidate_id
    assert freeze.protocol_id == plan.protocol_id
    assert freeze.protocol_sha256 == plan.protocol_sha256
    assert freeze.partition_plan_sha256 == plan.partition_plan_sha256
    assert freeze.candidate_budget_sha256 == plan.candidate_budget.sha256
    assert freeze.cost_model_set_sha256 == _SHA_A


def test_candidate_budget_requires_canonical_families_and_exact_candidate_ceiling_binding() -> None:
    with pytest.raises(PreregistrationError, match="candidate budget requires"):
        CandidateBudgetPlan(budget_id="budget-empty", budget_version="budget-v2", families=())
    with pytest.raises(PreregistrationError, match="canonically ordered"):
        CandidateBudgetPlan(
            budget_id="budget-unsorted",
            budget_version="budget-v2",
            families=(
                CandidateFamilyBudget(family_id="H02", candidate_ceiling=1),
                CandidateFamilyBudget(family_id="H01", candidate_ceiling=1),
            ),
        )
    plan = _plan()
    with pytest.raises(PreregistrationError, match="absent"):
        plan.candidate_budget.validate_candidate(_candidate(family_id="H03", candidate_ceiling=1))
    with pytest.raises(PreregistrationError, match="ceiling differs"):
        plan.candidate_budget.validate_candidate(_candidate(candidate_ceiling=1))
    with pytest.raises(PreregistrationError, match="candidate_ceiling"):
        CandidateFamilyBudget(family_id="H01", candidate_ceiling=0)


def test_walk_forward_folds_require_positive_embargo_nonempty_windows_and_nonoverlap() -> None:
    fold = _fold(fold_id="fold-001", start_days=0)
    with pytest.raises(PreregistrationError, match="embargo"):
        replace(fold, embargo=timedelta(0))
    with pytest.raises(PreregistrationError, match="train window"):
        replace(fold, train_end_at=fold.train_start_at)
    with pytest.raises(PreregistrationError, match="validation and test"):
        replace(fold, test_start_at=fold.validation_end_at)
    with pytest.raises(PreregistrationError, match="at least two"):
        WalkForwardPlan(plan_id="walk-single", plan_version="walk-v2", folds=(fold,))
    with pytest.raises(PreregistrationError, match="canonical sorted"):
        WalkForwardPlan(
            plan_id="walk-unsorted",
            plan_version="walk-v2",
            folds=(_fold(fold_id="fold-002", start_days=9), fold),
        )
    with pytest.raises(PreregistrationError, match="must not overlap"):
        WalkForwardPlan(
            plan_id="walk-overlap",
            plan_version="walk-v2",
            folds=(fold, _fold(fold_id="fold-002", start_days=7)),
        )


def test_regime_plan_requires_distinct_ex_ante_labels_ordered_ids_and_nonoverlapping_windows() -> (
    None
):
    first = _regimes().regimes[0]
    second = _regimes().regimes[1]
    with pytest.raises(PreregistrationError, match="at least two"):
        RegimePlan(
            regime_plan_id="regime-single",
            classifier_version="regime-v2",
            classifier_specification_sha256=_SHA_A,
            regimes=(first,),
        )
    with pytest.raises(PreregistrationError, match="regime labels"):
        replace(_regimes(), regimes=(first, replace(second, label=RegimeLabel.CALM)))
    with pytest.raises(PreregistrationError, match="must not overlap"):
        replace(
            _regimes(),
            regimes=(first, replace(second, start_at=_BASE + timedelta(days=19))),
        )
    with pytest.raises(PreregistrationError, match="regime window"):
        replace(first, end_at=first.start_at)
    assert _regimes().sha256 == _regimes().sha256


def test_new_campaign_lockbox_remains_sealed_separate_and_requires_user_approval() -> None:
    with pytest.raises(PreregistrationError, match="require explicit"):
        replace(_lockbox(), requires_explicit_user_approval=False)
    with pytest.raises(ValueError, match="before its bytes"):
        HoldoutSeal(
            holdout_id="lockbox-open",
            campaign_id="campaign-v2",
            candidate_id="candidate-001",
            boundary_sha256=_SHA_A,
            provider_query_sha256=_SHA_B,
            bytes_retrieved=True,
            sealed_at=_BASE,
        )
    with pytest.raises(PreregistrationError, match="must be bound"):
        _plan(lockbox=_lockbox(campaign_id="campaign-other"))


def test_plan_requires_permanent_baselines_and_freeze_rejects_campaign_or_code_changes() -> None:
    with pytest.raises(PreregistrationError, match="all permanent"):
        _plan(permanent_baselines=(PermanentBaseline.CASH, PermanentBaseline.EQUAL_WEIGHT))
    with pytest.raises(PreregistrationError, match="canonical sorted"):
        _plan(
            permanent_baselines=(
                PermanentBaseline.TREND_ONLY,
                PermanentBaseline.CASH,
                PermanentBaseline.EQUAL_WEIGHT,
            )
        )
    plan = _plan()
    with pytest.raises(PreregistrationError, match="different preregistration campaign"):
        plan.freeze_candidate(
            _candidate(campaign_id="campaign-other"), frozen_at=_BASE + timedelta(days=1)
        )
    with pytest.raises(PreregistrationError, match="code commit differs"):
        plan.freeze_candidate(
            _candidate(code_commit=_COMMIT_B), frozen_at=_BASE + timedelta(days=1)
        )


def test_preregistration_input_guards_reject_invalid_ids_versions_hashes_commits_and_times() -> (
    None
):
    with pytest.raises(PreregistrationError, match="family_id"):
        CandidateFamilyBudget(family_id="bad", candidate_ceiling=1)
    with pytest.raises(PreregistrationError, match="budget_id"):
        CandidateBudgetPlan(
            budget_id="bad", budget_version="budget-v2", families=_budget().families
        )
    with pytest.raises(PreregistrationError, match="plan_version"):
        WalkForwardPlan(plan_id="walk-plan", plan_version="!", folds=_walk_forward().folds)
    with pytest.raises(PreregistrationError, match="classifier_specification"):
        RegimePlan(
            regime_plan_id="regime-bad-hash",
            classifier_version="regime-v2",
            classifier_specification_sha256="bad",
            regimes=_regimes().regimes,
        )
    with pytest.raises(PreregistrationError, match="timezone-aware"):
        replace(_fold(fold_id="fold-001", start_days=0), train_start_at=datetime(2020, 1, 1))
    with pytest.raises(PreregistrationError, match="code_commit"):
        _plan(code_commit="bad")
