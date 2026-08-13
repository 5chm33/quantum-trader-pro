"""Causal execution-cost, liquidity, and capacity estimates for research only.

These contracts produce neither broker orders nor executable fill instructions.  They turn a
retained quote/volume observation and declared conservative assumptions into a transparent
counterfactual estimate.  Missing, stale, future-available, or zero-volume evidence becomes
an explicit no-trade estimate; it is never treated as a free fill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum

_ZERO = Decimal("0")
_ONE = Decimal("1")
_TEN_THOUSAND = Decimal("10000")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$")
_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ExecutionCostError(ValueError):
    """Raised when a cost estimate would be ambiguous, non-causal, or unreconcilable."""


class ResearchOrderSide(StrEnum):
    """Direction used only to calculate a conservative research price adjustment."""

    BUY = "buy"
    SELL = "sell"


class EstimateStatus(StrEnum):
    """Whether the retained market state supports a full, partial, or zero research fill."""

    FULL = "full"
    PARTIAL = "partial"
    NO_TRADE = "no_trade"


class NoTradeReason(StrEnum):
    """Reasons an estimate deliberately refuses to manufacture a fill."""

    UNAVAILABLE_AT_CUTOFF = "unavailable_at_cutoff"
    STALE_MARKET_DATA = "stale_market_data"
    ZERO_AVAILABLE_VOLUME = "zero_available_volume"
    COST_BUDGET_EXCEEDED = "cost_budget_exceeded"


@dataclass(frozen=True, slots=True)
class EquityLiquiditySnapshot:
    """One checksum-bound quote and volume observation that may later prove unusable."""

    instrument_id: str
    observed_at: datetime
    available_at: datetime
    bid: Decimal
    ask: Decimal
    available_volume: int
    source_record_id: str
    source_sha256: str
    source_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instrument_id", _normalized_identifier(self.instrument_id, "instrument_id")
        )
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "available_at", _utc(self.available_at, "available_at"))
        _finite(self.bid, "bid", positive=True)
        _finite(self.ask, "ask", positive=True)
        if self.ask < self.bid:
            raise ExecutionCostError("ask cannot be below bid")
        if self.available_volume < 0:
            raise ExecutionCostError("available_volume must be nonnegative")
        _identifier(self.source_record_id, "source_record_id")
        _sha256(self.source_sha256, "source_sha256")
        if _VERSION.fullmatch(self.source_version) is None:
            raise ExecutionCostError("source_version is invalid")

    @property
    def midpoint(self) -> Decimal:
        """Return the finite quote midpoint used for all cost reconciliation."""

        return (self.bid + self.ask) / Decimal("2")


@dataclass(frozen=True, slots=True)
class ResearchTradeRequest:
    """A hypothetical single-instrument trade quantity bound to one decision cutoff."""

    instrument_id: str
    decision_cutoff_at: datetime
    side: ResearchOrderSide
    requested_quantity: int
    request_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instrument_id", _normalized_identifier(self.instrument_id, "instrument_id")
        )
        object.__setattr__(
            self, "decision_cutoff_at", _utc(self.decision_cutoff_at, "decision_cutoff_at")
        )
        if self.requested_quantity < 1:
            raise ExecutionCostError("requested_quantity must be positive")
        _identifier(self.request_id, "request_id")


@dataclass(frozen=True, slots=True)
class ExecutionCostConfig:
    """Predeclared cost and liquidity assumptions for one research configuration."""

    config_version: str
    max_participation_rate: Decimal
    maximum_market_data_age: timedelta
    commission_per_share: Decimal
    fee_per_share: Decimal
    temporary_impact_bps_at_full_participation: Decimal
    maximum_total_cost_bps: Decimal

    def __post_init__(self) -> None:
        if _VERSION.fullmatch(self.config_version) is None:
            raise ExecutionCostError("config_version is invalid")
        _finite(self.max_participation_rate, "max_participation_rate", positive=True)
        if self.max_participation_rate > _ONE:
            raise ExecutionCostError("max_participation_rate cannot exceed one")
        if self.maximum_market_data_age < timedelta(0):
            raise ExecutionCostError("maximum_market_data_age must be nonnegative")
        for field_name in (
            "commission_per_share",
            "fee_per_share",
            "temporary_impact_bps_at_full_participation",
            "maximum_total_cost_bps",
        ):
            _finite(getattr(self, field_name), field_name, nonnegative=True)


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Separate, exact counterfactual cost components for a nonzero estimated fill."""

    half_spread_cost: Decimal
    commission_cost: Decimal
    fee_cost: Decimal
    temporary_impact_cost: Decimal
    total_cost: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "half_spread_cost",
            "commission_cost",
            "fee_cost",
            "temporary_impact_cost",
            "total_cost",
        ):
            _finite(getattr(self, field_name), field_name, nonnegative=True)
        component_total = (
            self.half_spread_cost
            + self.commission_cost
            + self.fee_cost
            + self.temporary_impact_cost
        )
        if self.total_cost != component_total:
            raise ExecutionCostError("total_cost does not reconcile to cost components")


@dataclass(frozen=True, slots=True)
class ExecutionCostEstimate:
    """A non-executable fill estimate with retained quote receipt and explicit remainder."""

    request: ResearchTradeRequest
    config_version: str
    market_snapshot: EquityLiquiditySnapshot
    status: EstimateStatus
    estimated_filled_quantity: int
    unfilled_quantity: int
    participation_rate: Decimal
    estimated_execution_price: Decimal | None
    cost_breakdown: CostBreakdown | None
    total_cost_bps: Decimal | None
    no_trade_reason: NoTradeReason | None

    def __post_init__(self) -> None:
        if _VERSION.fullmatch(self.config_version) is None:
            raise ExecutionCostError("estimate config_version is invalid")
        if self.request.instrument_id != self.market_snapshot.instrument_id:
            raise ExecutionCostError("request and market snapshot instruments must match")
        if self.estimated_filled_quantity < 0 or self.unfilled_quantity < 0:
            raise ExecutionCostError("estimated fill quantities must be nonnegative")
        if (
            self.estimated_filled_quantity + self.unfilled_quantity
            != self.request.requested_quantity
        ):
            raise ExecutionCostError("estimated fill quantities must reconcile to request")
        _finite(self.participation_rate, "participation_rate", nonnegative=True)
        if self.status is EstimateStatus.FULL:
            if self.unfilled_quantity != 0 or self.estimated_filled_quantity == 0:
                raise ExecutionCostError("full estimate must fill the entire positive request")
        elif self.status is EstimateStatus.PARTIAL:
            if self.estimated_filled_quantity == 0 or self.unfilled_quantity == 0:
                raise ExecutionCostError("partial estimate requires both fill and remainder")
        elif (
            self.estimated_filled_quantity != 0
            or self.unfilled_quantity != self.request.requested_quantity
        ):
            raise ExecutionCostError("no-trade estimate must leave the full request unfilled")
        if self.status is EstimateStatus.NO_TRADE:
            if (
                self.estimated_execution_price is not None
                or self.cost_breakdown is not None
                or self.total_cost_bps is not None
                or self.no_trade_reason is None
                or self.participation_rate != _ZERO
            ):
                raise ExecutionCostError("no-trade estimate cannot retain fill economics")
        else:
            if (
                self.estimated_execution_price is None
                or self.cost_breakdown is None
                or self.total_cost_bps is None
                or self.no_trade_reason is not None
            ):
                raise ExecutionCostError("fillable estimate requires complete economics")
            _finite(self.estimated_execution_price, "estimated_execution_price", positive=True)
            _finite(self.total_cost_bps, "total_cost_bps", nonnegative=True)


@dataclass(frozen=True, slots=True)
class CapacityAssessment:
    """Capacity diagnostic under declared volume participation and total-cost budget limits."""

    instrument_id: str
    decision_cutoff_at: datetime
    config_version: str
    market_snapshot: EquityLiquiditySnapshot
    maximum_quantity: int
    maximum_notional: Decimal
    maximum_participation_rate: Decimal
    base_cost_bps: Decimal | None
    binding_reason: NoTradeReason | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "instrument_id", _normalized_identifier(self.instrument_id, "instrument_id")
        )
        object.__setattr__(
            self, "decision_cutoff_at", _utc(self.decision_cutoff_at, "decision_cutoff_at")
        )
        if _VERSION.fullmatch(self.config_version) is None:
            raise ExecutionCostError("capacity config_version is invalid")
        if self.instrument_id != self.market_snapshot.instrument_id:
            raise ExecutionCostError("capacity and market snapshot instruments must match")
        if self.maximum_quantity < 0:
            raise ExecutionCostError("maximum_quantity must be nonnegative")
        _finite(self.maximum_notional, "maximum_notional", nonnegative=True)
        _finite(self.maximum_participation_rate, "maximum_participation_rate", nonnegative=True)
        if self.maximum_participation_rate > _ONE:
            raise ExecutionCostError("maximum_participation_rate cannot exceed one")
        if self.maximum_notional != Decimal(self.maximum_quantity) * self.market_snapshot.midpoint:
            raise ExecutionCostError("maximum_notional must reconcile to quantity and midpoint")
        if self.maximum_quantity == 0:
            if self.base_cost_bps is not None or self.binding_reason is None:
                raise ExecutionCostError(
                    "zero capacity requires a no-trade reason and no cost basis"
                )
        else:
            if self.base_cost_bps is None or self.binding_reason is not None:
                raise ExecutionCostError(
                    "positive capacity requires a base cost basis and no no-trade reason"
                )
            _finite(self.base_cost_bps, "base_cost_bps", nonnegative=True)


def estimate_equity_execution(
    *, request: ResearchTradeRequest, snapshot: EquityLiquiditySnapshot, config: ExecutionCostConfig
) -> ExecutionCostEstimate:
    """Estimate one participation-limited fill from point-in-time quote and volume evidence."""

    _require_matching_instrument(request=request, snapshot=snapshot)
    data_reason = _market_data_reason(
        decision_cutoff_at=request.decision_cutoff_at,
        snapshot=snapshot,
        maximum_market_data_age=config.maximum_market_data_age,
    )
    if data_reason is not None:
        return _no_trade_estimate(
            request=request, snapshot=snapshot, config=config, reason=data_reason
        )
    if snapshot.available_volume == 0:
        return _no_trade_estimate(
            request=request,
            snapshot=snapshot,
            config=config,
            reason=NoTradeReason.ZERO_AVAILABLE_VOLUME,
        )
    maximum_fill = _maximum_participation_quantity(snapshot=snapshot, config=config)
    if maximum_fill == 0:
        return _no_trade_estimate(
            request=request,
            snapshot=snapshot,
            config=config,
            reason=NoTradeReason.ZERO_AVAILABLE_VOLUME,
        )
    filled = min(request.requested_quantity, maximum_fill)
    participation = Decimal(filled) / Decimal(snapshot.available_volume)
    economics = _fill_economics(
        request=request,
        snapshot=snapshot,
        config=config,
        quantity=filled,
        participation_rate=participation,
    )
    if economics.total_cost_bps > config.maximum_total_cost_bps:
        return _no_trade_estimate(
            request=request,
            snapshot=snapshot,
            config=config,
            reason=NoTradeReason.COST_BUDGET_EXCEEDED,
        )
    return ExecutionCostEstimate(
        request=request,
        config_version=config.config_version,
        market_snapshot=snapshot,
        status=EstimateStatus.FULL
        if filled == request.requested_quantity
        else EstimateStatus.PARTIAL,
        estimated_filled_quantity=filled,
        unfilled_quantity=request.requested_quantity - filled,
        participation_rate=participation,
        estimated_execution_price=economics.execution_price,
        cost_breakdown=economics.breakdown,
        total_cost_bps=economics.total_cost_bps,
        no_trade_reason=None,
    )


def assess_equity_capacity(
    *,
    instrument_id: str,
    decision_cutoff_at: datetime,
    snapshot: EquityLiquiditySnapshot,
    config: ExecutionCostConfig,
) -> CapacityAssessment:
    """Calculate maximum quantity under declared participation and all-in per-trade cost budget."""

    normalized = _normalized_identifier(instrument_id, "instrument_id")
    cutoff = _utc(decision_cutoff_at, "decision_cutoff_at")
    if normalized != snapshot.instrument_id:
        raise ExecutionCostError("capacity instrument and market snapshot instruments must match")
    data_reason = _market_data_reason(
        decision_cutoff_at=cutoff,
        snapshot=snapshot,
        maximum_market_data_age=config.maximum_market_data_age,
    )
    if data_reason is not None:
        return _no_capacity(
            instrument_id=normalized,
            decision_cutoff_at=cutoff,
            snapshot=snapshot,
            config=config,
            reason=data_reason,
        )
    if snapshot.available_volume == 0:
        return _no_capacity(
            instrument_id=normalized,
            decision_cutoff_at=cutoff,
            snapshot=snapshot,
            config=config,
            reason=NoTradeReason.ZERO_AVAILABLE_VOLUME,
        )
    base_cost_bps = _base_cost_bps(snapshot=snapshot, config=config)
    if base_cost_bps > config.maximum_total_cost_bps:
        return _no_capacity(
            instrument_id=normalized,
            decision_cutoff_at=cutoff,
            snapshot=snapshot,
            config=config,
            reason=NoTradeReason.COST_BUDGET_EXCEEDED,
        )
    affordable_participation = _maximum_affordable_participation(
        base_cost_bps=base_cost_bps,
        config=config,
    )
    participation = min(config.max_participation_rate, affordable_participation)
    quantity = int(
        (Decimal(snapshot.available_volume) * participation).to_integral_value(rounding=ROUND_FLOOR)
    )
    if quantity == 0:
        return _no_capacity(
            instrument_id=normalized,
            decision_cutoff_at=cutoff,
            snapshot=snapshot,
            config=config,
            reason=NoTradeReason.COST_BUDGET_EXCEEDED,
        )
    return CapacityAssessment(
        instrument_id=normalized,
        decision_cutoff_at=cutoff,
        config_version=config.config_version,
        market_snapshot=snapshot,
        maximum_quantity=quantity,
        maximum_notional=Decimal(quantity) * snapshot.midpoint,
        maximum_participation_rate=participation,
        base_cost_bps=base_cost_bps,
        binding_reason=None,
    )


@dataclass(frozen=True, slots=True)
class _FillEconomics:
    execution_price: Decimal
    breakdown: CostBreakdown
    total_cost_bps: Decimal


def _fill_economics(
    *,
    request: ResearchTradeRequest,
    snapshot: EquityLiquiditySnapshot,
    config: ExecutionCostConfig,
    quantity: int,
    participation_rate: Decimal,
) -> _FillEconomics:
    midpoint = snapshot.midpoint
    half_spread_per_share = (snapshot.ask - snapshot.bid) / Decimal("2")
    impact_bps = config.temporary_impact_bps_at_full_participation * participation_rate
    temporary_impact_per_share = midpoint * impact_bps / _TEN_THOUSAND
    direction = _ONE if request.side is ResearchOrderSide.BUY else Decimal("-1")
    execution_price = midpoint + direction * (half_spread_per_share + temporary_impact_per_share)
    _finite(execution_price, "estimated execution price", positive=True)
    half_spread_cost = Decimal(quantity) * half_spread_per_share
    commission_cost = Decimal(quantity) * config.commission_per_share
    fee_cost = Decimal(quantity) * config.fee_per_share
    temporary_impact_cost = Decimal(quantity) * temporary_impact_per_share
    breakdown = CostBreakdown(
        half_spread_cost=half_spread_cost,
        commission_cost=commission_cost,
        fee_cost=fee_cost,
        temporary_impact_cost=temporary_impact_cost,
        total_cost=half_spread_cost + commission_cost + fee_cost + temporary_impact_cost,
    )
    total_cost_bps = breakdown.total_cost / (Decimal(quantity) * midpoint) * _TEN_THOUSAND
    _finite(total_cost_bps, "total_cost_bps", nonnegative=True)
    return _FillEconomics(
        execution_price=execution_price,
        breakdown=breakdown,
        total_cost_bps=total_cost_bps,
    )


def _base_cost_bps(*, snapshot: EquityLiquiditySnapshot, config: ExecutionCostConfig) -> Decimal:
    midpoint = snapshot.midpoint
    half_spread_per_share = (snapshot.ask - snapshot.bid) / Decimal("2")
    base = (
        (half_spread_per_share + config.commission_per_share + config.fee_per_share)
        / midpoint
        * _TEN_THOUSAND
    )
    _finite(base, "base_cost_bps", nonnegative=True)
    return base


def _maximum_affordable_participation(
    *, base_cost_bps: Decimal, config: ExecutionCostConfig
) -> Decimal:
    remaining_cost_budget = config.maximum_total_cost_bps - base_cost_bps
    coefficient = config.temporary_impact_bps_at_full_participation
    if coefficient == _ZERO:
        return _ONE
    participation = remaining_cost_budget / coefficient
    return min(_ONE, max(_ZERO, participation))


def _maximum_participation_quantity(
    *, snapshot: EquityLiquiditySnapshot, config: ExecutionCostConfig
) -> int:
    return int(
        (Decimal(snapshot.available_volume) * config.max_participation_rate).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )


def _market_data_reason(
    *,
    decision_cutoff_at: datetime,
    snapshot: EquityLiquiditySnapshot,
    maximum_market_data_age: timedelta,
) -> NoTradeReason | None:
    if snapshot.available_at > decision_cutoff_at or snapshot.observed_at > decision_cutoff_at:
        return NoTradeReason.UNAVAILABLE_AT_CUTOFF
    if decision_cutoff_at - snapshot.observed_at > maximum_market_data_age:
        return NoTradeReason.STALE_MARKET_DATA
    return None


def _require_matching_instrument(
    *, request: ResearchTradeRequest, snapshot: EquityLiquiditySnapshot
) -> None:
    if request.instrument_id != snapshot.instrument_id:
        raise ExecutionCostError("request and market snapshot instruments must match")


def _no_trade_estimate(
    *,
    request: ResearchTradeRequest,
    snapshot: EquityLiquiditySnapshot,
    config: ExecutionCostConfig,
    reason: NoTradeReason,
) -> ExecutionCostEstimate:
    return ExecutionCostEstimate(
        request=request,
        config_version=config.config_version,
        market_snapshot=snapshot,
        status=EstimateStatus.NO_TRADE,
        estimated_filled_quantity=0,
        unfilled_quantity=request.requested_quantity,
        participation_rate=_ZERO,
        estimated_execution_price=None,
        cost_breakdown=None,
        total_cost_bps=None,
        no_trade_reason=reason,
    )


def _no_capacity(
    *,
    instrument_id: str,
    decision_cutoff_at: datetime,
    snapshot: EquityLiquiditySnapshot,
    config: ExecutionCostConfig,
    reason: NoTradeReason,
) -> CapacityAssessment:
    return CapacityAssessment(
        instrument_id=instrument_id,
        decision_cutoff_at=decision_cutoff_at,
        config_version=config.config_version,
        market_snapshot=snapshot,
        maximum_quantity=0,
        maximum_notional=_ZERO,
        maximum_participation_rate=_ZERO,
        base_cost_bps=None,
        binding_reason=reason,
    )


def _normalized_identifier(value: str, field_name: str) -> str:
    _identifier(value, field_name)
    return value.strip().upper()


def _identifier(value: str, field_name: str) -> None:
    if _IDENTIFIER.fullmatch(value.strip()) is None:
        raise ExecutionCostError(f"{field_name} is invalid")


def _sha256(value: str, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ExecutionCostError(f"{field_name} must be a lowercase SHA-256 digest")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionCostError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(
    value: Decimal, field_name: str, *, positive: bool = False, nonnegative: bool = False
) -> None:
    if not value.is_finite():
        raise ExecutionCostError(f"{field_name} must be finite")
    if positive and value <= _ZERO:
        raise ExecutionCostError(f"{field_name} must be positive")
    if nonnegative and value < _ZERO:
        raise ExecutionCostError(f"{field_name} must be nonnegative")
