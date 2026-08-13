from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantum_trader.domain.execution_costs import (
    CapacityAssessment,
    CostBreakdown,
    EquityLiquiditySnapshot,
    EstimateStatus,
    ExecutionCostConfig,
    ExecutionCostError,
    ExecutionCostEstimate,
    NoTradeReason,
    ResearchOrderSide,
    ResearchTradeRequest,
    assess_equity_capacity,
    estimate_equity_execution,
)

_SHA = "a" * 64
_CUTOFF = datetime(2024, 1, 5, 21, 0, tzinfo=UTC)


def _snapshot(**overrides: object) -> EquityLiquiditySnapshot:
    values: dict[str, object] = {
        "instrument_id": "AAA",
        "observed_at": _CUTOFF - timedelta(minutes=1),
        "available_at": _CUTOFF - timedelta(seconds=30),
        "bid": Decimal("99"),
        "ask": Decimal("101"),
        "available_volume": 1000,
        "source_record_id": "liquidity-aaa-001",
        "source_sha256": _SHA,
        "source_version": "liquidity-v1",
    }
    values.update(overrides)
    return EquityLiquiditySnapshot(**values)  # type: ignore[arg-type]


def _config(**overrides: object) -> ExecutionCostConfig:
    values: dict[str, object] = {
        "config_version": "execution-cost-v1",
        "max_participation_rate": Decimal("0.10"),
        "maximum_market_data_age": timedelta(minutes=5),
        "commission_per_share": Decimal("0.01"),
        "fee_per_share": Decimal("0.02"),
        "temporary_impact_bps_at_full_participation": Decimal("100"),
        "maximum_total_cost_bps": Decimal("150"),
    }
    values.update(overrides)
    return ExecutionCostConfig(**values)  # type: ignore[arg-type]


def _request(
    *, quantity: int = 50, side: ResearchOrderSide = ResearchOrderSide.BUY
) -> ResearchTradeRequest:
    return ResearchTradeRequest(
        instrument_id="AAA",
        decision_cutoff_at=_CUTOFF,
        side=side,
        requested_quantity=quantity,
        request_id=f"request-{side.value}-{quantity}",
    )


def test_full_buy_and_sell_estimates_reconcile_each_cost_component_deterministically() -> None:
    buy = estimate_equity_execution(
        request=_request(quantity=50), snapshot=_snapshot(), config=_config()
    )
    repeated = estimate_equity_execution(
        request=_request(quantity=50), snapshot=_snapshot(), config=_config()
    )
    sell = estimate_equity_execution(
        request=_request(quantity=50, side=ResearchOrderSide.SELL),
        snapshot=_snapshot(),
        config=_config(),
    )
    assert buy == repeated
    assert buy.status is EstimateStatus.FULL
    assert buy.estimated_filled_quantity == 50
    assert buy.unfilled_quantity == 0
    assert buy.participation_rate == Decimal("0.05")
    assert buy.estimated_execution_price == Decimal("101.05")
    assert buy.cost_breakdown == CostBreakdown(
        half_spread_cost=Decimal("50"),
        commission_cost=Decimal("0.50"),
        fee_cost=Decimal("1.00"),
        temporary_impact_cost=Decimal("2.50"),
        total_cost=Decimal("54.00"),
    )
    assert buy.total_cost_bps == Decimal("108.00")
    assert sell.estimated_execution_price == Decimal("98.95")
    assert sell.cost_breakdown == buy.cost_breakdown
    assert sell.total_cost_bps == buy.total_cost_bps


def test_participation_limit_creates_explicit_partial_fill_without_assuming_remainder() -> None:
    result = estimate_equity_execution(
        request=_request(quantity=150), snapshot=_snapshot(), config=_config()
    )
    assert result.status is EstimateStatus.PARTIAL
    assert result.estimated_filled_quantity == 100
    assert result.unfilled_quantity == 50
    assert result.participation_rate == Decimal("0.1")
    assert result.estimated_execution_price == Decimal("101.10")
    assert result.cost_breakdown is not None
    assert result.cost_breakdown.total_cost == Decimal("113.00")
    assert result.total_cost_bps == Decimal("113.00")
    assert result.no_trade_reason is None


def test_unavailable_stale_zero_volume_and_cost_budget_data_each_fail_closed() -> None:
    unavailable = estimate_equity_execution(
        request=_request(),
        snapshot=replace(_snapshot(), available_at=_CUTOFF + timedelta(seconds=1)),
        config=_config(),
    )
    stale = estimate_equity_execution(
        request=_request(),
        snapshot=replace(_snapshot(), observed_at=_CUTOFF - timedelta(minutes=6)),
        config=_config(),
    )
    zero_volume = estimate_equity_execution(
        request=_request(), snapshot=_snapshot(available_volume=0), config=_config()
    )
    expensive = estimate_equity_execution(
        request=_request(),
        snapshot=_snapshot(),
        config=_config(maximum_total_cost_bps=Decimal("100")),
    )
    for result, reason in (
        (unavailable, NoTradeReason.UNAVAILABLE_AT_CUTOFF),
        (stale, NoTradeReason.STALE_MARKET_DATA),
        (zero_volume, NoTradeReason.ZERO_AVAILABLE_VOLUME),
        (expensive, NoTradeReason.COST_BUDGET_EXCEEDED),
    ):
        assert result.status is EstimateStatus.NO_TRADE
        assert result.estimated_filled_quantity == 0
        assert result.unfilled_quantity == 50
        assert result.participation_rate == Decimal("0")
        assert result.estimated_execution_price is None
        assert result.cost_breakdown is None
        assert result.total_cost_bps is None
        assert result.no_trade_reason is reason


def test_capacity_reports_participation_and_cost_budget_limits_without_deployability_claim() -> (
    None
):
    capacity = assess_equity_capacity(
        instrument_id="AAA", decision_cutoff_at=_CUTOFF, snapshot=_snapshot(), config=_config()
    )
    assert capacity.maximum_quantity == 100
    assert capacity.maximum_notional == Decimal("10000")
    assert capacity.maximum_participation_rate == Decimal("0.10")
    assert capacity.base_cost_bps == Decimal("103.00")
    assert capacity.binding_reason is None

    cost_limited = assess_equity_capacity(
        instrument_id="AAA",
        decision_cutoff_at=_CUTOFF,
        snapshot=_snapshot(),
        config=_config(
            maximum_total_cost_bps=Decimal("120"), max_participation_rate=Decimal("0.50")
        ),
    )
    assert cost_limited.maximum_participation_rate == Decimal("0.17")
    assert cost_limited.maximum_quantity == 170
    assert cost_limited.maximum_notional == Decimal("17000")


def test_capacity_uses_same_causal_and_cost_failures_as_execution_estimates() -> None:
    stale = assess_equity_capacity(
        instrument_id="AAA",
        decision_cutoff_at=_CUTOFF,
        snapshot=replace(_snapshot(), observed_at=_CUTOFF - timedelta(minutes=6)),
        config=_config(),
    )
    zero = assess_equity_capacity(
        instrument_id="AAA",
        decision_cutoff_at=_CUTOFF,
        snapshot=_snapshot(available_volume=0),
        config=_config(),
    )
    expensive = assess_equity_capacity(
        instrument_id="AAA",
        decision_cutoff_at=_CUTOFF,
        snapshot=_snapshot(),
        config=_config(maximum_total_cost_bps=Decimal("100")),
    )
    for result, reason in (
        (stale, NoTradeReason.STALE_MARKET_DATA),
        (zero, NoTradeReason.ZERO_AVAILABLE_VOLUME),
        (expensive, NoTradeReason.COST_BUDGET_EXCEEDED),
    ):
        assert result.maximum_quantity == 0
        assert result.maximum_notional == Decimal("0")
        assert result.maximum_participation_rate == Decimal("0")
        assert result.base_cost_bps is None
        assert result.binding_reason is reason


def test_liquidity_request_and_configuration_contracts_reject_invalid_inputs() -> None:
    with pytest.raises(ExecutionCostError, match="ask cannot"):
        _snapshot(bid=Decimal("101"), ask=Decimal("99"))
    with pytest.raises(ExecutionCostError, match="available_volume"):
        _snapshot(available_volume=-1)
    with pytest.raises(ExecutionCostError, match="source_version"):
        _snapshot(source_version="!")
    with pytest.raises(ExecutionCostError, match="requested_quantity"):
        _request(quantity=0)
    with pytest.raises(ExecutionCostError, match="cannot exceed one"):
        _config(max_participation_rate=Decimal("1.01"))
    with pytest.raises(ExecutionCostError, match="market_data_age"):
        _config(maximum_market_data_age=timedelta(seconds=-1))
    with pytest.raises(ExecutionCostError, match="must be nonnegative"):
        _config(commission_per_share=Decimal("-0.01"))


def test_estimate_and_capacity_result_contracts_reject_incoherent_states() -> None:
    request = _request()
    snapshot = _snapshot()
    with pytest.raises(ExecutionCostError, match="total_cost"):
        CostBreakdown(
            half_spread_cost=Decimal("1"),
            commission_cost=Decimal("1"),
            fee_cost=Decimal("1"),
            temporary_impact_cost=Decimal("1"),
            total_cost=Decimal("3"),
        )
    with pytest.raises(ExecutionCostError, match="no-trade estimate"):
        ExecutionCostEstimate(
            request=request,
            config_version="execution-cost-v1",
            market_snapshot=snapshot,
            status=EstimateStatus.NO_TRADE,
            estimated_filled_quantity=0,
            unfilled_quantity=50,
            participation_rate=Decimal("0.01"),
            estimated_execution_price=None,
            cost_breakdown=None,
            total_cost_bps=None,
            no_trade_reason=NoTradeReason.STALE_MARKET_DATA,
        )
    with pytest.raises(ExecutionCostError, match="zero capacity"):
        CapacityAssessment(
            instrument_id="AAA",
            decision_cutoff_at=_CUTOFF,
            config_version="execution-cost-v1",
            market_snapshot=snapshot,
            maximum_quantity=0,
            maximum_notional=Decimal("0"),
            maximum_participation_rate=Decimal("0"),
            base_cost_bps=Decimal("1"),
            binding_reason=NoTradeReason.STALE_MARKET_DATA,
        )


def test_instrument_mismatches_are_rejected_before_cost_or_capacity_calculation() -> None:
    with pytest.raises(ExecutionCostError, match="instruments must match"):
        estimate_equity_execution(
            request=replace(_request(), instrument_id="BBB"),
            snapshot=_snapshot(),
            config=_config(),
        )
    with pytest.raises(ExecutionCostError, match="instruments must match"):
        assess_equity_capacity(
            instrument_id="BBB", decision_cutoff_at=_CUTOFF, snapshot=_snapshot(), config=_config()
        )


def test_execution_estimate_contract_rejects_incomplete_or_incoherent_fill_states() -> None:
    request = _request()
    snapshot = _snapshot()
    breakdown = CostBreakdown(
        half_spread_cost=Decimal("1"),
        commission_cost=Decimal("0"),
        fee_cost=Decimal("0"),
        temporary_impact_cost=Decimal("0"),
        total_cost=Decimal("1"),
    )
    base: dict[str, object] = {
        "request": request,
        "config_version": "execution-cost-v1",
        "market_snapshot": snapshot,
        "status": EstimateStatus.FULL,
        "estimated_filled_quantity": 50,
        "unfilled_quantity": 0,
        "participation_rate": Decimal("0.05"),
        "estimated_execution_price": Decimal("101"),
        "cost_breakdown": breakdown,
        "total_cost_bps": Decimal("100"),
        "no_trade_reason": None,
    }
    with pytest.raises(ExecutionCostError, match="estimate config_version"):
        ExecutionCostEstimate(**{**base, "config_version": "!"})  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="instruments must match"):
        ExecutionCostEstimate(**{**base, "market_snapshot": replace(snapshot, instrument_id="BBB")})  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="must be nonnegative"):
        ExecutionCostEstimate(**{**base, "estimated_filled_quantity": -1})  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="must reconcile"):
        ExecutionCostEstimate(**{**base, "unfilled_quantity": 1})  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="full estimate"):
        ExecutionCostEstimate(**{**base, "estimated_filled_quantity": 49, "unfilled_quantity": 1})  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="partial estimate"):
        ExecutionCostEstimate(
            **{
                **base,
                "status": EstimateStatus.PARTIAL,
                "estimated_filled_quantity": 0,
                "unfilled_quantity": 50,
            }
        )  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="fillable estimate"):
        ExecutionCostEstimate(**{**base, "cost_breakdown": None})  # type: ignore[arg-type]


def test_capacity_contract_rejects_invalid_positive_and_negative_states() -> None:
    snapshot = _snapshot()
    base: dict[str, object] = {
        "instrument_id": "AAA",
        "decision_cutoff_at": _CUTOFF,
        "config_version": "execution-cost-v1",
        "market_snapshot": snapshot,
        "maximum_quantity": 1,
        "maximum_notional": Decimal("100"),
        "maximum_participation_rate": Decimal("0.01"),
        "base_cost_bps": Decimal("100"),
        "binding_reason": None,
    }
    with pytest.raises(ExecutionCostError, match="capacity config_version"):
        CapacityAssessment(**{**base, "config_version": "!"})  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="instruments must match"):
        CapacityAssessment(**{**base, "instrument_id": "BBB"})  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="maximum_quantity"):
        CapacityAssessment(**{**base, "maximum_quantity": -1})  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="cannot exceed one"):
        CapacityAssessment(**{**base, "maximum_participation_rate": Decimal("1.1")})  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="maximum_notional"):
        CapacityAssessment(**{**base, "maximum_notional": Decimal("99")})  # type: ignore[arg-type]
    with pytest.raises(ExecutionCostError, match="positive capacity"):
        CapacityAssessment(**{**base, "base_cost_bps": None})  # type: ignore[arg-type]


def test_small_participation_rounding_and_zero_impact_coefficient_are_explicit() -> None:
    rounding_config = _config(max_participation_rate=Decimal("0.0001"))
    rounded = estimate_equity_execution(
        request=_request(), snapshot=_snapshot(), config=rounding_config
    )
    assert rounded.status is EstimateStatus.NO_TRADE
    assert rounded.no_trade_reason is NoTradeReason.ZERO_AVAILABLE_VOLUME

    zero_quantity_capacity = assess_equity_capacity(
        instrument_id="AAA",
        decision_cutoff_at=_CUTOFF,
        snapshot=_snapshot(available_volume=1),
        config=_config(max_participation_rate=Decimal("0.1")),
    )
    assert zero_quantity_capacity.maximum_quantity == 0
    assert zero_quantity_capacity.binding_reason is NoTradeReason.COST_BUDGET_EXCEEDED

    no_impact = assess_equity_capacity(
        instrument_id="AAA",
        decision_cutoff_at=_CUTOFF,
        snapshot=_snapshot(),
        config=_config(
            max_participation_rate=Decimal("0.7"),
            temporary_impact_bps_at_full_participation=Decimal("0"),
        ),
    )
    assert no_impact.maximum_participation_rate == Decimal("0.7")
    assert no_impact.maximum_quantity == 700


def test_primitive_input_guards_reject_invalid_identifiers_digests_times_and_decimals() -> None:
    with pytest.raises(ExecutionCostError, match="instrument_id"):
        _snapshot(instrument_id=" ")
    with pytest.raises(ExecutionCostError, match="source_sha256"):
        _snapshot(source_sha256="bad")
    with pytest.raises(ExecutionCostError, match="timezone-aware"):
        _snapshot(observed_at=datetime(2024, 1, 5, 21, 0))
    with pytest.raises(ExecutionCostError, match="bid must be finite"):
        _snapshot(bid=Decimal("NaN"))
    with pytest.raises(ExecutionCostError, match="config_version"):
        _config(config_version="!")
