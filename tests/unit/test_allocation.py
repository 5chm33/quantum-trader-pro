from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantum_trader.domain.allocation import (
    AllocationConfig,
    AllocationConstraint,
    ConstructedPortfolio,
    ConstructionAbsenceReason,
    FactorExposureLimit,
    FactorLoading,
    FamilyGrossLimit,
    ForecastContribution,
    PortfolioConstructionError,
    PortfolioFactorExposure,
    TargetAllocation,
    construct_factor_aware_portfolio,
)
from quantum_trader.domain.signals import (
    ForecastAbsenceReason,
    ForecastRawInput,
    ForecastSignal,
    FrozenUniverse,
    SignalFamily,
    signal_portfolio,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_CUTOFF = datetime(2024, 1, 5, 21, 0, tzinfo=UTC)


def _universe() -> FrozenUniverse:
    return FrozenUniverse(
        universe_id="universe-phase9-v1",
        instrument_ids=("AAA", "BBB"),
        available_at=_CUTOFF - timedelta(days=1),
        universe_sha256=_SHA_A,
    )


def _signal(
    *, instrument_id: str, family: SignalFamily, value: str | None, ready: bool = True
) -> ForecastSignal:
    return ForecastSignal(
        instrument_id=instrument_id,
        decision_cutoff_at=_CUTOFF,
        signal_family=family,
        raw_inputs=(
            ForecastRawInput(
                record_id=f"record-{family.value[-3:]}-{instrument_id}",
                field_name="fixture",
                value=Decimal("1"),
                available_at=_CUTOFF - timedelta(days=1),
                content_sha256=_SHA_B,
            ),
        )
        if ready
        else (),
        warm_up_complete=ready,
        forecast_value=Decimal(value) if value is not None else None,
        absence_reason=None if ready else ForecastAbsenceReason.INSUFFICIENT_HISTORY,
        signal_version=f"phase9-{family.value[1:3]}-v1",
        universe_id="universe-phase9-v1"
        if family is SignalFamily.CROSS_SECTIONAL_MOMENTUM
        else None,
        universe_sha256=_SHA_A if family is SignalFamily.CROSS_SECTIONAL_MOMENTUM else None,
    )


def _portfolio(
    *,
    tsmom: tuple[str | None, str | None],
    xsmom: tuple[str | None, str | None],
    ready: bool = True,
) -> object:
    universe = _universe()
    signals = (
        _signal(
            instrument_id="AAA",
            family=SignalFamily.TIME_SERIES_MOMENTUM,
            value=tsmom[0],
            ready=ready,
        ),
        _signal(
            instrument_id="BBB",
            family=SignalFamily.TIME_SERIES_MOMENTUM,
            value=tsmom[1],
            ready=ready,
        ),
        _signal(
            instrument_id="AAA",
            family=SignalFamily.CROSS_SECTIONAL_MOMENTUM,
            value=xsmom[0],
            ready=ready,
        ),
        _signal(
            instrument_id="BBB",
            family=SignalFamily.CROSS_SECTIONAL_MOMENTUM,
            value=xsmom[1],
            ready=ready,
        ),
    )
    return signal_portfolio(
        universe=universe,
        decision_cutoff_at=_CUTOFF,
        signals=signals,
        family_blend_weights={
            SignalFamily.TIME_SERIES_MOMENTUM: Decimal("0.5"),
            SignalFamily.CROSS_SECTIONAL_MOMENTUM: Decimal("0.5"),
        },
    )


def _config(**overrides: object) -> AllocationConfig:
    values: dict[str, object] = {
        "config_version": "allocation-phase9-v1",
        "max_abs_instrument_exposure": Decimal("1"),
        "max_gross_exposure": Decimal("2"),
        "max_abs_net_exposure": Decimal("2"),
        "factor_max_age": timedelta(days=10),
    }
    values.update(overrides)
    return AllocationConfig(**values)  # type: ignore[arg-type]


def _loading(*, instrument_id: str, factor_id: str, value: str, age_days: int = 1) -> FactorLoading:
    return FactorLoading(
        instrument_id=instrument_id,
        factor_id=factor_id,
        as_of_at=_CUTOFF - timedelta(days=age_days),
        available_at=_CUTOFF - timedelta(days=age_days),
        value=Decimal(value),
        source_record_id=f"loading-{factor_id}-{instrument_id}",
        source_sha256=_SHA_A,
        model_version="factor-model-v1",
    )


def test_construction_is_deterministic_and_retains_family_forecast_attribution() -> None:
    portfolio = _portfolio(tsmom=("1", "-1"), xsmom=("0.5", "-0.5"))
    result = construct_factor_aware_portfolio(
        signal_portfolio=portfolio,
        config=_config(),
        factor_loadings=(),
    )
    repeated = construct_factor_aware_portfolio(
        signal_portfolio=portfolio,
        config=_config(),
        factor_loadings=(),
    )
    assert result == repeated
    assert result.ready
    assert result.gross_exposure == Decimal("1.5")
    assert result.net_exposure == Decimal("0")
    assert result.cash_residual == Decimal("-0.5")
    assert result.binding_constraints == ()
    aaa, bbb = result.allocations
    assert aaa.instrument_id == "AAA"
    assert aaa.provisional_exposure == Decimal("0.75")
    assert aaa.target_exposure == Decimal("0.75")
    assert [item.signal_family for item in aaa.forecast_contributions] == [
        SignalFamily.TIME_SERIES_MOMENTUM,
        SignalFamily.CROSS_SECTIONAL_MOMENTUM,
    ]
    assert bbb.target_exposure == Decimal("-0.75")


def test_family_instrument_gross_and_net_caps_are_declared_and_reconciled() -> None:
    portfolio = _portfolio(tsmom=("1", "1"), xsmom=("1", "1"))
    config = _config(
        max_abs_instrument_exposure=Decimal("0.3"),
        max_gross_exposure=Decimal("0.5"),
        max_abs_net_exposure=Decimal("0.2"),
        family_gross_limits=(FamilyGrossLimit(SignalFamily.TIME_SERIES_MOMENTUM, Decimal("0.4")),),
    )
    result = construct_factor_aware_portfolio(
        signal_portfolio=portfolio,
        config=config,
        factor_loadings=(),
    )
    assert result.ready
    assert result.gross_exposure == Decimal("0.2")
    assert result.net_exposure == Decimal("0.2")
    assert result.cash_residual == Decimal("0.8")
    assert result.binding_constraints == (
        AllocationConstraint.GROSS_CAP,
        AllocationConstraint.NET_CAP,
    )
    assert all(item.target_exposure == Decimal("0.1") for item in result.allocations)
    assert all(
        AllocationConstraint.FAMILY_GROSS_CAP in item.local_constraints
        and AllocationConstraint.INSTRUMENT_CAP in item.local_constraints
        for item in result.allocations
    )


def test_factor_cap_uses_only_available_loading_receipts_and_scales_entire_portfolio() -> None:
    portfolio = _portfolio(tsmom=("1", "-1"), xsmom=("0", "0"))
    config = _config(
        factor_exposure_limits=(FactorExposureLimit("market", Decimal("0.25")),),
    )
    result = construct_factor_aware_portfolio(
        signal_portfolio=portfolio,
        config=config,
        factor_loadings=(
            _loading(instrument_id="AAA", factor_id="market", value="2"),
            _loading(instrument_id="BBB", factor_id="market", value="0"),
        ),
    )
    assert result.ready
    assert result.binding_constraints == (AllocationConstraint.FACTOR_CAP,)
    assert [item.target_exposure for item in result.allocations] == [
        Decimal("0.125"),
        Decimal("-0.125"),
    ]
    assert result.factor_exposures[0].provisional_exposure == Decimal("1")
    assert result.factor_exposures[0].target_exposure == Decimal("0.25")
    assert tuple(item.instrument_id for item in result.factor_exposures[0].loadings) == (
        "AAA",
        "BBB",
    )


def test_missing_stale_or_incomplete_inputs_produce_explicit_all_cash_outcomes() -> None:
    complete = _portfolio(tsmom=("1", "-1"), xsmom=("0", "0"))
    config = _config(
        factor_exposure_limits=(FactorExposureLimit("market", Decimal("1")),),
    )
    missing = construct_factor_aware_portfolio(
        signal_portfolio=complete,
        config=config,
        factor_loadings=(_loading(instrument_id="AAA", factor_id="market", value="1"),),
    )
    assert not missing.ready
    assert missing.absence_reason is ConstructionAbsenceReason.MISSING_FACTOR_LOADING
    assert missing.missing_or_stale_inputs == ("BBB:MARKET",)
    assert missing.cash_residual == Decimal("1")
    assert all(item.target_exposure == Decimal("0") for item in missing.allocations)

    stale = construct_factor_aware_portfolio(
        signal_portfolio=complete,
        config=_config(
            factor_max_age=timedelta(days=1),
            factor_exposure_limits=(FactorExposureLimit("market", Decimal("1")),),
        ),
        factor_loadings=(
            _loading(instrument_id="AAA", factor_id="market", value="1", age_days=2),
            _loading(instrument_id="BBB", factor_id="market", value="1", age_days=2),
        ),
    )
    assert not stale.ready
    assert stale.absence_reason is ConstructionAbsenceReason.STALE_FACTOR_LOADING
    assert stale.missing_or_stale_inputs == ("AAA:MARKET", "BBB:MARKET")

    incomplete = construct_factor_aware_portfolio(
        signal_portfolio=_portfolio(tsmom=(None, None), xsmom=(None, None), ready=False),
        config=_config(),
        factor_loadings=(),
    )
    assert not incomplete.ready
    assert incomplete.absence_reason is ConstructionAbsenceReason.INCOMPLETE_FORECASTS
    assert len(incomplete.missing_or_stale_inputs) == 4


def test_factor_loading_and_configuration_contracts_reject_ambiguous_inputs() -> None:
    with pytest.raises(PortfolioConstructionError, match="cannot follow"):
        FactorLoading(
            instrument_id="AAA",
            factor_id="market",
            as_of_at=_CUTOFF,
            available_at=_CUTOFF - timedelta(seconds=1),
            value=Decimal("1"),
            source_record_id="loading-invalid",
            source_sha256=_SHA_A,
            model_version="factor-model-v1",
        )
    with pytest.raises(PortfolioConstructionError, match="unique factor ids"):
        _config(
            factor_exposure_limits=(
                FactorExposureLimit("market", Decimal("1")),
                FactorExposureLimit("market", Decimal("2")),
            )
        )
    with pytest.raises(PortfolioConstructionError, match="canonical factor order"):
        _config(
            factor_exposure_limits=(
                FactorExposureLimit("value", Decimal("1")),
                FactorExposureLimit("market", Decimal("1")),
            )
        )
    portfolio = _portfolio(tsmom=("1", "-1"), xsmom=("0", "0"))
    config = _config(
        factor_exposure_limits=(FactorExposureLimit("market", Decimal("1")),),
    )
    duplicate = _loading(instrument_id="AAA", factor_id="market", value="1")
    with pytest.raises(PortfolioConstructionError, match="duplicate"):
        construct_factor_aware_portfolio(
            signal_portfolio=portfolio,
            config=config,
            factor_loadings=(
                duplicate,
                duplicate,
                _loading(instrument_id="BBB", factor_id="market", value="1"),
            ),
        )


def test_configuration_and_family_limit_guards_reject_ambiguous_controls() -> None:
    with pytest.raises(PortfolioConstructionError, match="config_version"):
        _config(config_version="!")
    with pytest.raises(PortfolioConstructionError, match="cannot exceed max_gross"):
        _config(max_abs_instrument_exposure=Decimal("3"), max_gross_exposure=Decimal("2"))
    with pytest.raises(PortfolioConstructionError, match="cannot exceed max_gross"):
        _config(max_abs_net_exposure=Decimal("3"), max_gross_exposure=Decimal("2"))
    with pytest.raises(PortfolioConstructionError, match="factor_max_age"):
        _config(factor_max_age=timedelta(days=-1))
    with pytest.raises(PortfolioConstructionError, match="unique signal families"):
        _config(
            family_gross_limits=(
                FamilyGrossLimit(SignalFamily.TIME_SERIES_MOMENTUM, Decimal("1")),
                FamilyGrossLimit(SignalFamily.TIME_SERIES_MOMENTUM, Decimal("2")),
            )
        )
    portfolio = _portfolio(tsmom=("1", "-1"), xsmom=("0", "0"))
    with pytest.raises(PortfolioConstructionError, match="absent signal family"):
        construct_factor_aware_portfolio(
            signal_portfolio=portfolio,
            config=_config(
                family_gross_limits=(
                    FamilyGrossLimit(SignalFamily.VOLATILITY_TARGETING, Decimal("1")),
                )
            ),
            factor_loadings=(),
        )


def test_zero_net_cap_and_future_factor_availability_fail_closed() -> None:
    all_long = _portfolio(tsmom=("1", "1"), xsmom=("0", "0"))
    zero_net = construct_factor_aware_portfolio(
        signal_portfolio=all_long,
        config=_config(max_abs_net_exposure=Decimal("0")),
        factor_loadings=(),
    )
    assert zero_net.ready
    assert zero_net.gross_exposure == Decimal("0")
    assert zero_net.net_exposure == Decimal("0")
    assert zero_net.binding_constraints == (AllocationConstraint.NET_CAP,)
    assert all(item.target_exposure == Decimal("0") for item in zero_net.allocations)

    complete = _portfolio(tsmom=("1", "-1"), xsmom=("0", "0"))
    factor_config = _config(
        factor_exposure_limits=(FactorExposureLimit("market", Decimal("1")),),
    )
    future = construct_factor_aware_portfolio(
        signal_portfolio=complete,
        config=factor_config,
        factor_loadings=(
            replace(
                _loading(instrument_id="AAA", factor_id="market", value="1"),
                available_at=_CUTOFF + timedelta(seconds=1),
            ),
            _loading(instrument_id="BBB", factor_id="market", value="1"),
        ),
    )
    assert not future.ready
    assert future.absence_reason is ConstructionAbsenceReason.STALE_FACTOR_LOADING
    assert future.missing_or_stale_inputs == ("AAA:MARKET",)


def test_attribution_factor_and_constructed_portfolio_reconciliation_guards() -> None:
    with pytest.raises(PortfolioConstructionError, match="provisional contribution"):
        ForecastContribution(
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            forecast_value=Decimal("1"),
            blend_weight=Decimal("0.5"),
            provisional_contribution=Decimal("0.6"),
            constrained_contribution=Decimal("0.5"),
        )
    contribution = ForecastContribution(
        signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
        forecast_value=Decimal("1"),
        blend_weight=Decimal("0.5"),
        provisional_contribution=Decimal("0.5"),
        constrained_contribution=Decimal("0.5"),
    )
    with pytest.raises(PortfolioConstructionError, match="requires forecast contributions"):
        TargetAllocation("AAA", (), Decimal("0"), Decimal("0"), ())
    with pytest.raises(PortfolioConstructionError, match="provisional exposure"):
        TargetAllocation("AAA", (contribution,), Decimal("0"), Decimal("0"), ())

    first = _loading(instrument_id="AAA", factor_id="market", value="1")
    second = _loading(instrument_id="BBB", factor_id="market", value="1")
    with pytest.raises(PortfolioConstructionError, match="canonical instrument order"):
        PortfolioFactorExposure("market", Decimal("0"), Decimal("0"), Decimal("1"), (second, first))

    result = construct_factor_aware_portfolio(
        signal_portfolio=_portfolio(tsmom=("1", "-1"), xsmom=("0", "0")),
        config=_config(),
        factor_loadings=(),
    )
    assert isinstance(result, ConstructedPortfolio)
    with pytest.raises(PortfolioConstructionError, match="cash_residual"):
        replace(result, cash_residual=Decimal("1"))
    with pytest.raises(PortfolioConstructionError, match="ready portfolio"):
        replace(
            result,
            absence_reason=ConstructionAbsenceReason.MISSING_FACTOR_LOADING,
            missing_or_stale_inputs=("AAA:MARKET",),
        )
    unready = construct_factor_aware_portfolio(
        signal_portfolio=_portfolio(tsmom=(None, None), xsmom=(None, None), ready=False),
        config=_config(),
        factor_loadings=(),
    )
    with pytest.raises(PortfolioConstructionError, match="all cash"):
        replace(
            unready,
            allocations=(
                replace(unready.allocations[0], target_exposure=Decimal("1")),
                *unready.allocations[1:],
            ),
            gross_exposure=Decimal("1"),
            net_exposure=Decimal("1"),
            cash_residual=Decimal("0"),
        )


def test_factor_loading_schema_and_unknown_factor_inputs_are_never_silently_accepted() -> None:
    with pytest.raises(PortfolioConstructionError, match="model_version"):
        replace(_loading(instrument_id="AAA", factor_id="market", value="1"), model_version="!")
    with pytest.raises(PortfolioConstructionError, match="source_sha256"):
        replace(_loading(instrument_id="AAA", factor_id="market", value="1"), source_sha256="x")
    complete = _portfolio(tsmom=("1", "-1"), xsmom=("0", "0"))
    config = _config(
        factor_exposure_limits=(FactorExposureLimit("market", Decimal("1")),),
    )
    result = construct_factor_aware_portfolio(
        signal_portfolio=complete,
        config=config,
        factor_loadings=(
            _loading(instrument_id="AAA", factor_id="market", value="1"),
            _loading(instrument_id="BBB", factor_id="market", value="1"),
            _loading(instrument_id="AAA", factor_id="value", value="99"),
        ),
    )
    assert result.ready
    assert [item.factor_id for item in result.factor_exposures] == ["MARKET"]
