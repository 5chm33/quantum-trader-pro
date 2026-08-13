"""Dependence-aware inference and robustness diagnostics for research evidence.

The module does not determine strategy promotion, generate a performance claim, or unlock a
holdout.  It records chronological, point-in-time return observations and applies a declared,
deterministic circular moving-block resampling procedure.  The resulting quantities are
explicit diagnostics that later ledger-gated research may interpret only under a preregistered
protocol.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from enum import StrEnum

_ZERO = Decimal("0")
_ONE = Decimal("1")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$")
_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class InferenceError(ValueError):
    """Raised when diagnostic evidence would be non-causal, incomplete, or unreproducible."""


class BootstrapMethod(StrEnum):
    """Resampling method explicitly identified in retained research evidence."""

    CIRCULAR_MOVING_BLOCK = "circular_moving_block"


class SharpeDiagnosticStatus(StrEnum):
    """Whether a naive annualized Sharpe diagnostic is defined, never a performance verdict."""

    AVAILABLE = "available"
    ZERO_VARIANCE = "zero_variance"


class RobustnessScenarioKind(StrEnum):
    """Predeclared scenario types reported independently from baseline performance."""

    BASE = "base"
    COST_STRESS = "cost_stress"
    PARAMETER_PERTURBATION = "parameter_perturbation"
    PLACEBO = "placebo"
    REGIME = "regime"


@dataclass(frozen=True, slots=True)
class ReturnObservation:
    """One checksum-bound realized return that was available by a series decision cutoff."""

    record_id: str
    event_at: datetime
    available_at: datetime
    return_value: Decimal
    source_sha256: str

    def __post_init__(self) -> None:
        _identifier(self.record_id, "record_id")
        event_at = _utc(self.event_at, "event_at")
        available_at = _utc(self.available_at, "available_at")
        if event_at > available_at:
            raise InferenceError("return observation event_at cannot follow available_at")
        _finite(self.return_value, "return_value")
        _sha256(self.source_sha256, "source_sha256")
        object.__setattr__(self, "event_at", event_at)
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True, slots=True)
class ReturnSeries:
    """Chronological point-in-time return series with an explicit frequency convention."""

    series_id: str
    decision_cutoff_at: datetime
    observations_per_year: int
    observations: tuple[ReturnObservation, ...]

    def __post_init__(self) -> None:
        _identifier(self.series_id, "series_id")
        cutoff = _utc(self.decision_cutoff_at, "decision_cutoff_at")
        if self.observations_per_year < 1:
            raise InferenceError("observations_per_year must be positive")
        if len(self.observations) < 2:
            raise InferenceError("return series requires at least two observations")
        event_times = tuple(item.event_at for item in self.observations)
        if event_times != tuple(sorted(event_times)):
            raise InferenceError("return observations must use chronological order")
        if len(set(event_times)) != len(event_times):
            raise InferenceError("return observations must have unique event timestamps")
        record_ids = tuple(item.record_id for item in self.observations)
        if len(set(record_ids)) != len(record_ids):
            raise InferenceError("return observations must have unique record ids")
        if any(item.available_at > cutoff for item in self.observations):
            raise InferenceError("return series uses an observation unavailable at decision cutoff")
        object.__setattr__(self, "decision_cutoff_at", cutoff)


@dataclass(frozen=True, slots=True)
class BlockBootstrapConfig:
    """Versioned and deterministic dependence-aware resampling configuration."""

    config_version: str
    method: BootstrapMethod
    block_length: int
    replicate_count: int
    seed_sha256: str

    def __post_init__(self) -> None:
        if _VERSION.fullmatch(self.config_version) is None:
            raise InferenceError("bootstrap config_version is invalid")
        if self.method is not BootstrapMethod.CIRCULAR_MOVING_BLOCK:
            raise InferenceError("unsupported bootstrap method")
        if self.block_length < 1:
            raise InferenceError("block_length must be positive")
        if self.replicate_count < 1:
            raise InferenceError("replicate_count must be positive")
        _sha256(self.seed_sha256, "seed_sha256")


@dataclass(frozen=True, slots=True)
class AutocorrelationDiagnostic:
    """Sample autocorrelation at one retained lag; `None` explicitly denotes zero variance."""

    lag: int
    value: Decimal | None

    def __post_init__(self) -> None:
        if self.lag < 1:
            raise InferenceError("autocorrelation lag must be positive")
        if self.value is not None:
            _finite(self.value, "autocorrelation value")
            if self.value < Decimal("-1") or self.value > _ONE:
                raise InferenceError("autocorrelation value must be between minus one and one")


@dataclass(frozen=True, slots=True)
class SerialDependenceDiagnostics:
    """Descriptive serial-dependence diagnostics, not an inference conclusion."""

    series_id: str
    observation_count: int
    observations_per_year: int
    mean_return: Decimal
    sample_volatility: Decimal
    naive_annualized_sharpe: Decimal | None
    sharpe_status: SharpeDiagnosticStatus
    autocorrelations: tuple[AutocorrelationDiagnostic, ...]

    def __post_init__(self) -> None:
        _identifier(self.series_id, "series_id")
        if self.observation_count < 2:
            raise InferenceError("diagnostic observation_count must be at least two")
        if self.observations_per_year < 1:
            raise InferenceError("diagnostic observations_per_year must be positive")
        _finite(self.mean_return, "mean_return")
        _finite(self.sample_volatility, "sample_volatility", nonnegative=True)
        lags = tuple(item.lag for item in self.autocorrelations)
        if lags != tuple(sorted(lags)) or len(set(lags)) != len(lags):
            raise InferenceError("autocorrelations must use unique ascending lag order")
        if any(lag >= self.observation_count for lag in lags):
            raise InferenceError("autocorrelation lag must be below observation_count")
        if self.sharpe_status is SharpeDiagnosticStatus.AVAILABLE:
            if self.sample_volatility == _ZERO or self.naive_annualized_sharpe is None:
                raise InferenceError(
                    "available Sharpe diagnostic requires positive volatility and value"
                )
            _finite(self.naive_annualized_sharpe, "naive_annualized_sharpe")
        elif self.naive_annualized_sharpe is not None or self.sample_volatility != _ZERO:
            raise InferenceError(
                "zero-variance diagnostic must omit Sharpe and retain zero volatility"
            )


@dataclass(frozen=True, slots=True)
class BootstrapReplicate:
    """One deterministic circular moving-block sample receipt and its centered sample mean."""

    replicate_index: int
    block_start_indices: tuple[int, ...]
    centered_sample_mean: Decimal

    def __post_init__(self) -> None:
        if self.replicate_index < 0:
            raise InferenceError("replicate_index must be nonnegative")
        if not self.block_start_indices or any(index < 0 for index in self.block_start_indices):
            raise InferenceError("bootstrap replicate requires nonnegative block start indices")
        _finite(self.centered_sample_mean, "centered_sample_mean")


@dataclass(frozen=True, slots=True)
class CandidateBootstrapDiagnostic:
    """One candidate-versus-baseline diagnostic retaining every resampling receipt."""

    candidate_series_id: str
    baseline_series_id: str
    decision_cutoff_at: datetime
    config: BlockBootstrapConfig
    observation_count: int
    observed_mean_excess_return: Decimal
    one_sided_exceedance_rate: Decimal
    replicates: tuple[BootstrapReplicate, ...]

    def __post_init__(self) -> None:
        _identifier(self.candidate_series_id, "candidate_series_id")
        _identifier(self.baseline_series_id, "baseline_series_id")
        if self.candidate_series_id == self.baseline_series_id:
            raise InferenceError("candidate and baseline series ids must differ")
        object.__setattr__(
            self, "decision_cutoff_at", _utc(self.decision_cutoff_at, "decision_cutoff_at")
        )
        if self.observation_count < 2:
            raise InferenceError("bootstrap diagnostic requires at least two observations")
        if self.config.block_length > self.observation_count:
            raise InferenceError("block_length cannot exceed observation_count")
        _finite(self.observed_mean_excess_return, "observed_mean_excess_return")
        _finite(self.one_sided_exceedance_rate, "one_sided_exceedance_rate", nonnegative=True)
        if self.one_sided_exceedance_rate > _ONE:
            raise InferenceError("one_sided_exceedance_rate cannot exceed one")
        if len(self.replicates) != self.config.replicate_count:
            raise InferenceError("bootstrap replicates must match config replicate_count")
        indices = tuple(item.replicate_index for item in self.replicates)
        if indices != tuple(range(self.config.replicate_count)):
            raise InferenceError("bootstrap replicates must use canonical contiguous indices")
        for replicate in self.replicates:
            if any(index >= self.observation_count for index in replicate.block_start_indices):
                raise InferenceError("bootstrap block start index is outside observation_count")
        exceedance_count = sum(
            item.centered_sample_mean >= self.observed_mean_excess_return
            for item in self.replicates
        )
        expected_rate = Decimal(exceedance_count) / Decimal(self.config.replicate_count)
        if self.one_sided_exceedance_rate != expected_rate:
            raise InferenceError("one_sided_exceedance_rate does not reconcile to replicates")


@dataclass(frozen=True, slots=True)
class ComparisonFamilyDiagnostic:
    """Complete baseline-versus-candidate family; omission of an intended candidate is rejected."""

    comparison_id: str
    baseline_series_id: str
    candidate_series_ids: tuple[str, ...]
    diagnostics: tuple[CandidateBootstrapDiagnostic, ...]

    def __post_init__(self) -> None:
        _identifier(self.comparison_id, "comparison_id")
        _identifier(self.baseline_series_id, "baseline_series_id")
        if not self.candidate_series_ids:
            raise InferenceError("comparison family requires at least one candidate series")
        if self.candidate_series_ids != tuple(sorted(self.candidate_series_ids)):
            raise InferenceError("candidate series ids must use canonical sorted order")
        if len(set(self.candidate_series_ids)) != len(self.candidate_series_ids):
            raise InferenceError("candidate series ids must be unique")
        if self.baseline_series_id in self.candidate_series_ids:
            raise InferenceError("baseline series cannot be a candidate")
        diagnostic_ids = tuple(item.candidate_series_id for item in self.diagnostics)
        if diagnostic_ids != self.candidate_series_ids:
            raise InferenceError("comparison diagnostics must exactly cover declared candidates")
        if any(item.baseline_series_id != self.baseline_series_id for item in self.diagnostics):
            raise InferenceError("comparison diagnostics must use the declared baseline")
        cutoffs = {item.decision_cutoff_at for item in self.diagnostics}
        configs = {item.config for item in self.diagnostics}
        if len(cutoffs) != 1 or len(configs) != 1:
            raise InferenceError(
                "comparison diagnostics must share decision cutoff and bootstrap config"
            )


@dataclass(frozen=True, slots=True)
class RobustnessScenario:
    """One predeclared diagnostic scenario; labels intentionally describe stress, not success."""

    scenario_id: str
    kind: RobustnessScenarioKind
    adverse: bool
    scenario_version: str

    def __post_init__(self) -> None:
        _identifier(self.scenario_id, "scenario_id")
        if _VERSION.fullmatch(self.scenario_version) is None:
            raise InferenceError("scenario_version is invalid")
        if self.kind is RobustnessScenarioKind.BASE and self.adverse:
            raise InferenceError("base robustness scenario cannot be adverse")
        if self.kind is not RobustnessScenarioKind.BASE and not self.adverse:
            raise InferenceError("non-base robustness scenarios must be declared adverse")


@dataclass(frozen=True, slots=True)
class RobustnessOutcome:
    """One complete scenario return series retained without selecting a preferred result."""

    scenario: RobustnessScenario
    return_series: ReturnSeries
    mean_return: Decimal

    def __post_init__(self) -> None:
        _finite(self.mean_return, "mean_return")
        expected = _mean(tuple(item.return_value for item in self.return_series.observations))
        if self.mean_return != expected:
            raise InferenceError(
                "robustness outcome mean_return does not reconcile to return series"
            )


@dataclass(frozen=True, slots=True)
class RobustnessDiagnostic:
    """Exact scenario set for one candidate; an incomplete set cannot be represented."""

    candidate_series_id: str
    decision_cutoff_at: datetime
    scenarios: tuple[RobustnessScenario, ...]
    outcomes: tuple[RobustnessOutcome, ...]

    def __post_init__(self) -> None:
        _identifier(self.candidate_series_id, "candidate_series_id")
        cutoff = _utc(self.decision_cutoff_at, "decision_cutoff_at")
        scenario_ids = tuple(item.scenario_id for item in self.scenarios)
        if not scenario_ids:
            raise InferenceError("robustness diagnostic requires at least one scenario")
        if scenario_ids != tuple(sorted(scenario_ids)) or len(set(scenario_ids)) != len(
            scenario_ids
        ):
            raise InferenceError("robustness scenarios must use unique canonical sorted order")
        if sum(item.kind is RobustnessScenarioKind.BASE for item in self.scenarios) != 1:
            raise InferenceError("robustness diagnostic requires exactly one base scenario")
        outcome_ids = tuple(item.scenario.scenario_id for item in self.outcomes)
        if outcome_ids != scenario_ids:
            raise InferenceError("robustness outcomes must exactly cover declared scenarios")
        if any(item.return_series.decision_cutoff_at != cutoff for item in self.outcomes):
            raise InferenceError("robustness outcomes must share the diagnostic decision cutoff")
        object.__setattr__(self, "decision_cutoff_at", cutoff)


def serial_dependence_diagnostics(
    *, return_series: ReturnSeries, maximum_lag: int
) -> SerialDependenceDiagnostics:
    """Describe sample mean, volatility, naive Sharpe, and autocorrelation at declared lags."""

    if not 1 <= maximum_lag < len(return_series.observations):
        raise InferenceError("maximum_lag must be between one and observation_count minus one")
    values = tuple(item.return_value for item in return_series.observations)
    mean = _mean(values)
    sample_volatility = _sample_volatility(values=values, mean=mean)
    correlations = tuple(
        AutocorrelationDiagnostic(
            lag=lag, value=_autocorrelation(values=values, mean=mean, lag=lag)
        )
        for lag in range(1, maximum_lag + 1)
    )
    if sample_volatility == _ZERO:
        return SerialDependenceDiagnostics(
            series_id=return_series.series_id,
            observation_count=len(values),
            observations_per_year=return_series.observations_per_year,
            mean_return=mean,
            sample_volatility=sample_volatility,
            naive_annualized_sharpe=None,
            sharpe_status=SharpeDiagnosticStatus.ZERO_VARIANCE,
            autocorrelations=correlations,
        )
    with localcontext() as context:
        context.prec = 40
        naive_sharpe = (
            mean / sample_volatility * Decimal(return_series.observations_per_year).sqrt()
        )
    _finite(naive_sharpe, "naive_annualized_sharpe")
    return SerialDependenceDiagnostics(
        series_id=return_series.series_id,
        observation_count=len(values),
        observations_per_year=return_series.observations_per_year,
        mean_return=mean,
        sample_volatility=sample_volatility,
        naive_annualized_sharpe=naive_sharpe,
        sharpe_status=SharpeDiagnosticStatus.AVAILABLE,
        autocorrelations=correlations,
    )


def bootstrap_comparison_family(
    *,
    comparison_id: str,
    baseline: ReturnSeries,
    candidates: Sequence[ReturnSeries],
    config: BlockBootstrapConfig,
) -> ComparisonFamilyDiagnostic:
    """Produce one complete deterministic moving-block diagnostic for each declared candidate."""

    _identifier(comparison_id, "comparison_id")
    if not candidates:
        raise InferenceError("comparison family requires at least one candidate")
    ordered = tuple(sorted(candidates, key=lambda item: item.series_id))
    candidate_ids = tuple(item.series_id for item in ordered)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise InferenceError("comparison family candidates must have unique series ids")
    if baseline.series_id in candidate_ids:
        raise InferenceError("baseline series cannot be included as a candidate")
    diagnostics = tuple(
        _bootstrap_candidate(candidate=candidate, baseline=baseline, config=config)
        for candidate in ordered
    )
    return ComparisonFamilyDiagnostic(
        comparison_id=comparison_id,
        baseline_series_id=baseline.series_id,
        candidate_series_ids=candidate_ids,
        diagnostics=diagnostics,
    )


def robustness_diagnostic(
    *,
    candidate_series_id: str,
    decision_cutoff_at: datetime,
    scenarios: Sequence[RobustnessScenario],
    scenario_series: Sequence[ReturnSeries],
) -> RobustnessDiagnostic:
    """Build a complete, canonically ordered scenario report without selecting a favored outcome."""

    _identifier(candidate_series_id, "candidate_series_id")
    cutoff = _utc(decision_cutoff_at, "decision_cutoff_at")
    ordered_scenarios = tuple(sorted(scenarios, key=lambda item: item.scenario_id))
    ordered_series = tuple(sorted(scenario_series, key=lambda item: item.series_id))
    if len(ordered_scenarios) != len(ordered_series):
        raise InferenceError("robustness scenarios and scenario series must have equal counts")
    if any(series.decision_cutoff_at != cutoff for series in ordered_series):
        raise InferenceError("robustness scenario series must share requested decision cutoff")
    scenario_ids = tuple(item.scenario_id for item in ordered_scenarios)
    series_ids = tuple(item.series_id for item in ordered_series)
    if scenario_ids != series_ids:
        raise InferenceError("scenario ids must exactly match scenario return-series ids")
    outcomes = tuple(
        RobustnessOutcome(
            scenario=scenario,
            return_series=series,
            mean_return=_mean(tuple(item.return_value for item in series.observations)),
        )
        for scenario, series in zip(ordered_scenarios, ordered_series, strict=True)
    )
    return RobustnessDiagnostic(
        candidate_series_id=candidate_series_id,
        decision_cutoff_at=cutoff,
        scenarios=ordered_scenarios,
        outcomes=outcomes,
    )


def _bootstrap_candidate(
    *, candidate: ReturnSeries, baseline: ReturnSeries, config: BlockBootstrapConfig
) -> CandidateBootstrapDiagnostic:
    excess_values = _aligned_excess_values(candidate=candidate, baseline=baseline)
    observation_count = len(excess_values)
    if config.block_length > observation_count:
        raise InferenceError("block_length cannot exceed candidate comparison observation_count")
    observed = _mean(excess_values)
    centered = tuple(value - observed for value in excess_values)
    block_count = math.ceil(observation_count / config.block_length)
    replicates = tuple(
        _bootstrap_replicate(
            replicate_index=index,
            centered_values=centered,
            block_length=config.block_length,
            block_count=block_count,
            seed_sha256=config.seed_sha256,
            candidate_series_id=candidate.series_id,
        )
        for index in range(config.replicate_count)
    )
    exceedance_count = sum(item.centered_sample_mean >= observed for item in replicates)
    return CandidateBootstrapDiagnostic(
        candidate_series_id=candidate.series_id,
        baseline_series_id=baseline.series_id,
        decision_cutoff_at=candidate.decision_cutoff_at,
        config=config,
        observation_count=observation_count,
        observed_mean_excess_return=observed,
        one_sided_exceedance_rate=Decimal(exceedance_count) / Decimal(config.replicate_count),
        replicates=replicates,
    )


def _bootstrap_replicate(
    *,
    replicate_index: int,
    centered_values: tuple[Decimal, ...],
    block_length: int,
    block_count: int,
    seed_sha256: str,
    candidate_series_id: str,
) -> BootstrapReplicate:
    observation_count = len(centered_values)
    starts = tuple(
        _deterministic_start(
            seed_sha256=seed_sha256,
            candidate_series_id=candidate_series_id,
            replicate_index=replicate_index,
            block_index=block_index,
            observation_count=observation_count,
        )
        for block_index in range(block_count)
    )
    sampled = tuple(
        centered_values[(start + offset) % observation_count]
        for start in starts
        for offset in range(block_length)
    )[:observation_count]
    return BootstrapReplicate(
        replicate_index=replicate_index,
        block_start_indices=starts,
        centered_sample_mean=_mean(sampled),
    )


def _deterministic_start(
    *,
    seed_sha256: str,
    candidate_series_id: str,
    replicate_index: int,
    block_index: int,
    observation_count: int,
) -> int:
    material = f"{seed_sha256}:{candidate_series_id}:{replicate_index}:{block_index}".encode()
    return (
        int.from_bytes(hashlib.sha256(material).digest()[:8], byteorder="big") % observation_count
    )


def _aligned_excess_values(
    *, candidate: ReturnSeries, baseline: ReturnSeries
) -> tuple[Decimal, ...]:
    if candidate.decision_cutoff_at != baseline.decision_cutoff_at:
        raise InferenceError("candidate and baseline must share decision_cutoff_at")
    if candidate.observations_per_year != baseline.observations_per_year:
        raise InferenceError("candidate and baseline must share observations_per_year")
    candidate_times = tuple(item.event_at for item in candidate.observations)
    baseline_times = tuple(item.event_at for item in baseline.observations)
    if candidate_times != baseline_times:
        raise InferenceError(
            "candidate and baseline must have identical chronological event timestamps"
        )
    return tuple(
        candidate_item.return_value - baseline_item.return_value
        for candidate_item, baseline_item in zip(
            candidate.observations, baseline.observations, strict=True
        )
    )


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise InferenceError("mean requires at least one value")
    result = sum(values, _ZERO) / Decimal(len(values))
    _finite(result, "mean")
    return result


def _sample_volatility(*, values: Sequence[Decimal], mean: Decimal) -> Decimal:
    if len(values) < 2:
        raise InferenceError("sample volatility requires at least two observations")
    variance = sum(((value - mean) ** 2 for value in values), _ZERO) / Decimal(len(values) - 1)
    with localcontext() as context:
        context.prec = 40
        result = variance.sqrt()
    _finite(result, "sample_volatility", nonnegative=True)
    return result


def _autocorrelation(*, values: Sequence[Decimal], mean: Decimal, lag: int) -> Decimal | None:
    denominator = sum(((value - mean) ** 2 for value in values), _ZERO)
    if denominator == _ZERO:
        return None
    numerator = sum(
        (
            (values[index] - mean) * (values[index - lag] - mean)
            for index in range(lag, len(values))
        ),
        _ZERO,
    )
    value = numerator / denominator
    _finite(value, "autocorrelation")
    return value


def _identifier(value: str, field_name: str) -> None:
    if _IDENTIFIER.fullmatch(value.strip()) is None:
        raise InferenceError(f"{field_name} is invalid")


def _sha256(value: str, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise InferenceError(f"{field_name} must be a lowercase SHA-256 digest")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InferenceError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(
    value: Decimal, field_name: str, *, positive: bool = False, nonnegative: bool = False
) -> None:
    if not value.is_finite():
        raise InferenceError(f"{field_name} must be finite")
    if positive and value <= _ZERO:
        raise InferenceError(f"{field_name} must be positive")
    if nonnegative and value < _ZERO:
        raise InferenceError(f"{field_name} must be nonnegative")
