"""Constraint-first, factor-aware portfolio construction for research only.

This module maps an immutable :class:`SignalPortfolio` to auditable target exposures.
It deliberately avoids covariance optimization, fitted blend weights, order creation, and
execution.  Every target preserves forecast-family contributions and every factor input is
an explicit point-in-time receipt.  Missing or stale factor data produces an all-cash,
unready result rather than an implicit zero exposure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from quantum_trader.domain.signals import SignalFamily, SignalPortfolio

_ZERO = Decimal("0")
_ONE = Decimal("1")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$")
_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PortfolioConstructionError(ValueError):
    """Raised when a constrained allocation cannot be causal or internally reconciled."""


class ConstructionAbsenceReason(StrEnum):
    """Explicit reason a construction result is intentionally all-cash and unready."""

    INCOMPLETE_FORECASTS = "incomplete_forecasts"
    MISSING_FACTOR_LOADING = "missing_factor_loading"
    STALE_FACTOR_LOADING = "stale_factor_loading"


class AllocationConstraint(StrEnum):
    """Named transformations visible in output attribution rather than hidden in an optimizer."""

    FAMILY_GROSS_CAP = "family_gross_cap"
    INSTRUMENT_CAP = "instrument_cap"
    GROSS_CAP = "gross_cap"
    NET_CAP = "net_cap"
    FACTOR_CAP = "factor_cap"


@dataclass(frozen=True, slots=True)
class FamilyGrossLimit:
    """Maximum absolute exposure contributed by one forecast family before later caps."""

    signal_family: SignalFamily
    max_abs_gross_exposure: Decimal

    def __post_init__(self) -> None:
        _finite(self.max_abs_gross_exposure, "max_abs_gross_exposure", positive=True)


@dataclass(frozen=True, slots=True)
class FactorExposureLimit:
    """Maximum absolute linear exposure to one named factor at the decision cutoff."""

    factor_id: str
    max_abs_exposure: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_id", _normalized_identifier(self.factor_id, "factor_id"))
        _finite(self.max_abs_exposure, "max_abs_exposure", positive=True)


@dataclass(frozen=True, slots=True)
class FactorLoading:
    """One point-in-time instrument loading with a retained source receipt."""

    instrument_id: str
    factor_id: str
    as_of_at: datetime
    available_at: datetime
    value: Decimal
    source_record_id: str
    source_sha256: str
    model_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instrument_id", _normalized_identifier(self.instrument_id, "instrument_id")
        )
        object.__setattr__(self, "factor_id", _normalized_identifier(self.factor_id, "factor_id"))
        as_of_at = _utc(self.as_of_at, "as_of_at")
        available_at = _utc(self.available_at, "available_at")
        if as_of_at > available_at:
            raise PortfolioConstructionError("factor loading as_of_at cannot follow available_at")
        _finite(self.value, "factor loading value")
        _identifier(self.source_record_id, "source_record_id")
        _sha256(self.source_sha256, "source_sha256")
        if _VERSION.fullmatch(self.model_version) is None:
            raise PortfolioConstructionError("factor loading model_version is invalid")
        object.__setattr__(self, "as_of_at", as_of_at)
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True, slots=True)
class AllocationConfig:
    """Predeclared family, instrument, gross, net, and factor caps for one construction run."""

    config_version: str
    max_abs_instrument_exposure: Decimal
    max_gross_exposure: Decimal
    max_abs_net_exposure: Decimal
    factor_max_age: timedelta
    family_gross_limits: tuple[FamilyGrossLimit, ...] = ()
    factor_exposure_limits: tuple[FactorExposureLimit, ...] = ()

    def __post_init__(self) -> None:
        if _VERSION.fullmatch(self.config_version) is None:
            raise PortfolioConstructionError("allocation config_version is invalid")
        _finite(self.max_abs_instrument_exposure, "max_abs_instrument_exposure", positive=True)
        _finite(self.max_gross_exposure, "max_gross_exposure", positive=True)
        _finite(self.max_abs_net_exposure, "max_abs_net_exposure", nonnegative=True)
        if self.max_abs_instrument_exposure > self.max_gross_exposure:
            raise PortfolioConstructionError(
                "max_abs_instrument_exposure cannot exceed max_gross_exposure"
            )
        if self.max_abs_net_exposure > self.max_gross_exposure:
            raise PortfolioConstructionError(
                "max_abs_net_exposure cannot exceed max_gross_exposure"
            )
        if self.factor_max_age < timedelta(0):
            raise PortfolioConstructionError("factor_max_age must be nonnegative")
        family_ids = tuple(item.signal_family for item in self.family_gross_limits)
        if len(set(family_ids)) != len(family_ids):
            raise PortfolioConstructionError("family_gross_limits must use unique signal families")
        if family_ids != tuple(sorted(family_ids, key=lambda family: family.value)):
            raise PortfolioConstructionError("family_gross_limits must use canonical family order")
        factor_ids = tuple(item.factor_id for item in self.factor_exposure_limits)
        if len(set(factor_ids)) != len(factor_ids):
            raise PortfolioConstructionError("factor_exposure_limits must use unique factor ids")
        if factor_ids != tuple(sorted(factor_ids)):
            raise PortfolioConstructionError(
                "factor_exposure_limits must use canonical factor order"
            )


@dataclass(frozen=True, slots=True)
class ForecastContribution:
    """One retained family contribution to an instrument target before cross-family aggregation."""

    signal_family: SignalFamily
    forecast_value: Decimal
    blend_weight: Decimal
    provisional_contribution: Decimal
    constrained_contribution: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "forecast_value",
            "blend_weight",
            "provisional_contribution",
            "constrained_contribution",
        ):
            _finite(getattr(self, field_name), field_name)
        if self.blend_weight < _ZERO:
            raise PortfolioConstructionError("blend_weight must be nonnegative")
        if self.provisional_contribution != self.forecast_value * self.blend_weight:
            raise PortfolioConstructionError(
                "provisional contribution does not reconcile to forecast"
            )


@dataclass(frozen=True, slots=True)
class TargetAllocation:
    """One canonical instrument target with all pre- and post-constraint attribution retained."""

    instrument_id: str
    forecast_contributions: tuple[ForecastContribution, ...]
    provisional_exposure: Decimal
    target_exposure: Decimal
    local_constraints: tuple[AllocationConstraint, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instrument_id", _normalized_identifier(self.instrument_id, "instrument_id")
        )
        if not self.forecast_contributions:
            raise PortfolioConstructionError("target allocation requires forecast contributions")
        families = tuple(item.signal_family for item in self.forecast_contributions)
        if len(set(families)) != len(families):
            raise PortfolioConstructionError(
                "target allocation family contributions must be unique"
            )
        if families != tuple(sorted(families, key=lambda family: family.value)):
            raise PortfolioConstructionError(
                "target allocation contributions must use canonical order"
            )
        _finite(self.provisional_exposure, "provisional_exposure")
        _finite(self.target_exposure, "target_exposure")
        if self.provisional_exposure != sum(
            (item.provisional_contribution for item in self.forecast_contributions), _ZERO
        ):
            raise PortfolioConstructionError(
                "target allocation provisional exposure does not reconcile"
            )
        if len(set(self.local_constraints)) != len(self.local_constraints):
            raise PortfolioConstructionError("target allocation local constraints must be unique")


@dataclass(frozen=True, slots=True)
class PortfolioFactorExposure:
    """Pre- and post-cap linear factor exposure with all point-in-time loading receipts retained."""

    factor_id: str
    provisional_exposure: Decimal
    target_exposure: Decimal
    max_abs_exposure: Decimal
    loadings: tuple[FactorLoading, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_id", _normalized_identifier(self.factor_id, "factor_id"))
        for field_name in ("provisional_exposure", "target_exposure", "max_abs_exposure"):
            _finite(getattr(self, field_name), field_name)
        if self.max_abs_exposure <= _ZERO:
            raise PortfolioConstructionError("factor max_abs_exposure must be positive")
        loading_ids = tuple(item.instrument_id for item in self.loadings)
        if loading_ids != tuple(sorted(loading_ids)):
            raise PortfolioConstructionError("factor loadings must use canonical instrument order")
        if len(set(loading_ids)) != len(loading_ids):
            raise PortfolioConstructionError("factor loadings must be unique per instrument")


@dataclass(frozen=True, slots=True)
class ConstructedPortfolio:
    """Constraint-first portfolio result; absent results are all-cash by construction."""

    universe_id: str
    universe_sha256: str
    decision_cutoff_at: datetime
    config_version: str
    allocations: tuple[TargetAllocation, ...]
    gross_exposure: Decimal
    net_exposure: Decimal
    cash_residual: Decimal
    factor_exposures: tuple[PortfolioFactorExposure, ...]
    binding_constraints: tuple[AllocationConstraint, ...]
    ready: bool
    absence_reason: ConstructionAbsenceReason | None
    missing_or_stale_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.universe_id, "universe_id")
        _sha256(self.universe_sha256, "universe_sha256")
        object.__setattr__(
            self, "decision_cutoff_at", _utc(self.decision_cutoff_at, "decision_cutoff_at")
        )
        if _VERSION.fullmatch(self.config_version) is None:
            raise PortfolioConstructionError("constructed portfolio config_version is invalid")
        identifiers = tuple(item.instrument_id for item in self.allocations)
        if identifiers != tuple(sorted(identifiers)):
            raise PortfolioConstructionError(
                "target allocations must use canonical instrument order"
            )
        if len(set(identifiers)) != len(identifiers):
            raise PortfolioConstructionError("target allocations must use unique instruments")
        for field_name in ("gross_exposure", "net_exposure", "cash_residual"):
            _finite(getattr(self, field_name), field_name)
        calculated_gross = sum((abs(item.target_exposure) for item in self.allocations), _ZERO)
        calculated_net = sum((item.target_exposure for item in self.allocations), _ZERO)
        if self.gross_exposure != calculated_gross or self.net_exposure != calculated_net:
            raise PortfolioConstructionError("portfolio exposure totals do not reconcile")
        if self.cash_residual != _ONE - self.gross_exposure:
            raise PortfolioConstructionError(
                "cash_residual must reconcile to one minus gross exposure"
            )
        factor_ids = tuple(item.factor_id for item in self.factor_exposures)
        if factor_ids != tuple(sorted(factor_ids)):
            raise PortfolioConstructionError("factor exposures must use canonical factor order")
        if len(set(factor_ids)) != len(factor_ids):
            raise PortfolioConstructionError("factor exposures must be unique")
        if len(set(self.binding_constraints)) != len(self.binding_constraints):
            raise PortfolioConstructionError("binding_constraints must be unique")
        if self.ready:
            if self.absence_reason is not None or self.missing_or_stale_inputs:
                raise PortfolioConstructionError("ready portfolio cannot retain absence metadata")
        else:
            if self.absence_reason is None:
                raise PortfolioConstructionError("unready portfolio requires an absence_reason")
            if any(item.target_exposure != _ZERO for item in self.allocations):
                raise PortfolioConstructionError("unready portfolio must remain all cash")
            if self.factor_exposures:
                raise PortfolioConstructionError("unready portfolio cannot report factor exposures")
            if not self.missing_or_stale_inputs:
                raise PortfolioConstructionError(
                    "unready portfolio requires missing-or-stale input ids"
                )


def construct_factor_aware_portfolio(
    *,
    signal_portfolio: SignalPortfolio,
    config: AllocationConfig,
    factor_loadings: tuple[FactorLoading, ...],
) -> ConstructedPortfolio:
    """Construct a bounded target portfolio or return an explicit all-cash absence outcome."""

    if any(not signal.warm_up_complete for signal in signal_portfolio.signals):
        missing = tuple(
            f"{signal.signal_family.value}:{signal.instrument_id}:{signal.absence_reason.value}"
            for signal in signal_portfolio.signals
            if not signal.warm_up_complete and signal.absence_reason is not None
        )
        return _unready_portfolio(
            signal_portfolio=signal_portfolio,
            config=config,
            reason=ConstructionAbsenceReason.INCOMPLETE_FORECASTS,
            missing_or_stale_inputs=missing,
        )
    weights = {
        item.signal_family: item.blend_weight for item in signal_portfolio.family_attributions
    }
    families = tuple(sorted(weights, key=lambda family: family.value))
    _validate_family_limits(config=config, signal_families=families)
    contributions = _family_contributions(signal_portfolio=signal_portfolio, weights=weights)
    constrained_contributions, family_constraint_bound = _apply_family_caps(
        contributions=contributions,
        config=config,
    )
    provisional_targets = {
        instrument_id: sum(values.values(), _ZERO)
        for instrument_id, values in contributions.items()
    }
    family_capped_targets = {
        instrument_id: sum(values.values(), _ZERO)
        for instrument_id, values in constrained_contributions.items()
    }
    capped_targets, local_constraints = _apply_instrument_cap(
        targets=family_capped_targets,
        max_abs=config.max_abs_instrument_exposure,
    )
    target_values, portfolio_constraints = _apply_portfolio_caps(
        targets=capped_targets,
        config=config,
    )
    required_factor_ids = tuple(item.factor_id for item in config.factor_exposure_limits)
    if not required_factor_ids:
        return _ready_portfolio(
            signal_portfolio=signal_portfolio,
            config=config,
            contributions=contributions,
            constrained_contributions=constrained_contributions,
            provisional_targets=provisional_targets,
            target_values=target_values,
            local_constraints=_with_family_constraints(
                local_constraints=local_constraints, family_constraint_bound=family_constraint_bound
            ),
            factor_exposures=(),
            binding_constraints=portfolio_constraints,
        )
    availability = _factor_availability(
        signal_portfolio=signal_portfolio,
        config=config,
        factor_loadings=factor_loadings,
        required_factor_ids=required_factor_ids,
    )
    if availability.reason is not None:
        return _unready_portfolio(
            signal_portfolio=signal_portfolio,
            config=config,
            reason=availability.reason,
            missing_or_stale_inputs=availability.invalid_identifiers,
        )
    factor_loadings_by_id = availability.loadings_by_factor
    preliminary_factor_exposures = _factor_exposures(
        targets=provisional_targets,
        limits=config.factor_exposure_limits,
        loadings_by_factor=factor_loadings_by_id,
    )
    target_factor_exposures = _factor_exposures(
        targets=target_values,
        limits=config.factor_exposure_limits,
        loadings_by_factor=factor_loadings_by_id,
    )
    factor_scale = _required_factor_scale_with_limits(
        factor_exposures=target_factor_exposures,
        limits=config.factor_exposure_limits,
    )
    if factor_scale < _ONE:
        target_values = _scale_targets(target_values, factor_scale)
        target_factor_exposures = _factor_exposures(
            targets=target_values,
            limits=config.factor_exposure_limits,
            loadings_by_factor=factor_loadings_by_id,
        )
        portfolio_constraints = _append_constraint(
            portfolio_constraints, AllocationConstraint.FACTOR_CAP
        )
    factor_exposures = tuple(
        PortfolioFactorExposure(
            factor_id=limit.factor_id,
            provisional_exposure=preliminary_factor_exposures[limit.factor_id],
            target_exposure=target_factor_exposures[limit.factor_id],
            max_abs_exposure=limit.max_abs_exposure,
            loadings=factor_loadings_by_id[limit.factor_id],
        )
        for limit in config.factor_exposure_limits
    )
    return _ready_portfolio(
        signal_portfolio=signal_portfolio,
        config=config,
        contributions=contributions,
        constrained_contributions=constrained_contributions,
        provisional_targets=provisional_targets,
        target_values=target_values,
        local_constraints=_with_family_constraints(
            local_constraints=local_constraints, family_constraint_bound=family_constraint_bound
        ),
        factor_exposures=factor_exposures,
        binding_constraints=portfolio_constraints,
    )


@dataclass(frozen=True, slots=True)
class _FactorAvailability:
    """Internal validated factor-loading selection result."""

    reason: ConstructionAbsenceReason | None
    invalid_identifiers: tuple[str, ...]
    loadings_by_factor: dict[str, tuple[FactorLoading, ...]]


def _factor_availability(
    *,
    signal_portfolio: SignalPortfolio,
    config: AllocationConfig,
    factor_loadings: tuple[FactorLoading, ...],
    required_factor_ids: tuple[str, ...],
) -> _FactorAvailability:
    cutoff = signal_portfolio.decision_cutoff_at
    expected_keys = {
        (instrument_id, factor_id)
        for instrument_id in signal_portfolio.universe.instrument_ids
        for factor_id in required_factor_ids
    }
    selected = {
        (item.instrument_id, item.factor_id): item
        for item in factor_loadings
        if (item.instrument_id, item.factor_id) in expected_keys
    }
    duplicate_count = sum(
        1 for item in factor_loadings if (item.instrument_id, item.factor_id) in expected_keys
    )
    if duplicate_count != len(selected):
        raise PortfolioConstructionError(
            "factor loadings contain duplicate instrument-factor records"
        )
    missing = tuple(
        f"{instrument_id}:{factor_id}"
        for instrument_id, factor_id in sorted(expected_keys)
        if (instrument_id, factor_id) not in selected
    )
    if missing:
        return _FactorAvailability(
            reason=ConstructionAbsenceReason.MISSING_FACTOR_LOADING,
            invalid_identifiers=missing,
            loadings_by_factor={},
        )
    stale = tuple(
        f"{instrument_id}:{factor_id}"
        for (instrument_id, factor_id), item in sorted(selected.items())
        if item.available_at > cutoff
        or item.as_of_at > cutoff
        or cutoff - item.as_of_at > config.factor_max_age
    )
    if stale:
        return _FactorAvailability(
            reason=ConstructionAbsenceReason.STALE_FACTOR_LOADING,
            invalid_identifiers=stale,
            loadings_by_factor={},
        )
    return _FactorAvailability(
        reason=None,
        invalid_identifiers=(),
        loadings_by_factor={
            factor_id: tuple(
                selected[(instrument_id, factor_id)]
                for instrument_id in signal_portfolio.universe.instrument_ids
            )
            for factor_id in required_factor_ids
        },
    )


def _family_contributions(
    *, signal_portfolio: SignalPortfolio, weights: dict[SignalFamily, Decimal]
) -> dict[str, dict[SignalFamily, Decimal]]:
    contributions: dict[str, dict[SignalFamily, Decimal]] = {
        instrument_id: {} for instrument_id in signal_portfolio.universe.instrument_ids
    }
    for signal in signal_portfolio.signals:
        if signal.forecast_value is None:
            raise PortfolioConstructionError("warm signal portfolio must retain forecast values")
        contributions[signal.instrument_id][signal.signal_family] = (
            signal.forecast_value * weights[signal.signal_family]
        )
    expected_families = set(weights)
    if any(set(values) != expected_families for values in contributions.values()):
        raise PortfolioConstructionError(
            "every instrument must retain every represented signal family"
        )
    return contributions


def _validate_family_limits(
    *, config: AllocationConfig, signal_families: tuple[SignalFamily, ...]
) -> None:
    configured = {item.signal_family for item in config.family_gross_limits}
    if not configured.issubset(signal_families):
        raise PortfolioConstructionError("family gross limit references an absent signal family")


def _apply_family_caps(
    *, contributions: dict[str, dict[SignalFamily, Decimal]], config: AllocationConfig
) -> tuple[dict[str, dict[SignalFamily, Decimal]], set[SignalFamily]]:
    result = {instrument_id: dict(values) for instrument_id, values in contributions.items()}
    binding: set[SignalFamily] = set()
    for limit in config.family_gross_limits:
        gross = sum((abs(values[limit.signal_family]) for values in result.values()), _ZERO)
        if gross > limit.max_abs_gross_exposure:
            scale = limit.max_abs_gross_exposure / gross
            for values in result.values():
                values[limit.signal_family] *= scale
            binding.add(limit.signal_family)
    return result, binding


def _apply_instrument_cap(
    *, targets: dict[str, Decimal], max_abs: Decimal
) -> tuple[dict[str, Decimal], dict[str, tuple[AllocationConstraint, ...]]]:
    capped: dict[str, Decimal] = {}
    constraints: dict[str, tuple[AllocationConstraint, ...]] = {}
    for instrument_id, target in targets.items():
        bounded = min(max_abs, max(-max_abs, target))
        capped[instrument_id] = bounded
        constraints[instrument_id] = (
            (AllocationConstraint.INSTRUMENT_CAP,) if bounded != target else ()
        )
    return capped, constraints


def _apply_portfolio_caps(
    *, targets: dict[str, Decimal], config: AllocationConfig
) -> tuple[dict[str, Decimal], tuple[AllocationConstraint, ...]]:
    result = dict(targets)
    binding: tuple[AllocationConstraint, ...] = ()
    gross = sum((abs(value) for value in result.values()), _ZERO)
    if gross > config.max_gross_exposure:
        result = _scale_targets(result, config.max_gross_exposure / gross)
        binding = _append_constraint(binding, AllocationConstraint.GROSS_CAP)
    net = abs(sum(result.values(), _ZERO))
    if net > config.max_abs_net_exposure:
        if config.max_abs_net_exposure == _ZERO:
            return (
                {instrument_id: _ZERO for instrument_id in result},
                _append_constraint(binding, AllocationConstraint.NET_CAP),
            )
        result = _scale_targets(result, config.max_abs_net_exposure / net)
        binding = _append_constraint(binding, AllocationConstraint.NET_CAP)
    return result, binding


def _factor_exposures(
    *,
    targets: dict[str, Decimal],
    limits: tuple[FactorExposureLimit, ...],
    loadings_by_factor: dict[str, tuple[FactorLoading, ...]],
) -> dict[str, Decimal]:
    return {
        limit.factor_id: sum(
            (
                targets[item.instrument_id] * item.value
                for item in loadings_by_factor[limit.factor_id]
            ),
            _ZERO,
        )
        for limit in limits
    }


def _required_factor_scale_with_limits(
    *, factor_exposures: dict[str, Decimal], limits: tuple[FactorExposureLimit, ...]
) -> Decimal:
    scales = tuple(
        limit.max_abs_exposure / abs(factor_exposures[limit.factor_id])
        for limit in limits
        if abs(factor_exposures[limit.factor_id]) > limit.max_abs_exposure
    )
    return min(scales, default=_ONE)


def _scale_targets(targets: dict[str, Decimal], scale: Decimal) -> dict[str, Decimal]:
    _finite(scale, "scale", nonnegative=True)
    return {instrument_id: target * scale for instrument_id, target in targets.items()}


def _with_family_constraints(
    *,
    local_constraints: dict[str, tuple[AllocationConstraint, ...]],
    family_constraint_bound: set[SignalFamily],
) -> dict[str, tuple[AllocationConstraint, ...]]:
    if not family_constraint_bound:
        return local_constraints
    return {
        instrument_id: _append_constraint(constraints, AllocationConstraint.FAMILY_GROSS_CAP)
        for instrument_id, constraints in local_constraints.items()
    }


def _ready_portfolio(
    *,
    signal_portfolio: SignalPortfolio,
    config: AllocationConfig,
    contributions: dict[str, dict[SignalFamily, Decimal]],
    constrained_contributions: dict[str, dict[SignalFamily, Decimal]],
    provisional_targets: dict[str, Decimal],
    target_values: dict[str, Decimal],
    local_constraints: dict[str, tuple[AllocationConstraint, ...]],
    factor_exposures: tuple[PortfolioFactorExposure, ...],
    binding_constraints: tuple[AllocationConstraint, ...],
) -> ConstructedPortfolio:
    weights = {
        item.signal_family: item.blend_weight for item in signal_portfolio.family_attributions
    }
    allocations = tuple(
        TargetAllocation(
            instrument_id=instrument_id,
            forecast_contributions=tuple(
                ForecastContribution(
                    signal_family=family,
                    forecast_value=contributions[instrument_id][family] / weights[family]
                    if weights[family] != _ZERO
                    else _ZERO,
                    blend_weight=weights[family],
                    provisional_contribution=contributions[instrument_id][family],
                    constrained_contribution=constrained_contributions[instrument_id][family],
                )
                for family in sorted(weights, key=lambda family: family.value)
            ),
            provisional_exposure=provisional_targets[instrument_id],
            target_exposure=target_values[instrument_id],
            local_constraints=local_constraints[instrument_id],
        )
        for instrument_id in signal_portfolio.universe.instrument_ids
    )
    gross = sum((abs(item.target_exposure) for item in allocations), _ZERO)
    net = sum((item.target_exposure for item in allocations), _ZERO)
    return ConstructedPortfolio(
        universe_id=signal_portfolio.universe.universe_id,
        universe_sha256=signal_portfolio.universe.universe_sha256,
        decision_cutoff_at=signal_portfolio.decision_cutoff_at,
        config_version=config.config_version,
        allocations=allocations,
        gross_exposure=gross,
        net_exposure=net,
        cash_residual=_ONE - gross,
        factor_exposures=factor_exposures,
        binding_constraints=binding_constraints,
        ready=True,
        absence_reason=None,
    )


def _unready_portfolio(
    *,
    signal_portfolio: SignalPortfolio,
    config: AllocationConfig,
    reason: ConstructionAbsenceReason,
    missing_or_stale_inputs: tuple[str, ...],
) -> ConstructedPortfolio:
    families = tuple(
        sorted(
            {signal.signal_family for signal in signal_portfolio.signals},
            key=lambda family: family.value,
        )
    )
    allocations = tuple(
        TargetAllocation(
            instrument_id=instrument_id,
            forecast_contributions=tuple(
                ForecastContribution(
                    signal_family=family,
                    forecast_value=_ZERO,
                    blend_weight=next(
                        item.blend_weight
                        for item in signal_portfolio.family_attributions
                        if item.signal_family is family
                    ),
                    provisional_contribution=_ZERO,
                    constrained_contribution=_ZERO,
                )
                for family in families
            ),
            provisional_exposure=_ZERO,
            target_exposure=_ZERO,
            local_constraints=(),
        )
        for instrument_id in signal_portfolio.universe.instrument_ids
    )
    return ConstructedPortfolio(
        universe_id=signal_portfolio.universe.universe_id,
        universe_sha256=signal_portfolio.universe.universe_sha256,
        decision_cutoff_at=signal_portfolio.decision_cutoff_at,
        config_version=config.config_version,
        allocations=allocations,
        gross_exposure=_ZERO,
        net_exposure=_ZERO,
        cash_residual=_ONE,
        factor_exposures=(),
        binding_constraints=(),
        ready=False,
        absence_reason=reason,
        missing_or_stale_inputs=tuple(sorted(missing_or_stale_inputs)),
    )


def _append_constraint(
    existing: tuple[AllocationConstraint, ...], value: AllocationConstraint
) -> tuple[AllocationConstraint, ...]:
    return existing if value in existing else (*existing, value)


def _normalized_identifier(value: str, field_name: str) -> str:
    _identifier(value, field_name)
    return value.strip().upper()


def _identifier(value: str, field_name: str) -> None:
    if _IDENTIFIER.fullmatch(value.strip()) is None:
        raise PortfolioConstructionError(f"{field_name} is invalid")


def _sha256(value: str, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise PortfolioConstructionError(f"{field_name} must be a lowercase SHA-256 digest")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PortfolioConstructionError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(
    value: Decimal, field_name: str, *, positive: bool = False, nonnegative: bool = False
) -> None:
    if not value.is_finite():
        raise PortfolioConstructionError(f"{field_name} must be finite")
    if positive and value <= _ZERO:
        raise PortfolioConstructionError(f"{field_name} must be positive")
    if nonnegative and value < _ZERO:
        raise PortfolioConstructionError(f"{field_name} must be nonnegative")
