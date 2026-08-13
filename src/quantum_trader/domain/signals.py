"""Point-in-time forecast signals, bounded volatility scaling, and permanent baselines.

This module deliberately produces research forecasts rather than executable orders.  Every
forecast retains the inputs that were available by its decision cutoff, and every baseline
is represented explicitly so later experiment attempts cannot compare a candidate only to a
weaker benchmark.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from itertools import pairwise

from quantum_trader.domain.research_data import EquityBarRecord

_ZERO = Decimal("0")
_ONE = Decimal("1")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$")
_VERSION = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,99}$")


class SignalDataError(ValueError):
    """Raised when a forecast would be ambiguous, leaky, or unreproducible."""


class SignalFamily(StrEnum):
    """Literature-screened forecast and overlay families implemented in this module."""

    TIME_SERIES_MOMENTUM = "H01.time_series_trend"
    CROSS_SECTIONAL_MOMENTUM = "H02.cross_sectional_momentum"
    VOLATILITY_TARGETING = "H04.volatility_targeting"


class TrendForecastVariant(StrEnum):
    """Time-series-momentum representations declared before an experiment begins."""

    SIGN = "sign"
    CONTINUOUS_RETURN = "continuous_return"


class ForecastAbsenceReason(StrEnum):
    """Fail-closed reason codes for a forecast with no usable value."""

    NO_DATA_AT_CUTOFF = "no_data_at_cutoff"
    INSUFFICIENT_HISTORY = "insufficient_history"
    INSUFFICIENT_ELIGIBLE_MEMBERS = "insufficient_eligible_members"
    SOURCE_FORECAST_ABSENT = "source_forecast_absent"


class ScalingStatus(StrEnum):
    """Whether a volatility transform altered its source forecast."""

    NOT_APPLICABLE = "not_applicable"
    APPLIED = "applied"
    UNSCALED_INSUFFICIENT_HISTORY = "unscaled_insufficient_history"
    UNSCALED_ZERO_VOLATILITY = "unscaled_zero_volatility"
    SOURCE_FORECAST_ABSENT = "source_forecast_absent"


class PermanentBaseline(StrEnum):
    """Mandatory comparison portfolios for every later candidate experiment."""

    EQUAL_WEIGHT = "equal_weight_buy_and_hold"
    TREND_ONLY = "trend_only_unscaled"
    CASH = "cash_zero_exposure"


@dataclass(frozen=True, slots=True)
class ForecastRawInput:
    """One checksum-bound value used by a forecast at its decision time."""

    record_id: str
    field_name: str
    value: Decimal
    available_at: datetime
    content_sha256: str

    def __post_init__(self) -> None:
        _identifier(self.record_id, "record_id")
        if not self.field_name or len(self.field_name) > 100:
            raise SignalDataError("field_name must contain 1 to 100 characters")
        _finite(self.value, "raw input value")
        object.__setattr__(self, "available_at", _utc(self.available_at, "available_at"))
        _sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True, slots=True)
class ForecastSignal:
    """A causal, checksum-bound forecast; it is not an executable target position."""

    instrument_id: str
    decision_cutoff_at: datetime
    signal_family: SignalFamily
    raw_inputs: tuple[ForecastRawInput, ...]
    warm_up_complete: bool
    forecast_value: Decimal | None
    absence_reason: ForecastAbsenceReason | None
    signal_version: str
    universe_id: str | None = None
    universe_sha256: str | None = None
    scaling_status: ScalingStatus = ScalingStatus.NOT_APPLICABLE
    exposure_multiplier: Decimal | None = None

    def __post_init__(self) -> None:
        _identifier(self.instrument_id, "instrument_id")
        cutoff = _utc(self.decision_cutoff_at, "decision_cutoff_at")
        if len({item.record_id for item in self.raw_inputs}) != len(self.raw_inputs):
            raise SignalDataError("raw input record_ids must be unique")
        if any(item.available_at > cutoff for item in self.raw_inputs):
            raise SignalDataError("forecast uses input unavailable at decision_cutoff_at")
        if self.warm_up_complete:
            if not self.raw_inputs:
                raise SignalDataError("a warm forecast requires at least one retained raw input")
            if self.forecast_value is None:
                raise SignalDataError("a warm forecast requires forecast_value")
            _finite(self.forecast_value, "forecast_value")
            if self.absence_reason is not None:
                raise SignalDataError("a warm forecast cannot have an absence_reason")
        elif self.forecast_value is not None or self.absence_reason is None:
            raise SignalDataError(
                "an incomplete forecast requires no value and an explicit absence_reason"
            )
        if _VERSION.fullmatch(self.signal_version) is None:
            raise SignalDataError("signal_version is invalid")
        if (self.universe_id is None) != (self.universe_sha256 is None):
            raise SignalDataError("universe_id and universe_sha256 must be supplied together")
        if self.universe_id is not None and self.universe_sha256 is not None:
            _identifier(self.universe_id, "universe_id")
            _sha256(self.universe_sha256, "universe_sha256")
        if self.exposure_multiplier is not None:
            _finite(self.exposure_multiplier, "exposure_multiplier", nonnegative=True)
        if self.scaling_status is ScalingStatus.NOT_APPLICABLE:
            if self.exposure_multiplier is not None:
                raise SignalDataError("non-scaled forecasts cannot declare an exposure multiplier")
        elif self.scaling_status is ScalingStatus.SOURCE_FORECAST_ABSENT:
            if self.warm_up_complete or self.exposure_multiplier is not None:
                raise SignalDataError("an absent source forecast cannot have a scaling multiplier")
        elif self.exposure_multiplier is None:
            raise SignalDataError("a scaling status requires an explicit exposure multiplier")
        object.__setattr__(self, "instrument_id", self.instrument_id.strip().upper())
        object.__setattr__(self, "decision_cutoff_at", cutoff)


@dataclass(frozen=True, slots=True)
class FrozenUniverse:
    """A provider-versioned, available-by-cutoff universe that cannot silently shrink."""

    universe_id: str
    instrument_ids: tuple[str, ...]
    available_at: datetime
    universe_sha256: str

    def __post_init__(self) -> None:
        _identifier(self.universe_id, "universe_id")
        if len(self.instrument_ids) < 2:
            raise SignalDataError("a frozen universe requires at least two instruments")
        normalized = tuple(
            _normalized_identifier(value, "instrument_id") for value in self.instrument_ids
        )
        if len(set(normalized)) != len(normalized):
            raise SignalDataError("a frozen universe cannot contain duplicate instruments")
        if normalized != tuple(sorted(normalized)):
            raise SignalDataError("frozen universe instruments must use canonical sorted order")
        object.__setattr__(self, "instrument_ids", normalized)
        object.__setattr__(self, "available_at", _utc(self.available_at, "available_at"))
        _sha256(self.universe_sha256, "universe_sha256")


@dataclass(frozen=True, slots=True)
class FamilyAttribution:
    """Declared blend contribution retained even when a component is unavailable."""

    signal_family: SignalFamily
    blend_weight: Decimal
    signal_count: int

    def __post_init__(self) -> None:
        _finite(self.blend_weight, "blend_weight", nonnegative=True)
        if self.signal_count < 1:
            raise SignalDataError("signal_count must be positive")


@dataclass(frozen=True, slots=True)
class SignalPortfolio:
    """An ordered point-in-time collection with non-erasable family attribution."""

    universe: FrozenUniverse
    decision_cutoff_at: datetime
    signals: tuple[ForecastSignal, ...]
    family_attributions: tuple[FamilyAttribution, ...]

    def __post_init__(self) -> None:
        cutoff = _utc(self.decision_cutoff_at, "decision_cutoff_at")
        if self.universe.available_at > cutoff:
            raise SignalDataError("universe was not available at the portfolio decision cutoff")
        if not self.signals:
            raise SignalDataError("a signal portfolio requires at least one signal")
        expected_order = tuple(
            sorted(
                self.signals, key=lambda signal: (signal.signal_family.value, signal.instrument_id)
            )
        )
        if self.signals != expected_order:
            raise SignalDataError("signals must use canonical family-and-instrument order")
        identities = {(signal.signal_family, signal.instrument_id) for signal in self.signals}
        if len(identities) != len(self.signals):
            raise SignalDataError("signal portfolio has duplicate family and instrument entries")
        if any(signal.decision_cutoff_at != cutoff for signal in self.signals):
            raise SignalDataError("all signals must share the portfolio decision cutoff")
        if any(signal.instrument_id not in self.universe.instrument_ids for signal in self.signals):
            raise SignalDataError(
                "signal portfolio contains an instrument outside its frozen universe"
            )
        families = {signal.signal_family for signal in self.signals}
        attribution_families = {item.signal_family for item in self.family_attributions}
        if families != attribution_families:
            raise SignalDataError(
                "family attributions must exactly cover represented signal families"
            )
        if len(attribution_families) != len(self.family_attributions):
            raise SignalDataError("family attributions must be unique")
        count_by_family = {
            family: sum(1 for signal in self.signals if signal.signal_family is family)
            for family in families
        }
        if any(
            item.signal_count != count_by_family[item.signal_family]
            for item in self.family_attributions
        ):
            raise SignalDataError("family attribution signal counts do not reconcile")
        if sum((item.blend_weight for item in self.family_attributions), _ZERO) != _ONE:
            raise SignalDataError("family attribution weights must sum exactly to one")
        object.__setattr__(self, "decision_cutoff_at", cutoff)


@dataclass(frozen=True, slots=True)
class VolatilityScalingConfig:
    """Predeclared risk transform parameters, including both floor and cap."""

    target_annualized_volatility: Decimal
    estimation_window_bars: int
    minimum_observations: int
    max_leverage: Decimal
    annualization_observations: int = 252
    min_leverage: Decimal = _ZERO

    def __post_init__(self) -> None:
        _finite(self.target_annualized_volatility, "target_annualized_volatility", positive=True)
        _finite(self.max_leverage, "max_leverage", positive=True)
        _finite(self.min_leverage, "min_leverage", nonnegative=True)
        if self.min_leverage > self.max_leverage:
            raise SignalDataError("min_leverage cannot exceed max_leverage")
        if self.estimation_window_bars < 2:
            raise SignalDataError("estimation_window_bars must be at least two")
        if not 2 <= self.minimum_observations <= self.estimation_window_bars:
            raise SignalDataError(
                "minimum_observations must be between two and estimation_window_bars"
            )
        if self.annualization_observations < 1:
            raise SignalDataError("annualization_observations must be positive")


@dataclass(frozen=True, slots=True)
class BaselineAllocation:
    """One baseline exposure; values can be signed because this is research-only."""

    instrument_id: str
    target_exposure: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instrument_id", _normalized_identifier(self.instrument_id, "instrument_id")
        )
        _finite(self.target_exposure, "target_exposure")


@dataclass(frozen=True, slots=True)
class BaselinePortfolio:
    """A required comparator retaining its universe, readiness, and source forecast version."""

    baseline: PermanentBaseline
    universe: FrozenUniverse
    decision_cutoff_at: datetime
    allocations: tuple[BaselineAllocation, ...]
    warm_up_complete: bool
    absence_reason: ForecastAbsenceReason | None
    source_signal_versions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        cutoff = _utc(self.decision_cutoff_at, "decision_cutoff_at")
        if self.universe.available_at > cutoff:
            raise SignalDataError("universe was not available at the baseline decision cutoff")
        allocation_ids = tuple(item.instrument_id for item in self.allocations)
        if allocation_ids != self.universe.instrument_ids:
            raise SignalDataError("baseline allocations must match canonical frozen-universe order")
        if self.warm_up_complete and self.absence_reason is not None:
            raise SignalDataError("a ready baseline cannot have an absence_reason")
        if not self.warm_up_complete and self.absence_reason is None:
            raise SignalDataError("an unready baseline requires an absence_reason")
        if any(_VERSION.fullmatch(value) is None for value in self.source_signal_versions):
            raise SignalDataError("source_signal_versions contains an invalid version")
        if self.baseline is not PermanentBaseline.TREND_ONLY and self.source_signal_versions:
            raise SignalDataError("only trend-only baselines can retain source signal versions")
        if self.baseline is PermanentBaseline.EQUAL_WEIGHT:
            expected = _ONE / Decimal(len(self.universe.instrument_ids))
            if any(item.target_exposure != expected for item in self.allocations):
                raise SignalDataError("equal-weight baseline allocations are inconsistent")
        if self.baseline is PermanentBaseline.CASH and any(
            item.target_exposure != _ZERO for item in self.allocations
        ):
            raise SignalDataError("cash baseline allocations must be zero")
        object.__setattr__(self, "decision_cutoff_at", cutoff)


def time_series_momentum_forecast(
    *,
    instrument_id: str,
    bars: Sequence[EquityBarRecord],
    decision_cutoff_at: datetime,
    lookback_bars: int,
    variant: TrendForecastVariant = TrendForecastVariant.SIGN,
) -> ForecastSignal:
    """Return a no-lookahead trailing-return forecast or an explicit warm-up absence."""

    if lookback_bars < 2:
        raise SignalDataError("lookback_bars must be at least two")
    normalized = _normalized_identifier(instrument_id, "instrument_id")
    cutoff = _utc(decision_cutoff_at, "decision_cutoff_at")
    eligible = _eligible_bars(bars=bars, instrument_id=normalized, decision_cutoff_at=cutoff)
    signal_version = f"tsmom-v1-{variant.value}-lb{lookback_bars}"
    if not eligible:
        return _absent_signal(
            instrument_id=normalized,
            decision_cutoff_at=cutoff,
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            raw_inputs=(),
            absence_reason=ForecastAbsenceReason.NO_DATA_AT_CUTOFF,
            signal_version=signal_version,
        )
    if len(eligible) < lookback_bars + 1:
        return _absent_signal(
            instrument_id=normalized,
            decision_cutoff_at=cutoff,
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            raw_inputs=_raw_inputs(eligible),
            absence_reason=ForecastAbsenceReason.INSUFFICIENT_HISTORY,
            signal_version=signal_version,
        )
    window = eligible[-(lookback_bars + 1) :]
    trailing_return = _trailing_return(window)
    forecast = _sign(trailing_return) if variant is TrendForecastVariant.SIGN else trailing_return
    return ForecastSignal(
        instrument_id=normalized,
        decision_cutoff_at=cutoff,
        signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
        raw_inputs=_raw_inputs(window),
        warm_up_complete=True,
        forecast_value=forecast,
        absence_reason=None,
        signal_version=signal_version,
    )


def cross_sectional_momentum_forecasts(
    *,
    universe: FrozenUniverse,
    bars_by_instrument: Mapping[str, Sequence[EquityBarRecord]],
    decision_cutoff_at: datetime,
    lookback_bars: int,
    minimum_eligible_members: int,
) -> tuple[ForecastSignal, ...]:
    """Rank a complete frozen universe while retaining every unavailable member explicitly."""

    if lookback_bars < 2:
        raise SignalDataError("lookback_bars must be at least two")
    if not 2 <= minimum_eligible_members <= len(universe.instrument_ids):
        raise SignalDataError("minimum_eligible_members is outside the frozen-universe bounds")
    cutoff = _utc(decision_cutoff_at, "decision_cutoff_at")
    if universe.available_at > cutoff:
        raise SignalDataError("frozen universe was unavailable at decision cutoff")
    normalized_mapping = {
        _normalized_identifier(instrument_id, "bars_by_instrument key"): value
        for instrument_id, value in bars_by_instrument.items()
    }
    if set(normalized_mapping) != set(universe.instrument_ids):
        raise SignalDataError(
            "cross-sectional inputs must contain every and only frozen-universe member"
        )
    version = f"xsmom-v1-lb{lookback_bars}"
    windows: dict[str, tuple[EquityBarRecord, ...]] = {}
    preliminary_absences: dict[str, tuple[ForecastAbsenceReason, tuple[ForecastRawInput, ...]]] = {}
    returns: dict[str, Decimal] = {}
    for instrument_id in universe.instrument_ids:
        eligible = _eligible_bars(
            bars=normalized_mapping[instrument_id],
            instrument_id=instrument_id,
            decision_cutoff_at=cutoff,
        )
        if not eligible:
            preliminary_absences[instrument_id] = (
                ForecastAbsenceReason.NO_DATA_AT_CUTOFF,
                (),
            )
        elif len(eligible) < lookback_bars + 1:
            preliminary_absences[instrument_id] = (
                ForecastAbsenceReason.INSUFFICIENT_HISTORY,
                _raw_inputs(eligible),
            )
        else:
            window = eligible[-(lookback_bars + 1) :]
            windows[instrument_id] = window
            returns[instrument_id] = _trailing_return(window)
    if len(returns) < minimum_eligible_members:
        return tuple(
            _absent_signal(
                instrument_id=instrument_id,
                decision_cutoff_at=cutoff,
                signal_family=SignalFamily.CROSS_SECTIONAL_MOMENTUM,
                raw_inputs=(
                    _raw_inputs(windows[instrument_id])
                    if instrument_id in windows
                    else preliminary_absences[instrument_id][1]
                ),
                absence_reason=ForecastAbsenceReason.INSUFFICIENT_ELIGIBLE_MEMBERS,
                signal_version=version,
                universe=universe,
            )
            for instrument_id in universe.instrument_ids
        )
    ranks = _average_descending_ranks(returns)
    denominator = Decimal(len(returns) - 1)
    signals: list[ForecastSignal] = []
    for instrument_id in universe.instrument_ids:
        if instrument_id in returns:
            score = _ONE - (Decimal("2") * (ranks[instrument_id] - _ONE) / denominator)
            signals.append(
                ForecastSignal(
                    instrument_id=instrument_id,
                    decision_cutoff_at=cutoff,
                    signal_family=SignalFamily.CROSS_SECTIONAL_MOMENTUM,
                    raw_inputs=_raw_inputs(windows[instrument_id]),
                    warm_up_complete=True,
                    forecast_value=score,
                    absence_reason=None,
                    signal_version=version,
                    universe_id=universe.universe_id,
                    universe_sha256=universe.universe_sha256,
                )
            )
        else:
            reason, raw_inputs = preliminary_absences[instrument_id]
            signals.append(
                _absent_signal(
                    instrument_id=instrument_id,
                    decision_cutoff_at=cutoff,
                    signal_family=SignalFamily.CROSS_SECTIONAL_MOMENTUM,
                    raw_inputs=raw_inputs,
                    absence_reason=reason,
                    signal_version=version,
                    universe=universe,
                )
            )
    return tuple(signals)


def volatility_scale_forecast(
    *,
    source_signal: ForecastSignal,
    bars: Sequence[EquityBarRecord],
    config: VolatilityScalingConfig,
) -> ForecastSignal:
    """Apply a predeclared bounded risk multiplier without using future returns."""

    cutoff = source_signal.decision_cutoff_at
    eligible = _eligible_bars(
        bars=bars,
        instrument_id=source_signal.instrument_id,
        decision_cutoff_at=cutoff,
    )
    merged_inputs = _merge_raw_inputs(source_signal.raw_inputs, _raw_inputs(eligible))
    version = f"{source_signal.signal_version}.voltarget-v1"
    if not source_signal.warm_up_complete:
        return ForecastSignal(
            instrument_id=source_signal.instrument_id,
            decision_cutoff_at=cutoff,
            signal_family=SignalFamily.VOLATILITY_TARGETING,
            raw_inputs=merged_inputs,
            warm_up_complete=False,
            forecast_value=None,
            absence_reason=ForecastAbsenceReason.SOURCE_FORECAST_ABSENT,
            signal_version=version,
            universe_id=source_signal.universe_id,
            universe_sha256=source_signal.universe_sha256,
            scaling_status=ScalingStatus.SOURCE_FORECAST_ABSENT,
        )
    source_forecast = source_signal.forecast_value
    if source_forecast is None:
        raise SignalDataError("a warm source signal must retain a forecast value")
    if len(eligible) < config.minimum_observations + 1:
        return _unscaled_forecast(
            source_signal=source_signal,
            raw_inputs=merged_inputs,
            signal_version=version,
            scaling_status=ScalingStatus.UNSCALED_INSUFFICIENT_HISTORY,
        )
    return_bars = eligible[-(config.estimation_window_bars + 1) :]
    returns = tuple(
        _price(current) / _price(previous) - _ONE for previous, current in pairwise(return_bars)
    )
    if len(returns) < config.minimum_observations:
        return _unscaled_forecast(
            source_signal=source_signal,
            raw_inputs=_merge_raw_inputs(source_signal.raw_inputs, _raw_inputs(return_bars)),
            signal_version=version,
            scaling_status=ScalingStatus.UNSCALED_INSUFFICIENT_HISTORY,
        )
    realized_volatility = _annualized_sample_volatility(
        returns=returns,
        annualization_observations=config.annualization_observations,
    )
    if realized_volatility == _ZERO:
        return _unscaled_forecast(
            source_signal=source_signal,
            raw_inputs=_merge_raw_inputs(source_signal.raw_inputs, _raw_inputs(return_bars)),
            signal_version=version,
            scaling_status=ScalingStatus.UNSCALED_ZERO_VOLATILITY,
        )
    multiplier = config.target_annualized_volatility / realized_volatility
    multiplier = min(config.max_leverage, max(config.min_leverage, multiplier))
    forecast_value = source_forecast * multiplier
    _finite(forecast_value, "scaled forecast_value")
    return ForecastSignal(
        instrument_id=source_signal.instrument_id,
        decision_cutoff_at=cutoff,
        signal_family=SignalFamily.VOLATILITY_TARGETING,
        raw_inputs=_merge_raw_inputs(source_signal.raw_inputs, _raw_inputs(return_bars)),
        warm_up_complete=True,
        forecast_value=forecast_value,
        absence_reason=None,
        signal_version=version,
        universe_id=source_signal.universe_id,
        universe_sha256=source_signal.universe_sha256,
        scaling_status=ScalingStatus.APPLIED,
        exposure_multiplier=multiplier,
    )


def signal_portfolio(
    *,
    universe: FrozenUniverse,
    decision_cutoff_at: datetime,
    signals: Sequence[ForecastSignal],
    family_blend_weights: Mapping[SignalFamily, Decimal],
) -> SignalPortfolio:
    """Create a canonically ordered forecast portfolio with explicit family attribution."""

    cutoff = _utc(decision_cutoff_at, "decision_cutoff_at")
    ordered_signals = tuple(
        sorted(signals, key=lambda signal: (signal.signal_family.value, signal.instrument_id))
    )
    families = {signal.signal_family for signal in ordered_signals}
    if set(family_blend_weights) != families:
        raise SignalDataError("family_blend_weights must exactly cover represented signal families")
    attributions = tuple(
        FamilyAttribution(
            signal_family=family,
            blend_weight=family_blend_weights[family],
            signal_count=sum(1 for signal in ordered_signals if signal.signal_family is family),
        )
        for family in sorted(families, key=lambda family: family.value)
    )
    return SignalPortfolio(
        universe=universe,
        decision_cutoff_at=cutoff,
        signals=ordered_signals,
        family_attributions=attributions,
    )


def equal_weight_baseline(
    *, universe: FrozenUniverse, decision_cutoff_at: datetime
) -> BaselinePortfolio:
    """Return the same-universe equal-weight buy-and-hold comparator."""

    cutoff = _utc(decision_cutoff_at, "decision_cutoff_at")
    weight = _ONE / Decimal(len(universe.instrument_ids))
    return BaselinePortfolio(
        baseline=PermanentBaseline.EQUAL_WEIGHT,
        universe=universe,
        decision_cutoff_at=cutoff,
        allocations=tuple(
            BaselineAllocation(instrument_id=instrument_id, target_exposure=weight)
            for instrument_id in universe.instrument_ids
        ),
        warm_up_complete=True,
        absence_reason=None,
    )


def cash_baseline(*, universe: FrozenUniverse, decision_cutoff_at: datetime) -> BaselinePortfolio:
    """Return the permanent zero-exposure comparator for the declared universe."""

    cutoff = _utc(decision_cutoff_at, "decision_cutoff_at")
    return BaselinePortfolio(
        baseline=PermanentBaseline.CASH,
        universe=universe,
        decision_cutoff_at=cutoff,
        allocations=tuple(
            BaselineAllocation(instrument_id=instrument_id, target_exposure=_ZERO)
            for instrument_id in universe.instrument_ids
        ),
        warm_up_complete=True,
        absence_reason=None,
    )


def trend_only_baseline(
    *,
    universe: FrozenUniverse,
    decision_cutoff_at: datetime,
    trend_signals: Sequence[ForecastSignal],
) -> BaselinePortfolio:
    """Return an unscaled, equal-gross-weight time-series-momentum comparator."""

    cutoff = _utc(decision_cutoff_at, "decision_cutoff_at")
    ordered = tuple(sorted(trend_signals, key=lambda signal: signal.instrument_id))
    if tuple(signal.instrument_id for signal in ordered) != universe.instrument_ids:
        raise SignalDataError("trend baseline signals must exactly match frozen-universe members")
    if any(signal.signal_family is not SignalFamily.TIME_SERIES_MOMENTUM for signal in ordered):
        raise SignalDataError("trend baseline accepts only H01 time-series momentum signals")
    if any(signal.decision_cutoff_at != cutoff for signal in ordered):
        raise SignalDataError("trend baseline signals must share the requested decision cutoff")
    versions = tuple(sorted({signal.signal_version for signal in ordered}))
    if not all(signal.warm_up_complete for signal in ordered):
        return BaselinePortfolio(
            baseline=PermanentBaseline.TREND_ONLY,
            universe=universe,
            decision_cutoff_at=cutoff,
            allocations=tuple(
                BaselineAllocation(instrument_id=instrument_id, target_exposure=_ZERO)
                for instrument_id in universe.instrument_ids
            ),
            warm_up_complete=False,
            absence_reason=ForecastAbsenceReason.SOURCE_FORECAST_ABSENT,
            source_signal_versions=versions,
        )
    divisor = Decimal(len(universe.instrument_ids))
    return BaselinePortfolio(
        baseline=PermanentBaseline.TREND_ONLY,
        universe=universe,
        decision_cutoff_at=cutoff,
        allocations=tuple(
            BaselineAllocation(
                instrument_id=signal.instrument_id,
                target_exposure=_sign(signal.forecast_value) / divisor,
            )
            for signal in ordered
        ),
        warm_up_complete=True,
        absence_reason=None,
        source_signal_versions=versions,
    )


def permanent_baselines(
    *,
    universe: FrozenUniverse,
    decision_cutoff_at: datetime,
    trend_signals: Sequence[ForecastSignal],
) -> tuple[BaselinePortfolio, BaselinePortfolio, BaselinePortfolio]:
    """Build the full mandatory baseline set in stable public order."""

    return (
        equal_weight_baseline(universe=universe, decision_cutoff_at=decision_cutoff_at),
        trend_only_baseline(
            universe=universe,
            decision_cutoff_at=decision_cutoff_at,
            trend_signals=trend_signals,
        ),
        cash_baseline(universe=universe, decision_cutoff_at=decision_cutoff_at),
    )


def _absent_signal(
    *,
    instrument_id: str,
    decision_cutoff_at: datetime,
    signal_family: SignalFamily,
    raw_inputs: tuple[ForecastRawInput, ...],
    absence_reason: ForecastAbsenceReason,
    signal_version: str,
    universe: FrozenUniverse | None = None,
) -> ForecastSignal:
    return ForecastSignal(
        instrument_id=instrument_id,
        decision_cutoff_at=decision_cutoff_at,
        signal_family=signal_family,
        raw_inputs=raw_inputs,
        warm_up_complete=False,
        forecast_value=None,
        absence_reason=absence_reason,
        signal_version=signal_version,
        universe_id=None if universe is None else universe.universe_id,
        universe_sha256=None if universe is None else universe.universe_sha256,
    )


def _unscaled_forecast(
    *,
    source_signal: ForecastSignal,
    raw_inputs: tuple[ForecastRawInput, ...],
    signal_version: str,
    scaling_status: ScalingStatus,
) -> ForecastSignal:
    if source_signal.forecast_value is None:
        raise SignalDataError("an unscaled transform requires a source forecast value")
    return ForecastSignal(
        instrument_id=source_signal.instrument_id,
        decision_cutoff_at=source_signal.decision_cutoff_at,
        signal_family=SignalFamily.VOLATILITY_TARGETING,
        raw_inputs=raw_inputs,
        warm_up_complete=True,
        forecast_value=source_signal.forecast_value,
        absence_reason=None,
        signal_version=signal_version,
        universe_id=source_signal.universe_id,
        universe_sha256=source_signal.universe_sha256,
        scaling_status=scaling_status,
        exposure_multiplier=_ONE,
    )


def _eligible_bars(
    *, bars: Sequence[EquityBarRecord], instrument_id: str, decision_cutoff_at: datetime
) -> tuple[EquityBarRecord, ...]:
    selected: list[EquityBarRecord] = []
    for bar in bars:
        if bar.security.instrument_id != instrument_id:
            raise SignalDataError("bar security does not match signal instrument")
        if bar.availability.available_at <= decision_cutoff_at:
            selected.append(bar)
    ordered = tuple(
        sorted(selected, key=lambda bar: (bar.availability.event_at, bar.identity.record_id))
    )
    event_times = tuple(bar.availability.event_at for bar in ordered)
    if len(set(event_times)) != len(event_times):
        raise SignalDataError("eligible bars contain duplicate event timestamps")
    return ordered


def _raw_inputs(bars: Sequence[EquityBarRecord]) -> tuple[ForecastRawInput, ...]:
    return tuple(
        ForecastRawInput(
            record_id=bar.identity.record_id,
            field_name="adjusted_close" if bar.adjusted_close is not None else "close",
            value=_price(bar),
            available_at=bar.availability.available_at,
            content_sha256=bar.provenance.raw_sha256,
        )
        for bar in bars
    )


def _merge_raw_inputs(
    *groups: Sequence[ForecastRawInput],
) -> tuple[ForecastRawInput, ...]:
    by_record_id: dict[str, ForecastRawInput] = {}
    for item in (input_item for group in groups for input_item in group):
        existing = by_record_id.get(item.record_id)
        if existing is not None and existing != item:
            raise SignalDataError("the same raw record_id has inconsistent retained input values")
        by_record_id[item.record_id] = item
    return tuple(
        sorted(by_record_id.values(), key=lambda item: (item.available_at, item.record_id))
    )


def _trailing_return(window: Sequence[EquityBarRecord]) -> Decimal:
    if len(window) < 2:
        raise SignalDataError("a trailing return requires at least two bars")
    value = _price(window[-1]) / _price(window[0]) - _ONE
    _finite(value, "trailing_return")
    return value


def _annualized_sample_volatility(
    *, returns: Sequence[Decimal], annualization_observations: int
) -> Decimal:
    if len(returns) < 2:
        raise SignalDataError("sample volatility requires at least two observations")
    mean = sum(returns, _ZERO) / Decimal(len(returns))
    variance = sum(((value - mean) ** 2 for value in returns), _ZERO) / Decimal(len(returns) - 1)
    with localcontext() as context:
        context.prec = 40
        result = variance.sqrt() * Decimal(annualization_observations).sqrt()
    _finite(result, "realized_volatility", nonnegative=True)
    return result


def _average_descending_ranks(values: Mapping[str, Decimal]) -> dict[str, Decimal]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    ranks: dict[str, Decimal] = {}
    index = 0
    while index < len(ordered):
        value = ordered[index][1]
        stop = index + 1
        while stop < len(ordered) and ordered[stop][1] == value:
            stop += 1
        average_rank = (Decimal(index + 1) + Decimal(stop)) / Decimal("2")
        for instrument_id, _ in ordered[index:stop]:
            ranks[instrument_id] = average_rank
        index = stop
    return ranks


def _price(bar: EquityBarRecord) -> Decimal:
    return bar.adjusted_close if bar.adjusted_close is not None else bar.close


def _sign(value: Decimal | None) -> Decimal:
    if value is None:
        raise SignalDataError("a sign requires a non-null forecast value")
    if value > _ZERO:
        return _ONE
    if value < _ZERO:
        return Decimal("-1")
    return _ZERO


def _normalized_identifier(value: str, field_name: str) -> str:
    _identifier(value, field_name)
    return value.strip().upper()


def _identifier(value: str, field_name: str) -> None:
    if _IDENTIFIER.fullmatch(value.strip()) is None:
        raise SignalDataError(f"{field_name} is invalid")


def _sha256(value: str, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise SignalDataError(f"{field_name} must be a lowercase SHA-256 digest")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SignalDataError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(
    value: Decimal, field_name: str, *, positive: bool = False, nonnegative: bool = False
) -> Decimal:
    if not value.is_finite():
        raise SignalDataError(f"{field_name} must be finite")
    if positive and value <= _ZERO:
        raise SignalDataError(f"{field_name} must be positive")
    if nonnegative and value < _ZERO:
        raise SignalDataError(f"{field_name} must be nonnegative")
    return value
