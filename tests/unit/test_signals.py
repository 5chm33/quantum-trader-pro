from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantum_trader.domain.research_data import (
    AssetClass,
    BarFinality,
    DataAvailability,
    DataProvenance,
    EquityBarRecord,
    RecordIdentity,
    SecurityIdentity,
)
from quantum_trader.domain.signals import (
    BaselineAllocation,
    BaselinePortfolio,
    FamilyAttribution,
    ForecastAbsenceReason,
    ForecastRawInput,
    ForecastSignal,
    FrozenUniverse,
    PermanentBaseline,
    ScalingStatus,
    SignalDataError,
    SignalFamily,
    SignalPortfolio,
    TrendForecastVariant,
    VolatilityScalingConfig,
    cash_baseline,
    cross_sectional_momentum_forecasts,
    equal_weight_baseline,
    permanent_baselines,
    signal_portfolio,
    time_series_momentum_forecast,
    trend_only_baseline,
    volatility_scale_forecast,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_BASE_TIME = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)
_CUTOFF = _BASE_TIME + timedelta(days=20)


def _bar(
    instrument_id: str,
    index: int,
    close: str,
    *,
    available_offset_days: int = 0,
) -> EquityBarRecord:
    event_at = _BASE_TIME + timedelta(days=index)
    available_at = event_at + timedelta(days=available_offset_days, minutes=10)
    price = Decimal(close)
    return EquityBarRecord(
        identity=RecordIdentity(record_id=f"bar-{instrument_id}-{index:04d}"),
        security=SecurityIdentity(
            instrument_id=instrument_id,
            asset_class=AssetClass.ETF,
            currency="usd",
        ),
        availability=DataAvailability(
            event_at=event_at,
            available_at=available_at,
            captured_at=available_at + timedelta(minutes=1),
        ),
        provenance=DataProvenance(
            provider="fixture",
            dataset="bars",
            provider_schema_version="v1",
            source_uri="fixture://bars",
            license_class="synthetic",
            redistribution_allowed=True,
            raw_sha256=_SHA_A,
            query_sha256=_SHA_B,
            transform_version="test-v1",
        ),
        interval="1d",
        session="regular",
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("100"),
        finality=BarFinality.FINAL,
        adjusted_close=price,
    )


def _universe() -> FrozenUniverse:
    return FrozenUniverse(
        universe_id="etf-universe-v1",
        instrument_ids=("AAA", "BBB", "CCC"),
        available_at=_BASE_TIME,
        universe_sha256=_SHA_A,
    )


def test_forecast_contract_rejects_naive_and_inconsistent_states() -> None:
    raw_input = ForecastRawInput(
        record_id="raw-input-0001",
        field_name="adjusted_close",
        value=Decimal("100"),
        available_at=_BASE_TIME,
        content_sha256=_SHA_A,
    )
    with pytest.raises(SignalDataError, match="timezone-aware"):
        ForecastSignal(
            instrument_id="AAA",
            decision_cutoff_at=datetime(2024, 1, 1),
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            raw_inputs=(raw_input,),
            warm_up_complete=True,
            forecast_value=Decimal("1"),
            absence_reason=None,
            signal_version="tsmom-v1",
        )
    with pytest.raises(SignalDataError, match="requires no value"):
        ForecastSignal(
            instrument_id="AAA",
            decision_cutoff_at=_CUTOFF,
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            raw_inputs=(raw_input,),
            warm_up_complete=False,
            forecast_value=Decimal("1"),
            absence_reason=ForecastAbsenceReason.INSUFFICIENT_HISTORY,
            signal_version="tsmom-v1",
        )
    unavailable_input = ForecastRawInput(
        record_id="raw-input-0002",
        field_name="adjusted_close",
        value=Decimal("100"),
        available_at=_BASE_TIME + timedelta(minutes=1),
        content_sha256=_SHA_A,
    )
    with pytest.raises(SignalDataError, match="unavailable"):
        ForecastSignal(
            instrument_id="AAA",
            decision_cutoff_at=_BASE_TIME,
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            raw_inputs=(unavailable_input,),
            warm_up_complete=True,
            forecast_value=Decimal("1"),
            absence_reason=None,
            signal_version="tsmom-v1",
        )


def test_time_series_momentum_warms_up_and_ignores_unavailable_future_input() -> None:
    bars = (
        _bar("AAA", 0, "100"),
        _bar("AAA", 1, "110"),
    )
    incomplete = time_series_momentum_forecast(
        instrument_id="AAA",
        bars=bars,
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
    )
    assert incomplete.warm_up_complete is False
    assert incomplete.forecast_value is None
    assert incomplete.absence_reason is ForecastAbsenceReason.INSUFFICIENT_HISTORY

    complete_bars = (*bars, _bar("AAA", 2, "120"))
    baseline = time_series_momentum_forecast(
        instrument_id="AAA",
        bars=complete_bars,
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
    )
    repeated = time_series_momentum_forecast(
        instrument_id="AAA",
        bars=complete_bars,
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
    )
    with_delayed_future = time_series_momentum_forecast(
        instrument_id="AAA",
        bars=(*complete_bars, _bar("AAA", 3, "1000", available_offset_days=30)),
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
    )
    continuous = time_series_momentum_forecast(
        instrument_id="AAA",
        bars=complete_bars,
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
        variant=TrendForecastVariant.CONTINUOUS_RETURN,
    )
    assert baseline == repeated == with_delayed_future
    assert baseline.forecast_value == Decimal("1")
    assert continuous.forecast_value == Decimal("0.2")
    assert tuple(raw_input.record_id for raw_input in baseline.raw_inputs) == (
        "bar-AAA-0000",
        "bar-AAA-0001",
        "bar-AAA-0002",
    )


def test_cross_sectional_momentum_ranks_complete_frozen_universe_and_fails_closed() -> None:
    universe = _universe()
    bars_by_instrument = {
        "AAA": (_bar("AAA", 0, "100"), _bar("AAA", 1, "110"), _bar("AAA", 2, "130")),
        "BBB": (_bar("BBB", 0, "100"), _bar("BBB", 1, "105"), _bar("BBB", 2, "110")),
        "CCC": (_bar("CCC", 0, "100"), _bar("CCC", 1, "95"), _bar("CCC", 2, "90")),
    }
    ranked = cross_sectional_momentum_forecasts(
        universe=universe,
        bars_by_instrument=bars_by_instrument,
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
        minimum_eligible_members=3,
    )
    assert tuple(signal.instrument_id for signal in ranked) == ("AAA", "BBB", "CCC")
    assert tuple(signal.forecast_value for signal in ranked) == (
        Decimal("1"),
        Decimal("0"),
        Decimal("-1"),
    )
    assert all(signal.universe_sha256 == _SHA_A for signal in ranked)

    incomplete_universe = dict(bars_by_instrument)
    incomplete_universe["CCC"] = (_bar("CCC", 0, "100"), _bar("CCC", 1, "90"))
    failed_closed = cross_sectional_momentum_forecasts(
        universe=universe,
        bars_by_instrument=incomplete_universe,
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
        minimum_eligible_members=3,
    )
    assert all(signal.warm_up_complete is False for signal in failed_closed)
    assert {signal.absence_reason for signal in failed_closed} == {
        ForecastAbsenceReason.INSUFFICIENT_ELIGIBLE_MEMBERS
    }
    with pytest.raises(SignalDataError, match="every and only"):
        cross_sectional_momentum_forecasts(
            universe=universe,
            bars_by_instrument={"AAA": bars_by_instrument["AAA"]},
            decision_cutoff_at=_CUTOFF,
            lookback_bars=2,
            minimum_eligible_members=2,
        )


def test_volatility_scaling_is_bounded_and_preserves_explicit_warmup_paths() -> None:
    bars = (
        _bar("AAA", 0, "100"),
        _bar("AAA", 1, "110"),
        _bar("AAA", 2, "100"),
        _bar("AAA", 3, "120"),
    )
    source = time_series_momentum_forecast(
        instrument_id="AAA",
        bars=bars,
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
    )
    config = VolatilityScalingConfig(
        target_annualized_volatility=Decimal("1"),
        estimation_window_bars=3,
        minimum_observations=3,
        max_leverage=Decimal("1.2"),
        annualization_observations=1,
    )
    scaled = volatility_scale_forecast(source_signal=source, bars=bars, config=config)
    assert scaled.scaling_status is ScalingStatus.APPLIED
    assert scaled.exposure_multiplier == Decimal("1.2")
    assert scaled.forecast_value == Decimal("1.2")

    unscaled = volatility_scale_forecast(source_signal=source, bars=bars[:3], config=config)
    assert unscaled.scaling_status is ScalingStatus.UNSCALED_INSUFFICIENT_HISTORY
    assert unscaled.exposure_multiplier == Decimal("1")
    assert unscaled.forecast_value == source.forecast_value

    source_absent = time_series_momentum_forecast(
        instrument_id="AAA",
        bars=bars[:2],
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
    )
    absent_scale = volatility_scale_forecast(
        source_signal=source_absent,
        bars=bars,
        config=config,
    )
    assert absent_scale.warm_up_complete is False
    assert absent_scale.absence_reason is ForecastAbsenceReason.SOURCE_FORECAST_ABSENT
    assert absent_scale.scaling_status is ScalingStatus.SOURCE_FORECAST_ABSENT

    zero_volatility = volatility_scale_forecast(
        source_signal=time_series_momentum_forecast(
            instrument_id="AAA",
            bars=tuple(_bar("AAA", index, "100") for index in range(4)),
            decision_cutoff_at=_CUTOFF,
            lookback_bars=2,
        ),
        bars=tuple(_bar("AAA", index, "100") for index in range(4)),
        config=config,
    )
    assert zero_volatility.scaling_status is ScalingStatus.UNSCALED_ZERO_VOLATILITY
    assert zero_volatility.exposure_multiplier == Decimal("1")


def test_permanent_baselines_and_signal_portfolio_reconcile_exactly() -> None:
    universe = _universe()
    trend_signals = tuple(
        time_series_momentum_forecast(
            instrument_id=instrument_id,
            bars=bars,
            decision_cutoff_at=_CUTOFF,
            lookback_bars=2,
        )
        for instrument_id, bars in {
            "AAA": (_bar("AAA", 0, "100"), _bar("AAA", 1, "110"), _bar("AAA", 2, "120")),
            "BBB": (_bar("BBB", 0, "100"), _bar("BBB", 1, "90"), _bar("BBB", 2, "80")),
            "CCC": (_bar("CCC", 0, "100"), _bar("CCC", 1, "100"), _bar("CCC", 2, "100")),
        }.items()
    )
    equal = equal_weight_baseline(universe=universe, decision_cutoff_at=_CUTOFF)
    cash = cash_baseline(universe=universe, decision_cutoff_at=_CUTOFF)
    trend = trend_only_baseline(
        universe=universe,
        decision_cutoff_at=_CUTOFF,
        trend_signals=trend_signals,
    )
    all_baselines = permanent_baselines(
        universe=universe,
        decision_cutoff_at=_CUTOFF,
        trend_signals=trend_signals,
    )
    assert equal.baseline is PermanentBaseline.EQUAL_WEIGHT
    assert {allocation.target_exposure for allocation in equal.allocations} == {Decimal("1") / 3}
    assert all(allocation.target_exposure == Decimal("0") for allocation in cash.allocations)
    assert tuple(allocation.target_exposure for allocation in trend.allocations) == (
        Decimal("1") / 3,
        Decimal("-1") / 3,
        Decimal("0"),
    )
    assert tuple(baseline.baseline for baseline in all_baselines) == (
        PermanentBaseline.EQUAL_WEIGHT,
        PermanentBaseline.TREND_ONLY,
        PermanentBaseline.CASH,
    )

    cross_sectional = cross_sectional_momentum_forecasts(
        universe=universe,
        bars_by_instrument={
            "AAA": (_bar("AAA", 0, "100"), _bar("AAA", 1, "110"), _bar("AAA", 2, "120")),
            "BBB": (_bar("BBB", 0, "100"), _bar("BBB", 1, "95"), _bar("BBB", 2, "90")),
            "CCC": (_bar("CCC", 0, "100"), _bar("CCC", 1, "100"), _bar("CCC", 2, "100")),
        },
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
        minimum_eligible_members=3,
    )
    portfolio = signal_portfolio(
        universe=universe,
        decision_cutoff_at=_CUTOFF,
        signals=tuple(reversed(trend_signals + cross_sectional)),
        family_blend_weights={
            SignalFamily.TIME_SERIES_MOMENTUM: Decimal("0.5"),
            SignalFamily.CROSS_SECTIONAL_MOMENTUM: Decimal("0.5"),
        },
    )
    assert tuple(
        (signal.signal_family, signal.instrument_id) for signal in portfolio.signals
    ) == tuple(
        sorted(
            (
                (signal.signal_family, signal.instrument_id)
                for signal in trend_signals + cross_sectional
            ),
            key=lambda item: (item[0].value, item[1]),
        )
    )
    assert sum(item.blend_weight for item in portfolio.family_attributions) == Decimal("1")

    with pytest.raises(SignalDataError, match="canonical frozen-universe order"):
        BaselineAllocation(instrument_id="AAA", target_exposure=Decimal("1"))
        type(equal)(
            baseline=PermanentBaseline.EQUAL_WEIGHT,
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            allocations=(
                BaselineAllocation(instrument_id="BBB", target_exposure=Decimal("1") / 3),
                BaselineAllocation(instrument_id="AAA", target_exposure=Decimal("1") / 3),
                BaselineAllocation(instrument_id="CCC", target_exposure=Decimal("1") / 3),
            ),
            warm_up_complete=True,
            absence_reason=None,
        )


def test_forecast_contract_rejects_missing_evidence_nonfinite_values_and_invalid_scaling() -> None:
    raw_input = ForecastRawInput(
        record_id="raw-input-0100",
        field_name="adjusted_close",
        value=Decimal("100"),
        available_at=_BASE_TIME,
        content_sha256=_SHA_A,
    )
    with pytest.raises(SignalDataError, match="at least one retained raw input"):
        ForecastSignal(
            instrument_id="AAA",
            decision_cutoff_at=_CUTOFF,
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            raw_inputs=(),
            warm_up_complete=True,
            forecast_value=Decimal("1"),
            absence_reason=None,
            signal_version="tsmom-v1",
        )
    with pytest.raises(SignalDataError, match="finite"):
        ForecastSignal(
            instrument_id="AAA",
            decision_cutoff_at=_CUTOFF,
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            raw_inputs=(raw_input,),
            warm_up_complete=True,
            forecast_value=Decimal("NaN"),
            absence_reason=None,
            signal_version="tsmom-v1",
        )
    with pytest.raises(SignalDataError, match="record_ids must be unique"):
        ForecastSignal(
            instrument_id="AAA",
            decision_cutoff_at=_CUTOFF,
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            raw_inputs=(raw_input, raw_input),
            warm_up_complete=True,
            forecast_value=Decimal("1"),
            absence_reason=None,
            signal_version="tsmom-v1",
        )
    with pytest.raises(SignalDataError, match="universe_id and universe_sha256"):
        ForecastSignal(
            instrument_id="AAA",
            decision_cutoff_at=_CUTOFF,
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            raw_inputs=(raw_input,),
            warm_up_complete=True,
            forecast_value=Decimal("1"),
            absence_reason=None,
            signal_version="tsmom-v1",
            universe_id="etf-universe-v1",
        )
    with pytest.raises(SignalDataError, match="non-scaled"):
        ForecastSignal(
            instrument_id="AAA",
            decision_cutoff_at=_CUTOFF,
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            raw_inputs=(raw_input,),
            warm_up_complete=True,
            forecast_value=Decimal("1"),
            absence_reason=None,
            signal_version="tsmom-v1",
            exposure_multiplier=Decimal("1"),
        )


def test_frozen_universe_and_attribution_reject_ambiguous_membership_and_weights() -> None:
    with pytest.raises(SignalDataError, match="duplicate"):
        FrozenUniverse(
            universe_id="etf-universe-v1",
            instrument_ids=("AAA", "AAA"),
            available_at=_BASE_TIME,
            universe_sha256=_SHA_A,
        )
    with pytest.raises(SignalDataError, match="canonical sorted"):
        FrozenUniverse(
            universe_id="etf-universe-v1",
            instrument_ids=("BBB", "AAA"),
            available_at=_BASE_TIME,
            universe_sha256=_SHA_A,
        )
    with pytest.raises(SignalDataError, match="nonnegative"):
        FamilyAttribution(
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            blend_weight=Decimal("-0.1"),
            signal_count=1,
        )
    with pytest.raises(SignalDataError, match="signal_count"):
        FamilyAttribution(
            signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
            blend_weight=Decimal("1"),
            signal_count=0,
        )


def test_signal_portfolio_and_baselines_fail_closed_for_unreconciled_inputs() -> None:
    universe = _universe()
    trend_signals = tuple(
        time_series_momentum_forecast(
            instrument_id=instrument_id,
            bars=(
                _bar(instrument_id, 0, "100"),
                _bar(instrument_id, 1, "110"),
                _bar(instrument_id, 2, "120"),
            ),
            decision_cutoff_at=_CUTOFF,
            lookback_bars=2,
        )
        for instrument_id in universe.instrument_ids
    )
    with pytest.raises(SignalDataError, match="exactly cover"):
        signal_portfolio(
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            signals=trend_signals,
            family_blend_weights={},
        )
    with pytest.raises(SignalDataError, match="sum exactly"):
        signal_portfolio(
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            signals=trend_signals,
            family_blend_weights={SignalFamily.TIME_SERIES_MOMENTUM: Decimal("0.9")},
        )
    with pytest.raises(SignalDataError, match="exactly match"):
        trend_only_baseline(
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            trend_signals=trend_signals[:2],
        )
    incomplete_trend = (
        *trend_signals[:2],
        time_series_momentum_forecast(
            instrument_id="CCC",
            bars=(_bar("CCC", 0, "100"), _bar("CCC", 1, "101")),
            decision_cutoff_at=_CUTOFF,
            lookback_bars=2,
        ),
    )
    unready = trend_only_baseline(
        universe=universe,
        decision_cutoff_at=_CUTOFF,
        trend_signals=incomplete_trend,
    )
    assert unready.warm_up_complete is False
    assert unready.absence_reason is ForecastAbsenceReason.SOURCE_FORECAST_ABSENT
    assert all(item.target_exposure == Decimal("0") for item in unready.allocations)


def test_cross_sectional_ties_and_scaling_configuration_reject_unsafe_parameters() -> None:
    universe = _universe()
    tied = cross_sectional_momentum_forecasts(
        universe=universe,
        bars_by_instrument={
            "AAA": (_bar("AAA", 0, "100"), _bar("AAA", 1, "110"), _bar("AAA", 2, "120")),
            "BBB": (_bar("BBB", 0, "100"), _bar("BBB", 1, "110"), _bar("BBB", 2, "120")),
            "CCC": (_bar("CCC", 0, "100"), _bar("CCC", 1, "90"), _bar("CCC", 2, "80")),
        },
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
        minimum_eligible_members=3,
    )
    assert tuple(signal.forecast_value for signal in tied) == (
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("-1"),
    )
    with pytest.raises(SignalDataError, match="cannot exceed"):
        VolatilityScalingConfig(
            target_annualized_volatility=Decimal("0.1"),
            estimation_window_bars=5,
            minimum_observations=3,
            max_leverage=Decimal("1"),
            min_leverage=Decimal("2"),
        )
    source = time_series_momentum_forecast(
        instrument_id="AAA",
        bars=(_bar("AAA", 0, "100"), _bar("AAA", 1, "110"), _bar("AAA", 2, "120")),
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
    )
    with pytest.raises(SignalDataError, match="does not match"):
        volatility_scale_forecast(
            source_signal=source,
            bars=(_bar("BBB", 0, "100"), _bar("BBB", 1, "110")),
            config=VolatilityScalingConfig(
                target_annualized_volatility=Decimal("0.1"),
                estimation_window_bars=2,
                minimum_observations=2,
                max_leverage=Decimal("1"),
            ),
        )


def test_signal_portfolio_rejects_unavailable_unsorted_duplicate_and_unreconciled_members() -> None:
    universe = _universe()
    first = time_series_momentum_forecast(
        instrument_id="AAA",
        bars=(_bar("AAA", 0, "100"), _bar("AAA", 1, "110"), _bar("AAA", 2, "120")),
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
    )
    second = time_series_momentum_forecast(
        instrument_id="BBB",
        bars=(_bar("BBB", 0, "100"), _bar("BBB", 1, "110"), _bar("BBB", 2, "120")),
        decision_cutoff_at=_CUTOFF,
        lookback_bars=2,
    )
    attribution = FamilyAttribution(
        signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
        blend_weight=Decimal("1"),
        signal_count=2,
    )
    with pytest.raises(SignalDataError, match="canonical family"):
        SignalPortfolio(
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            signals=(second, first),
            family_attributions=(attribution,),
        )
    with pytest.raises(SignalDataError, match="duplicate family"):
        SignalPortfolio(
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            signals=(first, first),
            family_attributions=(attribution,),
        )
    stale_signal = ForecastSignal(
        instrument_id="BBB",
        decision_cutoff_at=_BASE_TIME,
        signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
        raw_inputs=(
            ForecastRawInput(
                record_id="raw-input-0200",
                field_name="adjusted_close",
                value=Decimal("100"),
                available_at=_BASE_TIME,
                content_sha256=_SHA_A,
            ),
        ),
        warm_up_complete=True,
        forecast_value=Decimal("1"),
        absence_reason=None,
        signal_version="tsmom-v1",
    )
    with pytest.raises(SignalDataError, match="share the portfolio"):
        SignalPortfolio(
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            signals=(first, stale_signal),
            family_attributions=(attribution,),
        )
    outside_signal = ForecastSignal(
        instrument_id="ZZZ",
        decision_cutoff_at=_CUTOFF,
        signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
        raw_inputs=(
            ForecastRawInput(
                record_id="raw-input-0201",
                field_name="adjusted_close",
                value=Decimal("100"),
                available_at=_BASE_TIME,
                content_sha256=_SHA_A,
            ),
        ),
        warm_up_complete=True,
        forecast_value=Decimal("1"),
        absence_reason=None,
        signal_version="tsmom-v1",
    )
    with pytest.raises(SignalDataError, match="outside its frozen universe"):
        SignalPortfolio(
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            signals=(outside_signal,),
            family_attributions=(
                FamilyAttribution(
                    signal_family=SignalFamily.TIME_SERIES_MOMENTUM,
                    blend_weight=Decimal("1"),
                    signal_count=1,
                ),
            ),
        )


def test_baseline_contract_rejects_wrong_readiness_versions_and_allocations() -> None:
    universe = _universe()
    equal_allocations = tuple(
        BaselineAllocation(instrument_id=instrument_id, target_exposure=Decimal("1") / 3)
        for instrument_id in universe.instrument_ids
    )
    cash_allocations = tuple(
        BaselineAllocation(instrument_id=instrument_id, target_exposure=Decimal("0"))
        for instrument_id in universe.instrument_ids
    )
    with pytest.raises(SignalDataError, match="ready baseline"):
        BaselinePortfolio(
            baseline=PermanentBaseline.TREND_ONLY,
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            allocations=equal_allocations,
            warm_up_complete=True,
            absence_reason=ForecastAbsenceReason.INSUFFICIENT_HISTORY,
        )
    with pytest.raises(SignalDataError, match="unready baseline"):
        BaselinePortfolio(
            baseline=PermanentBaseline.TREND_ONLY,
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            allocations=equal_allocations,
            warm_up_complete=False,
            absence_reason=None,
        )
    with pytest.raises(SignalDataError, match="source_signal_versions"):
        BaselinePortfolio(
            baseline=PermanentBaseline.TREND_ONLY,
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            allocations=equal_allocations,
            warm_up_complete=True,
            absence_reason=None,
            source_signal_versions=("!",),
        )
    with pytest.raises(SignalDataError, match="only trend-only"):
        BaselinePortfolio(
            baseline=PermanentBaseline.CASH,
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            allocations=cash_allocations,
            warm_up_complete=True,
            absence_reason=None,
            source_signal_versions=("tsmom-v1",),
        )
    with pytest.raises(SignalDataError, match="equal-weight"):
        BaselinePortfolio(
            baseline=PermanentBaseline.EQUAL_WEIGHT,
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            allocations=cash_allocations,
            warm_up_complete=True,
            absence_reason=None,
        )
    with pytest.raises(SignalDataError, match="cash baseline"):
        BaselinePortfolio(
            baseline=PermanentBaseline.CASH,
            universe=universe,
            decision_cutoff_at=_CUTOFF,
            allocations=equal_allocations,
            warm_up_complete=True,
            absence_reason=None,
        )


def test_remaining_small_contract_validation_paths_are_fail_closed() -> None:
    with pytest.raises(SignalDataError, match="field_name"):
        ForecastRawInput(
            record_id="raw-input-0300",
            field_name="",
            value=Decimal("1"),
            available_at=_BASE_TIME,
            content_sha256=_SHA_A,
        )
    with pytest.raises(SignalDataError, match="at least two"):
        FrozenUniverse(
            universe_id="etf-universe-v1",
            instrument_ids=("AAA",),
            available_at=_BASE_TIME,
            universe_sha256=_SHA_A,
        )
    with pytest.raises(SignalDataError, match="estimation_window"):
        VolatilityScalingConfig(
            target_annualized_volatility=Decimal("0.1"),
            estimation_window_bars=1,
            minimum_observations=2,
            max_leverage=Decimal("1"),
        )
