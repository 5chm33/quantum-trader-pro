from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantum_trader.domain.inference import (
    AutocorrelationDiagnostic,
    BlockBootstrapConfig,
    BootstrapMethod,
    BootstrapReplicate,
    CandidateBootstrapDiagnostic,
    ComparisonFamilyDiagnostic,
    InferenceError,
    ReturnObservation,
    ReturnSeries,
    RobustnessDiagnostic,
    RobustnessOutcome,
    RobustnessScenario,
    RobustnessScenarioKind,
    SerialDependenceDiagnostics,
    SharpeDiagnosticStatus,
    bootstrap_comparison_family,
    robustness_diagnostic,
    serial_dependence_diagnostics,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_CUTOFF = datetime(2024, 2, 1, 21, 0, tzinfo=UTC)


def _series(
    *,
    series_id: str,
    returns: tuple[str, ...],
    cutoff: datetime = _CUTOFF,
    events_offset: int = 0,
    observations_per_year: int = 252,
) -> ReturnSeries:
    origin = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)
    observations = tuple(
        ReturnObservation(
            record_id=f"{series_id}-return-{index}",
            event_at=origin + timedelta(days=index + events_offset),
            available_at=origin + timedelta(days=index + events_offset, minutes=1),
            return_value=Decimal(value),
            source_sha256=_SHA_A,
        )
        for index, value in enumerate(returns)
    )
    return ReturnSeries(
        series_id=series_id,
        decision_cutoff_at=cutoff,
        observations_per_year=observations_per_year,
        observations=observations,
    )


def _config(**overrides: object) -> BlockBootstrapConfig:
    values: dict[str, object] = {
        "config_version": "block-bootstrap-v1",
        "method": BootstrapMethod.CIRCULAR_MOVING_BLOCK,
        "block_length": 2,
        "replicate_count": 4,
        "seed_sha256": _SHA_B,
    }
    values.update(overrides)
    return BlockBootstrapConfig(**values)  # type: ignore[arg-type]


def test_serial_dependence_diagnostics_retain_frequency_lags_and_deterministic_statistics() -> None:
    series = _series(
        series_id="candidate-alpha",
        returns=("0.01", "0.02", "-0.01", "0.01", "0.03"),
    )
    result = serial_dependence_diagnostics(return_series=series, maximum_lag=2)
    repeated = serial_dependence_diagnostics(return_series=series, maximum_lag=2)
    assert result == repeated
    assert result.series_id == "candidate-alpha"
    assert result.observation_count == 5
    assert result.observations_per_year == 252
    assert result.mean_return == Decimal("0.012")
    assert result.sample_volatility > Decimal("0")
    assert result.sharpe_status is SharpeDiagnosticStatus.AVAILABLE
    assert result.naive_annualized_sharpe is not None
    assert tuple(item.lag for item in result.autocorrelations) == (1, 2)
    assert all(item.value is not None for item in result.autocorrelations)


def test_zero_variance_series_records_unavailable_sharpe_and_explicit_null_autocorrelations() -> (
    None
):
    result = serial_dependence_diagnostics(
        return_series=_series(series_id="constant", returns=("0.01", "0.01", "0.01")),
        maximum_lag=2,
    )
    assert result.sample_volatility == Decimal("0")
    assert result.sharpe_status is SharpeDiagnosticStatus.ZERO_VARIANCE
    assert result.naive_annualized_sharpe is None
    assert tuple(item.value for item in result.autocorrelations) == (None, None)


def test_moving_block_comparison_is_deterministic_and_retains_receipts() -> None:
    baseline = _series(series_id="baseline", returns=("0", "0", "0", "0", "0"))
    alpha = _series(series_id="alpha", returns=("0.01", "0.02", "-0.01", "0.01", "0.03"))
    beta = _series(series_id="beta", returns=("-0.01", "0", "0.01", "0", "0.01"))
    result = bootstrap_comparison_family(
        comparison_id="family-001",
        baseline=baseline,
        candidates=(beta, alpha),
        config=_config(),
    )
    repeated = bootstrap_comparison_family(
        comparison_id="family-001",
        baseline=baseline,
        candidates=(alpha, beta),
        config=_config(),
    )
    assert result == repeated
    assert result.candidate_series_ids == ("alpha", "beta")
    assert len(result.diagnostics) == 2
    alpha_diagnostic = result.diagnostics[0]
    assert alpha_diagnostic.observed_mean_excess_return == Decimal("0.012")
    assert len(alpha_diagnostic.replicates) == 4
    assert tuple(item.replicate_index for item in alpha_diagnostic.replicates) == (0, 1, 2, 3)
    assert all(len(item.block_start_indices) == 3 for item in alpha_diagnostic.replicates)
    assert Decimal("0") <= alpha_diagnostic.one_sided_exceedance_rate <= Decimal("1")


def test_comparison_rejects_implicit_alignment_changes_and_incomplete_candidate_coverage() -> None:
    baseline = _series(series_id="baseline", returns=("0", "0", "0"))
    shifted = _series(series_id="shifted", returns=("0", "0", "0"), events_offset=1)
    mismatched_frequency = _series(
        series_id="frequency", returns=("0", "0", "0"), observations_per_year=12
    )
    with pytest.raises(InferenceError, match="identical chronological"):
        bootstrap_comparison_family(
            comparison_id="family-shifted",
            baseline=baseline,
            candidates=(shifted,),
            config=_config(block_length=2),
        )
    with pytest.raises(InferenceError, match="observations_per_year"):
        bootstrap_comparison_family(
            comparison_id="family-frequency",
            baseline=baseline,
            candidates=(mismatched_frequency,),
            config=_config(block_length=2),
        )
    with pytest.raises(InferenceError, match="block_length"):
        bootstrap_comparison_family(
            comparison_id="family-block-too-long",
            baseline=baseline,
            candidates=(_series(series_id="too-long", returns=("0", "0", "0")),),
            config=_config(block_length=4),
        )

    diagnostic = bootstrap_comparison_family(
        comparison_id="family-complete",
        baseline=baseline,
        candidates=(_series(series_id="alpha", returns=("0", "0", "0")),),
        config=_config(block_length=2),
    ).diagnostics[0]
    with pytest.raises(InferenceError, match="exactly cover"):
        ComparisonFamilyDiagnostic(
            comparison_id="family-incomplete",
            baseline_series_id="BASELINE",
            candidate_series_ids=("ALPHA", "BETA"),
            diagnostics=(diagnostic,),
        )


def test_robustness_diagnostic_requires_complete_predeclared_scenarios_and_keeps_all_outcomes() -> (
    None
):
    base = RobustnessScenario(
        scenario_id="base",
        kind=RobustnessScenarioKind.BASE,
        adverse=False,
        scenario_version="scenario-v1",
    )
    cost = RobustnessScenario(
        scenario_id="cost-stress",
        kind=RobustnessScenarioKind.COST_STRESS,
        adverse=True,
        scenario_version="scenario-v1",
    )
    diagnostic = robustness_diagnostic(
        candidate_series_id="candidate-alpha",
        decision_cutoff_at=_CUTOFF,
        scenarios=(cost, base),
        scenario_series=(
            _series(series_id="cost-stress", returns=("0", "-0.01", "0.01")),
            _series(series_id="base", returns=("0.01", "0.02", "-0.01")),
        ),
    )
    assert diagnostic.candidate_series_id == "candidate-alpha"
    assert tuple(item.scenario_id for item in diagnostic.scenarios) == ("base", "cost-stress")
    assert tuple(item.scenario.scenario_id for item in diagnostic.outcomes) == (
        "base",
        "cost-stress",
    )
    assert diagnostic.outcomes[0].mean_return == Decimal("0.006666666666666666666666666667")

    with pytest.raises(InferenceError, match="equal counts"):
        robustness_diagnostic(
            candidate_series_id="candidate-alpha",
            decision_cutoff_at=_CUTOFF,
            scenarios=(base, cost),
            scenario_series=(_series(series_id="base", returns=("0", "0", "0")),),
        )
    with pytest.raises(InferenceError, match="exactly one base"):
        RobustnessDiagnostic(
            candidate_series_id="candidate-alpha",
            decision_cutoff_at=_CUTOFF,
            scenarios=(cost,),
            outcomes=(),
        )


def test_return_bootstrap_and_scenario_contracts_reject_noncausal_or_invalid_states() -> None:
    with pytest.raises(InferenceError, match="event_at"):
        ReturnObservation(
            record_id="bad-timing",
            event_at=_CUTOFF,
            available_at=_CUTOFF - timedelta(seconds=1),
            return_value=Decimal("0"),
            source_sha256=_SHA_A,
        )
    ordered = _series(series_id="ordered", returns=("0", "0", "0"))
    with pytest.raises(InferenceError, match="chronological order"):
        ReturnSeries(
            series_id="out-of-order",
            decision_cutoff_at=_CUTOFF,
            observations_per_year=252,
            observations=(
                ordered.observations[1],
                ordered.observations[0],
                ordered.observations[2],
            ),
        )
    with pytest.raises(InferenceError, match="unavailable"):
        ReturnSeries(
            series_id="future-availability",
            decision_cutoff_at=_CUTOFF,
            observations_per_year=252,
            observations=(
                *ordered.observations[:2],
                replace(ordered.observations[2], available_at=_CUTOFF + timedelta(seconds=1)),
            ),
        )
    with pytest.raises(InferenceError, match="config_version"):
        _config(config_version="!")
    with pytest.raises(InferenceError, match="block_length"):
        _config(block_length=0)
    with pytest.raises(InferenceError, match="base robustness"):
        RobustnessScenario(
            scenario_id="bad-base",
            kind=RobustnessScenarioKind.BASE,
            adverse=True,
            scenario_version="scenario-v1",
        )
    with pytest.raises(InferenceError, match="non-base"):
        RobustnessScenario(
            scenario_id="bad-cost",
            kind=RobustnessScenarioKind.COST_STRESS,
            adverse=False,
            scenario_version="scenario-v1",
        )


def test_diagnostic_result_guards_reject_unreconciled_pvalues_and_invalid_lag_layout() -> None:
    baseline = _series(series_id="baseline", returns=("0", "0", "0"))
    candidate = _series(series_id="candidate", returns=("0.01", "0", "0"))
    diagnostic = bootstrap_comparison_family(
        comparison_id="family-guard",
        baseline=baseline,
        candidates=(candidate,),
        config=_config(block_length=2),
    ).diagnostics[0]
    with pytest.raises(InferenceError, match="does not reconcile"):
        replace(diagnostic, one_sided_exceedance_rate=Decimal("1"))
    with pytest.raises(InferenceError, match="ascending lag"):
        replace(
            serial_dependence_diagnostics(return_series=candidate, maximum_lag=2),
            autocorrelations=(
                AutocorrelationDiagnostic(lag=2, value=Decimal("0")),
                AutocorrelationDiagnostic(lag=1, value=Decimal("0")),
            ),
        )
    with pytest.raises(InferenceError, match="replicates must use canonical"):
        CandidateBootstrapDiagnostic(
            candidate_series_id=diagnostic.candidate_series_id,
            baseline_series_id=diagnostic.baseline_series_id,
            decision_cutoff_at=diagnostic.decision_cutoff_at,
            config=diagnostic.config,
            observation_count=diagnostic.observation_count,
            observed_mean_excess_return=diagnostic.observed_mean_excess_return,
            one_sided_exceedance_rate=diagnostic.one_sided_exceedance_rate,
            replicates=(
                replace(diagnostic.replicates[0], replicate_index=1),
                *diagnostic.replicates[1:],
            ),
        )


def test_return_series_and_bootstrap_input_guards_reject_incomplete_or_invalid_evidence() -> None:
    ordered = _series(series_id="ordered", returns=("0", "0", "0"))
    with pytest.raises(InferenceError, match="observations_per_year"):
        replace(ordered, observations_per_year=0)
    with pytest.raises(InferenceError, match="at least two"):
        replace(ordered, observations=(ordered.observations[0],))
    with pytest.raises(InferenceError, match="unique event"):
        replace(ordered, observations=(ordered.observations[0], *ordered.observations[:2]))
    with pytest.raises(InferenceError, match="unique record"):
        replace(
            ordered,
            observations=(
                ordered.observations[0],
                replace(ordered.observations[1], record_id=ordered.observations[0].record_id),
                ordered.observations[2],
            ),
        )
    with pytest.raises(InferenceError, match="unsupported"):
        _config(method="iid")  # type: ignore[arg-type]
    with pytest.raises(InferenceError, match="replicate_count"):
        _config(replicate_count=0)
    with pytest.raises(InferenceError, match="autocorrelation lag"):
        AutocorrelationDiagnostic(lag=0, value=None)
    with pytest.raises(InferenceError, match="between minus one"):
        AutocorrelationDiagnostic(lag=1, value=Decimal("1.01"))
    with pytest.raises(InferenceError, match="maximum_lag"):
        serial_dependence_diagnostics(return_series=ordered, maximum_lag=3)


def test_serial_diagnostic_and_bootstrap_replicate_guards_reject_incoherent_states() -> None:
    series = _series(series_id="series", returns=("0.01", "0", "-0.01"))
    diagnostic = serial_dependence_diagnostics(return_series=series, maximum_lag=1)
    with pytest.raises(InferenceError, match="observation_count"):
        replace(diagnostic, observation_count=1)
    with pytest.raises(InferenceError, match="observations_per_year"):
        replace(diagnostic, observations_per_year=0)
    with pytest.raises(InferenceError, match="available Sharpe"):
        replace(diagnostic, sample_volatility=Decimal("0"))
    zero = serial_dependence_diagnostics(
        return_series=_series(series_id="constant-guard", returns=("0", "0", "0")), maximum_lag=1
    )
    with pytest.raises(InferenceError, match="zero-variance"):
        replace(zero, naive_annualized_sharpe=Decimal("1"))
    with pytest.raises(InferenceError, match="below observation_count"):
        SerialDependenceDiagnostics(
            series_id="series",
            observation_count=3,
            observations_per_year=252,
            mean_return=Decimal("0"),
            sample_volatility=Decimal("0"),
            naive_annualized_sharpe=None,
            sharpe_status=SharpeDiagnosticStatus.ZERO_VARIANCE,
            autocorrelations=(AutocorrelationDiagnostic(lag=3, value=None),),
        )
    with pytest.raises(InferenceError, match="replicate_index"):
        BootstrapReplicate(
            replicate_index=-1, block_start_indices=(0,), centered_sample_mean=Decimal("0")
        )
    with pytest.raises(InferenceError, match="block start"):
        BootstrapReplicate(
            replicate_index=0, block_start_indices=(), centered_sample_mean=Decimal("0")
        )


def test_candidate_bootstrap_and_comparison_guards_reject_partial_or_mixed_families() -> None:
    baseline = _series(series_id="baseline", returns=("0", "0", "0"))
    candidate = _series(series_id="candidate", returns=("0.01", "0", "-0.01"))
    result = bootstrap_comparison_family(
        comparison_id="family-candidate-guard",
        baseline=baseline,
        candidates=(candidate,),
        config=_config(block_length=2),
    )
    diagnostic = result.diagnostics[0]
    with pytest.raises(InferenceError, match="must differ"):
        replace(diagnostic, baseline_series_id=diagnostic.candidate_series_id)
    with pytest.raises(InferenceError, match="at least two"):
        replace(diagnostic, observation_count=1)
    with pytest.raises(InferenceError, match="cannot exceed"):
        replace(diagnostic, config=_config(block_length=4))
    with pytest.raises(InferenceError, match="cannot exceed one"):
        replace(diagnostic, one_sided_exceedance_rate=Decimal("1.1"))
    with pytest.raises(InferenceError, match="replicates must match"):
        replace(diagnostic, replicates=diagnostic.replicates[:-1])
    with pytest.raises(InferenceError, match="outside observation"):
        replace(
            diagnostic,
            replicates=(
                replace(diagnostic.replicates[0], block_start_indices=(3,)),
                *diagnostic.replicates[1:],
            ),
        )
    with pytest.raises(InferenceError, match="at least one candidate"):
        ComparisonFamilyDiagnostic(
            comparison_id="empty-family",
            baseline_series_id="baseline",
            candidate_series_ids=(),
            diagnostics=(),
        )
    with pytest.raises(InferenceError, match="canonical sorted"):
        ComparisonFamilyDiagnostic(
            comparison_id="unordered-family",
            baseline_series_id="baseline",
            candidate_series_ids=("zeta", "alpha"),
            diagnostics=(diagnostic,),
        )
    with pytest.raises(InferenceError, match="declared baseline"):
        replace(
            result,
            baseline_series_id="other-baseline",
        )


def test_comparison_and_robustness_functions_reject_mixed_inputs() -> None:
    baseline = _series(series_id="baseline", returns=("0", "0", "0"))
    candidate = _series(series_id="candidate", returns=("0", "0", "0"))
    with pytest.raises(InferenceError, match="at least one candidate"):
        bootstrap_comparison_family(
            comparison_id="no-candidates",
            baseline=baseline,
            candidates=(),
            config=_config(block_length=2),
        )
    with pytest.raises(InferenceError, match="unique series ids"):
        bootstrap_comparison_family(
            comparison_id="duplicate-candidates",
            baseline=baseline,
            candidates=(candidate, candidate),
            config=_config(block_length=2),
        )
    with pytest.raises(InferenceError, match="cannot be included"):
        bootstrap_comparison_family(
            comparison_id="baseline-in-candidates",
            baseline=baseline,
            candidates=(baseline,),
            config=_config(block_length=2),
        )
    base = RobustnessScenario(
        scenario_id="base",
        kind=RobustnessScenarioKind.BASE,
        adverse=False,
        scenario_version="scenario-v1",
    )
    cost = RobustnessScenario(
        scenario_id="cost",
        kind=RobustnessScenarioKind.COST_STRESS,
        adverse=True,
        scenario_version="scenario-v1",
    )
    with pytest.raises(InferenceError, match="requested decision cutoff"):
        robustness_diagnostic(
            candidate_series_id="candidate",
            decision_cutoff_at=_CUTOFF,
            scenarios=(base,),
            scenario_series=(
                _series(
                    series_id="base",
                    returns=("0", "0", "0"),
                    cutoff=_CUTOFF + timedelta(days=1),
                ),
            ),
        )
    with pytest.raises(InferenceError, match="exactly match"):
        robustness_diagnostic(
            candidate_series_id="candidate",
            decision_cutoff_at=_CUTOFF,
            scenarios=(base,),
            scenario_series=(_series(series_id="different", returns=("0", "0", "0")),),
        )
    outcome = RobustnessOutcome(
        scenario=base,
        return_series=_series(series_id="base", returns=("0", "0", "0")),
        mean_return=Decimal("0"),
    )
    with pytest.raises(InferenceError, match="does not reconcile"):
        replace(outcome, mean_return=Decimal("0.01"))
    with pytest.raises(InferenceError, match="scenario_version"):
        replace(cost, scenario_version="!")


def test_robustness_and_primitive_validation_reject_bad_values() -> None:
    base = RobustnessScenario(
        scenario_id="base",
        kind=RobustnessScenarioKind.BASE,
        adverse=False,
        scenario_version="scenario-v1",
    )
    cost = RobustnessScenario(
        scenario_id="cost",
        kind=RobustnessScenarioKind.COST_STRESS,
        adverse=True,
        scenario_version="scenario-v1",
    )
    base_series = _series(series_id="base", returns=("0", "0", "0"))
    outcome = RobustnessOutcome(scenario=base, return_series=base_series, mean_return=Decimal("0"))
    with pytest.raises(InferenceError, match="at least one scenario"):
        RobustnessDiagnostic(
            candidate_series_id="candidate", decision_cutoff_at=_CUTOFF, scenarios=(), outcomes=()
        )
    with pytest.raises(InferenceError, match="canonical sorted"):
        RobustnessDiagnostic(
            candidate_series_id="candidate",
            decision_cutoff_at=_CUTOFF,
            scenarios=(cost, base),
            outcomes=(outcome,),
        )
    with pytest.raises(InferenceError, match="exactly cover"):
        RobustnessDiagnostic(
            candidate_series_id="candidate",
            decision_cutoff_at=_CUTOFF,
            scenarios=(base, cost),
            outcomes=(outcome,),
        )
    with pytest.raises(InferenceError, match="record_id"):
        ReturnObservation(
            record_id="!",
            event_at=_CUTOFF,
            available_at=_CUTOFF,
            return_value=Decimal("0"),
            source_sha256=_SHA_A,
        )
    with pytest.raises(InferenceError, match="source_sha256"):
        ReturnObservation(
            record_id="bad-sha",
            event_at=_CUTOFF,
            available_at=_CUTOFF,
            return_value=Decimal("0"),
            source_sha256="bad",
        )
    with pytest.raises(InferenceError, match="timezone-aware"):
        ReturnObservation(
            record_id="naive",
            event_at=datetime(2024, 1, 1),
            available_at=_CUTOFF,
            return_value=Decimal("0"),
            source_sha256=_SHA_A,
        )
    with pytest.raises(InferenceError, match="return_value must be finite"):
        ReturnObservation(
            record_id="nan",
            event_at=_CUTOFF,
            available_at=_CUTOFF,
            return_value=Decimal("NaN"),
            source_sha256=_SHA_A,
        )
