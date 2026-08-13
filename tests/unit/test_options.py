from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantum_trader.domain.options import (
    BlackScholesInputs,
    DefinedRiskOptionStrategy,
    DeliverableType,
    ExerciseStyle,
    OptionContract,
    OptionContractAdjustment,
    OptionContractStatus,
    OptionDeliverable,
    OptionFill,
    OptionLeg,
    OptionLegPosition,
    OptionLifecycleEvent,
    OptionLifecycleEventType,
    OptionPositionStatus,
    OptionRight,
    OptionsDomainError,
    OptionStrategyPosition,
    OptionStructure,
    OptionTradeAction,
    PositionSide,
    SettlementType,
    ValuationSource,
    apply_option_fill,
    apply_option_lifecycle,
    black_scholes_greeks,
    initialize_option_position,
)
from quantum_trader.domain.research_data import (
    AssetClass,
    DataAvailability,
    DataProvenance,
    RecordIdentity,
    SecurityIdentity,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_BASE_TIME = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)


def _provenance() -> DataProvenance:
    return DataProvenance(
        provider="fixture",
        dataset="option-contracts",
        provider_schema_version="v1",
        source_uri="fixture://options",
        license_class="synthetic",
        redistribution_allowed=True,
        raw_sha256=_SHA_A,
        query_sha256=_SHA_B,
        transform_version="test-v1",
    )


def _contract(
    *,
    instrument_id: str = "OPT-SPY-C100",
    right: OptionRight = OptionRight.CALL,
    strike: str = "100",
    status: OptionContractStatus = OptionContractStatus.ACTIVE,
    multiplier: str = "100",
    expiration_days: int = 30,
) -> OptionContract:
    expiration = _BASE_TIME + timedelta(days=expiration_days)
    return OptionContract(
        identity=RecordIdentity(record_id=f"record-{instrument_id}"),
        security=SecurityIdentity(
            instrument_id=instrument_id,
            asset_class=AssetClass.OPTION,
            currency="usd",
        ),
        underlying_security=SecurityIdentity(
            instrument_id="SPY",
            asset_class=AssetClass.ETF,
            currency="usd",
            symbol="SPY",
        ),
        availability=DataAvailability(
            event_at=_BASE_TIME,
            available_at=_BASE_TIME,
            captured_at=_BASE_TIME,
        ),
        provenance=_provenance(),
        occ_symbol="SPY   240621C00500000",
        root_symbol="SPY",
        right=right,
        strike=Decimal(strike),
        expiration_at=expiration,
        last_trade_at=expiration - timedelta(minutes=1),
        contract_multiplier=Decimal(multiplier),
        exercise_style=ExerciseStyle.AMERICAN,
        settlement_type=SettlementType.PHYSICAL,
        settlement_currency="usd",
        deliverable_version="standard-v1",
        deliverables=(
            OptionDeliverable(
                deliverable_type=DeliverableType.SECURITY,
                instrument_id="SPY",
                quantity=Decimal(multiplier),
            ),
        ),
        status=status,
    )


def _sources() -> tuple[ValuationSource, ValuationSource, ValuationSource, ValuationSource]:
    return (
        ValuationSource("option-quote-0001", _BASE_TIME, _SHA_A),
        ValuationSource("spot-quote-00001", _BASE_TIME, _SHA_A),
        ValuationSource("rate-curve-0001", _BASE_TIME, _SHA_A),
        ValuationSource("dividend-00001", _BASE_TIME, _SHA_A),
    )


def _bsm_inputs(*, calculation_at: datetime | None = None) -> BlackScholesInputs:
    quote, spot, rate, dividend = _sources()
    return BlackScholesInputs(
        calculation_at=calculation_at or _BASE_TIME + timedelta(days=1),
        observed_option_price=Decimal("6.50"),
        underlying_price=Decimal("100"),
        risk_free_rate=Decimal("0.05"),
        dividend_yield=Decimal("0.02"),
        implied_volatility=Decimal("0.20"),
        time_to_expiration_years=Decimal("0.5"),
        option_quote_source=quote,
        underlying_source=spot,
        rate_source=rate,
        dividend_source=dividend,
    )


def test_option_contract_and_bsm_greeks_are_causal_and_explicit_about_american_limits() -> None:
    contract = _contract()
    greeks = black_scholes_greeks(contract=contract, inputs=_bsm_inputs())
    assert greeks.contract_id == contract.security.instrument_id
    assert greeks.model_price > Decimal("0")
    assert Decimal("0") < greeks.delta < Decimal("1")
    assert greeks.gamma > Decimal("0")
    assert greeks.vega > Decimal("0")
    assert greeks.rho > Decimal("0")
    assert greeks.limitation_flags == ("european_exercise_assumption",)
    assert greeks.price_residual == greeks.model_price - Decimal("6.50")

    with pytest.raises(OptionsDomainError, match="unavailable"):
        _bsm_inputs(calculation_at=_BASE_TIME - timedelta(seconds=1))
    with pytest.raises(OptionsDomainError, match="option contract security"):
        OptionContract(
            identity=RecordIdentity(record_id="record-not-option"),
            security=SecurityIdentity("SPY", AssetClass.ETF, "usd"),
            underlying_security=SecurityIdentity("QQQ", AssetClass.ETF, "usd"),
            availability=DataAvailability(_BASE_TIME, _BASE_TIME, _BASE_TIME),
            provenance=_provenance(),
            occ_symbol="SPY   240621C00500000",
            root_symbol="SPY",
            right=OptionRight.CALL,
            strike=Decimal("100"),
            expiration_at=_BASE_TIME + timedelta(days=30),
            last_trade_at=_BASE_TIME + timedelta(days=29),
            contract_multiplier=Decimal("100"),
            exercise_style=ExerciseStyle.AMERICAN,
            settlement_type=SettlementType.PHYSICAL,
            settlement_currency="USD",
            deliverable_version="standard-v1",
            deliverables=(
                OptionDeliverable(DeliverableType.SECURITY, Decimal("100"), instrument_id="SPY"),
            ),
            status=OptionContractStatus.ACTIVE,
        )


def test_defined_risk_strategy_validation_rejects_naked_and_mismatched_structures() -> None:
    call_100 = _contract(instrument_id="OPT-SPY-C100", strike="100")
    call_110 = _contract(instrument_id="OPT-SPY-C110", strike="110")
    put_90 = _contract(instrument_id="OPT-SPY-P090", right=OptionRight.PUT, strike="90")
    long_call = DefinedRiskOptionStrategy(
        strategy_id="long-call-v1",
        structure=OptionStructure.LONG_CALL,
        legs=(OptionLeg("long-call-100", call_100, PositionSide.LONG, 1),),
    )
    assert long_call.maximum_width_cash is None
    vertical = DefinedRiskOptionStrategy(
        strategy_id="call-debit-v1",
        structure=OptionStructure.VERTICAL_DEBIT_SPREAD,
        legs=(
            OptionLeg("long-call-100", call_100, PositionSide.LONG, 1),
            OptionLeg("short-call-110", call_110, PositionSide.SHORT, 1),
        ),
    )
    assert vertical.maximum_width_cash == Decimal("1000")
    covered_call = DefinedRiskOptionStrategy(
        strategy_id="covered-call-v1",
        structure=OptionStructure.COVERED_CALL,
        legs=(OptionLeg("short-call-100", call_100, PositionSide.SHORT, 1),),
        covered_underlying_shares=Decimal("100"),
    )
    assert covered_call.covered_underlying_shares == Decimal("100")
    cash_secured_put = DefinedRiskOptionStrategy(
        strategy_id="cash-put-v1",
        structure=OptionStructure.CASH_SECURED_PUT,
        legs=(OptionLeg("short-put-90", put_90, PositionSide.SHORT, 1),),
        cash_collateral=Decimal("9000"),
    )
    assert cash_secured_put.cash_collateral == Decimal("9000")

    with pytest.raises(OptionsDomainError, match="exactly two option legs"):
        DefinedRiskOptionStrategy(
            strategy_id="naked-short-attempt",
            structure=OptionStructure.VERTICAL_CREDIT_SPREAD,
            legs=(OptionLeg("short-call-100", call_100, PositionSide.SHORT, 1),),
        )
    with pytest.raises(OptionsDomainError, match="sufficient underlying share"):
        DefinedRiskOptionStrategy(
            strategy_id="undercovered-call",
            structure=OptionStructure.COVERED_CALL,
            legs=(OptionLeg("short-call-100", call_100, PositionSide.SHORT, 1),),
            covered_underlying_shares=Decimal("99"),
        )
    with pytest.raises(OptionsDomainError, match="full strike collateral"):
        DefinedRiskOptionStrategy(
            strategy_id="undersecured-put",
            structure=OptionStructure.CASH_SECURED_PUT,
            legs=(OptionLeg("short-put-90", put_90, PositionSide.SHORT, 1),),
            cash_collateral=Decimal("8999"),
        )


def test_vertical_partial_fills_preserve_defined_risk_and_reconcile_cashflows() -> None:
    call_100 = _contract(instrument_id="OPT-SPY-C100", strike="100")
    call_110 = _contract(instrument_id="OPT-SPY-C110", strike="110")
    strategy = DefinedRiskOptionStrategy(
        strategy_id="call-credit-v1",
        structure=OptionStructure.VERTICAL_CREDIT_SPREAD,
        legs=(
            OptionLeg("short-call-100", call_100, PositionSide.SHORT, 1),
            OptionLeg("long-call-110", call_110, PositionSide.LONG, 1),
        ),
    )
    initial = initialize_option_position(strategy=strategy)
    assert initial.status is OptionPositionStatus.NEW
    with pytest.raises(OptionsDomainError, match="unprotected"):
        apply_option_fill(
            position=initial,
            fill=OptionFill(
                "fill-short-first",
                "short-call-100",
                OptionTradeAction.SELL_TO_OPEN,
                _BASE_TIME,
                1,
                Decimal("3"),
                Decimal("1"),
            ),
        )
    long_open = apply_option_fill(
        position=initial,
        fill=OptionFill(
            "fill-long-open",
            "long-call-110",
            OptionTradeAction.BUY_TO_OPEN,
            _BASE_TIME,
            1,
            Decimal("1"),
            Decimal("1"),
        ),
    )
    assert long_open.status is OptionPositionStatus.PARTIALLY_OPEN
    fully_open = apply_option_fill(
        position=long_open,
        fill=OptionFill(
            "fill-short-open",
            "short-call-100",
            OptionTradeAction.SELL_TO_OPEN,
            _BASE_TIME,
            1,
            Decimal("3"),
            Decimal("1"),
        ),
    )
    assert fully_open.status is OptionPositionStatus.OPEN
    assert fully_open.premium_cashflow == Decimal("200")
    assert fully_open.fees == Decimal("2")
    assert fully_open.net_cashflow == Decimal("198")
    partially_closed = apply_option_fill(
        position=fully_open,
        fill=OptionFill(
            "fill-short-close",
            "short-call-100",
            OptionTradeAction.BUY_TO_CLOSE,
            _BASE_TIME,
            1,
            Decimal("2"),
            Decimal("1"),
        ),
    )
    assert partially_closed.status is OptionPositionStatus.PARTIALLY_CLOSED
    assert partially_closed.premium_cashflow == Decimal("0")
    assert partially_closed.net_cashflow == Decimal("-3")
    with pytest.raises(OptionsDomainError, match="more contracts"):
        apply_option_fill(
            position=partially_closed,
            fill=OptionFill(
                "fill-over-close",
                "short-call-100",
                OptionTradeAction.BUY_TO_CLOSE,
                _BASE_TIME,
                1,
                Decimal("2"),
                Decimal("0"),
            ),
        )


def test_exercise_assignment_and_expiry_move_cash_shares_and_close_only_the_affected_leg() -> None:
    call = _contract()
    covered = DefinedRiskOptionStrategy(
        strategy_id="covered-call-v1",
        structure=OptionStructure.COVERED_CALL,
        legs=(OptionLeg("short-call-100", call, PositionSide.SHORT, 1),),
        covered_underlying_shares=Decimal("100"),
    )
    position = apply_option_fill(
        position=initialize_option_position(strategy=covered),
        fill=OptionFill(
            "fill-covered-open",
            "short-call-100",
            OptionTradeAction.SELL_TO_OPEN,
            _BASE_TIME,
            1,
            Decimal("2"),
            Decimal("1"),
        ),
    )
    assigned = apply_option_lifecycle(
        position=position,
        event=OptionLifecycleEvent(
            "event-covered-assignment",
            "short-call-100",
            OptionLifecycleEventType.ASSIGNMENT,
            _BASE_TIME + timedelta(days=10),
            _BASE_TIME + timedelta(days=10),
            1,
            underlying_price=Decimal("120"),
        ),
    )
    assert assigned.status is OptionPositionStatus.CLOSED
    assert assigned.underlying_shares == Decimal("0")
    assert assigned.lifecycle_cashflow == Decimal("10000")
    assert assigned.net_cashflow == Decimal("10199")

    put = _contract(instrument_id="OPT-SPY-P100", right=OptionRight.PUT, strike="100")
    secured_put = DefinedRiskOptionStrategy(
        strategy_id="cash-put-v1",
        structure=OptionStructure.CASH_SECURED_PUT,
        legs=(OptionLeg("short-put-100", put, PositionSide.SHORT, 1),),
        cash_collateral=Decimal("10000"),
    )
    put_position = apply_option_fill(
        position=initialize_option_position(strategy=secured_put),
        fill=OptionFill(
            "fill-put-open",
            "short-put-100",
            OptionTradeAction.SELL_TO_OPEN,
            _BASE_TIME,
            1,
            Decimal("2"),
            Decimal("0"),
        ),
    )
    put_assignment = apply_option_lifecycle(
        position=put_position,
        event=OptionLifecycleEvent(
            "event-put-assignment",
            "short-put-100",
            OptionLifecycleEventType.ASSIGNMENT,
            _BASE_TIME + timedelta(days=10),
            _BASE_TIME + timedelta(days=10),
            1,
            underlying_price=Decimal("80"),
        ),
    )
    assert put_assignment.underlying_shares == Decimal("100")
    assert put_assignment.lifecycle_cashflow == Decimal("-10000")

    long_put = DefinedRiskOptionStrategy(
        strategy_id="long-put-v1",
        structure=OptionStructure.LONG_PUT,
        legs=(OptionLeg("long-put-100", put, PositionSide.LONG, 1),),
    )
    long_put_position = apply_option_fill(
        position=initialize_option_position(strategy=long_put),
        fill=OptionFill(
            "fill-long-put",
            "long-put-100",
            OptionTradeAction.BUY_TO_OPEN,
            _BASE_TIME,
            1,
            Decimal("2"),
            Decimal("0"),
        ),
    )
    expired = apply_option_lifecycle(
        position=long_put_position,
        event=OptionLifecycleEvent(
            "event-long-put-expiry",
            "long-put-100",
            OptionLifecycleEventType.EXPIRE_WORTHLESS,
            put.expiration_at,
            put.expiration_at,
            1,
            underlying_price=Decimal("110"),
        ),
    )
    assert expired.status is OptionPositionStatus.CLOSED
    assert expired.lifecycle_cashflow == Decimal("0")
    with pytest.raises(OptionsDomainError, match="in-the-money"):
        apply_option_lifecycle(
            position=long_put_position,
            event=OptionLifecycleEvent(
                "event-itm-expiry",
                "long-put-100",
                OptionLifecycleEventType.EXPIRE_WORTHLESS,
                put.expiration_at,
                put.expiration_at,
                1,
                underlying_price=Decimal("80"),
            ),
        )


def test_lifecycle_event_and_adjustment_receipts_are_explicit_and_idempotent() -> None:
    original = _contract()
    adjusted = _contract(
        instrument_id="OPT-SPY-C100-ADJ",
        status=OptionContractStatus.ADJUSTED,
        multiplier="50",
    )
    adjustment = OptionContractAdjustment(
        adjustment_id="adjustment-0001",
        original_contract_id=original.security.instrument_id,
        adjusted_contract=adjusted,
        effective_at=_BASE_TIME + timedelta(days=5),
        available_at=_BASE_TIME + timedelta(days=5),
        occ_memo_sha256=_SHA_A,
    )
    strategy = DefinedRiskOptionStrategy(
        strategy_id="long-call-adjustment-v1",
        structure=OptionStructure.LONG_CALL,
        legs=(OptionLeg("long-call-100", original, PositionSide.LONG, 1),),
    )
    position = apply_option_fill(
        position=initialize_option_position(strategy=strategy),
        fill=OptionFill(
            "fill-adjustment-open",
            "long-call-100",
            OptionTradeAction.BUY_TO_OPEN,
            _BASE_TIME,
            1,
            Decimal("2"),
            Decimal("0"),
        ),
    )
    event = OptionLifecycleEvent(
        "event-adjustment",
        "long-call-100",
        OptionLifecycleEventType.CONTRACT_ADJUSTMENT,
        adjustment.effective_at,
        adjustment.available_at,
        1,
        adjustment=adjustment,
    )
    adjusted_position = apply_option_lifecycle(position=position, event=event)
    assert adjusted_position.leg_positions == position.leg_positions
    assert adjusted_position.lifecycle_events == (event,)
    with pytest.raises(OptionsDomainError, match="already been applied"):
        apply_option_lifecycle(position=adjusted_position, event=event)
    with pytest.raises(OptionsDomainError, match="only short option legs"):
        apply_option_lifecycle(
            position=position,
            event=OptionLifecycleEvent(
                "event-invalid-exercise",
                "long-call-100",
                OptionLifecycleEventType.ASSIGNMENT,
                _BASE_TIME + timedelta(days=5),
                _BASE_TIME + timedelta(days=5),
                1,
                underlying_price=Decimal("110"),
            ),
        )


def test_deliverable_contract_and_valuation_input_guards_are_fail_closed() -> None:
    with pytest.raises(OptionsDomainError, match="require instrument_id"):
        OptionDeliverable(DeliverableType.SECURITY, Decimal("1"))
    with pytest.raises(OptionsDomainError, match="cannot declare cash currency"):
        OptionDeliverable(
            DeliverableType.SECURITY,
            Decimal("1"),
            instrument_id="SPY",
            currency="USD",
        )
    with pytest.raises(OptionsDomainError, match="cannot declare instrument_id"):
        OptionDeliverable(DeliverableType.CASH, Decimal("1"), instrument_id="SPY", currency="USD")
    with pytest.raises(OptionsDomainError, match="identifier or description"):
        OptionDeliverable(DeliverableType.OTHER, Decimal("1"))

    base = _contract()
    with pytest.raises(OptionsDomainError, match="underlying"):
        replace(
            base,
            underlying_security=SecurityIdentity("CASH", AssetClass.CASH, "USD"),
        )
    with pytest.raises(OptionsDomainError, match="last_trade_at"):
        replace(base, last_trade_at=base.expiration_at + timedelta(seconds=1))
    with pytest.raises(OptionsDomainError, match="settlement currency"):
        replace(base, settlement_currency="EUR")
    with pytest.raises(OptionsDomainError, match="physical settlement"):
        replace(
            base,
            deliverables=(OptionDeliverable(DeliverableType.CASH, Decimal("1"), currency="USD"),),
        )
    with pytest.raises(OptionsDomainError, match="cash settlement"):
        replace(base, settlement_type=SettlementType.CASH)

    quote, _, rate, _ = _sources()
    with pytest.raises(OptionsDomainError, match="record_ids must be unique"):
        BlackScholesInputs(
            calculation_at=_BASE_TIME,
            observed_option_price=Decimal("1"),
            underlying_price=Decimal("100"),
            risk_free_rate=Decimal("0"),
            dividend_yield=Decimal("0"),
            implied_volatility=Decimal("0.2"),
            time_to_expiration_years=Decimal("0.5"),
            option_quote_source=quote,
            underlying_source=quote,
            rate_source=rate,
            dividend_source=ValuationSource("dividend-00002", _BASE_TIME, _SHA_A),
        )


def test_put_european_greeks_expired_pricing_and_strategy_shape_guards() -> None:
    put = _contract(instrument_id="OPT-SPY-P100", right=OptionRight.PUT, strike="100")
    european_put = replace(put, exercise_style=ExerciseStyle.EUROPEAN)
    greeks = black_scholes_greeks(contract=european_put, inputs=_bsm_inputs())
    assert greeks.delta < Decimal("0")
    assert greeks.rho < Decimal("0")
    assert greeks.limitation_flags == ()
    with pytest.raises(OptionsDomainError, match="after its expiration"):
        black_scholes_greeks(
            contract=put,
            inputs=_bsm_inputs(calculation_at=put.expiration_at + timedelta(seconds=1)),
        )
    with pytest.raises(OptionsDomainError, match="contracts must be positive"):
        OptionLeg("invalid-leg", put, PositionSide.LONG, 0)
    with pytest.raises(OptionsDomainError, match="leg_ids must be unique"):
        DefinedRiskOptionStrategy(
            strategy_id="duplicate-leg-v1",
            structure=OptionStructure.LONG_PUT,
            legs=(
                OptionLeg("dup-leg", put, PositionSide.LONG, 1),
                OptionLeg("dup-leg", put, PositionSide.LONG, 1),
            ),
        )
    with pytest.raises(OptionsDomainError, match="single-long strategy"):
        DefinedRiskOptionStrategy(
            strategy_id="long-put-coverage-v1",
            structure=OptionStructure.LONG_PUT,
            legs=(OptionLeg("long-put", put, PositionSide.LONG, 1),),
            cash_collateral=Decimal("1"),
        )


def test_vertical_and_collateral_validation_cover_mismatches_and_direction_errors() -> None:
    call_100 = _contract(instrument_id="OPT-SPY-C100", strike="100")
    call_110 = _contract(instrument_id="OPT-SPY-C110", strike="110")
    put_100 = _contract(instrument_id="OPT-SPY-P100", right=OptionRight.PUT, strike="100")
    with pytest.raises(OptionsDomainError, match="equal contract"):
        DefinedRiskOptionStrategy(
            strategy_id="unequal-vertical-v1",
            structure=OptionStructure.VERTICAL_DEBIT_SPREAD,
            legs=(
                OptionLeg("long-call", call_100, PositionSide.LONG, 2),
                OptionLeg("short-call", call_110, PositionSide.SHORT, 1),
            ),
        )
    with pytest.raises(OptionsDomainError, match="share a right"):
        DefinedRiskOptionStrategy(
            strategy_id="mixed-right-vertical-v1",
            structure=OptionStructure.VERTICAL_DEBIT_SPREAD,
            legs=(
                OptionLeg("long-call", call_100, PositionSide.LONG, 1),
                OptionLeg("short-put", put_100, PositionSide.SHORT, 1),
            ),
        )
    with pytest.raises(OptionsDomainError, match="inconsistent with strikes"):
        DefinedRiskOptionStrategy(
            strategy_id="wrong-credit-direction-v1",
            structure=OptionStructure.VERTICAL_CREDIT_SPREAD,
            legs=(
                OptionLeg("long-call", call_100, PositionSide.LONG, 1),
                OptionLeg("short-call", call_110, PositionSide.SHORT, 1),
            ),
        )
    with pytest.raises(OptionsDomainError, match="standalone coverage"):
        DefinedRiskOptionStrategy(
            strategy_id="vertical-collateral-v1",
            structure=OptionStructure.VERTICAL_DEBIT_SPREAD,
            legs=(
                OptionLeg("long-call", call_100, PositionSide.LONG, 1),
                OptionLeg("short-call", call_110, PositionSide.SHORT, 1),
            ),
            cash_collateral=Decimal("1"),
        )
    with pytest.raises(OptionsDomainError, match="covered call requires one short call"):
        DefinedRiskOptionStrategy(
            strategy_id="wrong-covered-v1",
            structure=OptionStructure.COVERED_CALL,
            legs=(OptionLeg("long-call", call_100, PositionSide.LONG, 1),),
            covered_underlying_shares=Decimal("100"),
        )
    with pytest.raises(OptionsDomainError, match="cash-secured put requires one short put"):
        DefinedRiskOptionStrategy(
            strategy_id="wrong-cash-put-v1",
            structure=OptionStructure.CASH_SECURED_PUT,
            legs=(OptionLeg("short-call", call_100, PositionSide.SHORT, 1),),
            cash_collateral=Decimal("10000"),
        )


def test_position_and_fill_contracts_reject_nonreconciling_or_duplicate_state() -> None:
    contract = _contract()
    strategy = DefinedRiskOptionStrategy(
        strategy_id="long-call-position-v1",
        structure=OptionStructure.LONG_CALL,
        legs=(OptionLeg("long-call", contract, PositionSide.LONG, 1),),
    )
    with pytest.raises(OptionsDomainError, match="canonical strategy leg order"):
        OptionStrategyPosition(strategy=strategy, leg_positions=())
    with pytest.raises(OptionsDomainError, match="exceeds predeclared"):
        OptionStrategyPosition(
            strategy=strategy,
            leg_positions=(OptionLegPosition("long-call", 2),),
        )
    with pytest.raises(OptionsDomainError, match="fees must be nonnegative"):
        OptionStrategyPosition(
            strategy=strategy,
            leg_positions=(OptionLegPosition("long-call", 0),),
            fees=Decimal("-1"),
        )
    with pytest.raises(OptionsDomainError, match="fill contracts"):
        OptionFill(
            "fill-invalid",
            "long-call",
            OptionTradeAction.BUY_TO_OPEN,
            _BASE_TIME,
            0,
            Decimal("1"),
            Decimal("0"),
        )
    initial = initialize_option_position(strategy=strategy)
    with pytest.raises(OptionsDomainError, match="incompatible"):
        apply_option_fill(
            position=initial,
            fill=OptionFill(
                "fill-bad-side",
                "long-call",
                OptionTradeAction.SELL_TO_OPEN,
                _BASE_TIME,
                1,
                Decimal("1"),
                Decimal("0"),
            ),
        )
    opened = apply_option_fill(
        position=initial,
        fill=OptionFill(
            "fill-good-open",
            "long-call",
            OptionTradeAction.BUY_TO_OPEN,
            _BASE_TIME,
            1,
            Decimal("1"),
            Decimal("0"),
        ),
    )
    with pytest.raises(OptionsDomainError, match="fill ids must be unique"):
        apply_option_fill(
            position=opened,
            fill=OptionFill(
                "fill-good-open",
                "long-call",
                OptionTradeAction.SELL_TO_CLOSE,
                _BASE_TIME,
                1,
                Decimal("1"),
                Decimal("0"),
            ),
        )


def test_lifecycle_time_type_settlement_and_adjustment_mismatch_guards() -> None:
    call = _contract()
    long_call = DefinedRiskOptionStrategy(
        strategy_id="long-call-lifecycle-v1",
        structure=OptionStructure.LONG_CALL,
        legs=(OptionLeg("long-call", call, PositionSide.LONG, 1),),
    )
    position = apply_option_fill(
        position=initialize_option_position(strategy=long_call),
        fill=OptionFill(
            "fill-lifecycle-open",
            "long-call",
            OptionTradeAction.BUY_TO_OPEN,
            _BASE_TIME,
            1,
            Decimal("1"),
            Decimal("0"),
        ),
    )
    exercised = apply_option_lifecycle(
        position=position,
        event=OptionLifecycleEvent(
            "event-long-exercise",
            "long-call",
            OptionLifecycleEventType.EXERCISE,
            _BASE_TIME + timedelta(days=5),
            _BASE_TIME + timedelta(days=5),
            1,
            underlying_price=Decimal("120"),
        ),
    )
    assert exercised.underlying_shares == Decimal("100")
    assert exercised.lifecycle_cashflow == Decimal("-10000")
    with pytest.raises(OptionsDomainError, match="exceeds open"):
        apply_option_lifecycle(
            position=position,
            event=OptionLifecycleEvent(
                "event-too-many",
                "long-call",
                OptionLifecycleEventType.EXERCISE,
                _BASE_TIME + timedelta(days=5),
                _BASE_TIME + timedelta(days=5),
                2,
                underlying_price=Decimal("120"),
            ),
        )
    with pytest.raises(OptionsDomainError, match="cannot precede"):
        apply_option_lifecycle(
            position=position,
            event=OptionLifecycleEvent(
                "event-early-expiry",
                "long-call",
                OptionLifecycleEventType.EXPIRE_WORTHLESS,
                _BASE_TIME,
                _BASE_TIME,
                1,
                underlying_price=Decimal("90"),
            ),
        )

    cash_contract = replace(
        call,
        settlement_type=SettlementType.CASH,
        deliverables=(OptionDeliverable(DeliverableType.CASH, Decimal("1"), currency="USD"),),
    )
    cash_strategy = DefinedRiskOptionStrategy(
        strategy_id="cash-call-v1",
        structure=OptionStructure.LONG_CALL,
        legs=(OptionLeg("cash-call", cash_contract, PositionSide.LONG, 1),),
    )
    cash_position = apply_option_fill(
        position=initialize_option_position(strategy=cash_strategy),
        fill=OptionFill(
            "fill-cash-open",
            "cash-call",
            OptionTradeAction.BUY_TO_OPEN,
            _BASE_TIME,
            1,
            Decimal("1"),
            Decimal("0"),
        ),
    )
    cash_exercise = apply_option_lifecycle(
        position=cash_position,
        event=OptionLifecycleEvent(
            "event-cash-exercise",
            "cash-call",
            OptionLifecycleEventType.EXERCISE,
            _BASE_TIME + timedelta(days=5),
            _BASE_TIME + timedelta(days=5),
            1,
            underlying_price=Decimal("120"),
        ),
    )
    assert cash_exercise.lifecycle_cashflow == Decimal("2000")
    assert cash_exercise.underlying_shares == Decimal("0")

    adjusted = _contract(
        instrument_id="OPT-SPY-C100-ADJ2",
        status=OptionContractStatus.ADJUSTED,
    )
    receipt = OptionContractAdjustment(
        "adjustment-0002",
        call.security.instrument_id,
        adjusted,
        _BASE_TIME + timedelta(days=5),
        _BASE_TIME + timedelta(days=5),
        _SHA_A,
    )
    with pytest.raises(OptionsDomainError, match="does not reference"):
        apply_option_lifecycle(
            position=position,
            event=OptionLifecycleEvent(
                "event-wrong-adjustment",
                "long-call",
                OptionLifecycleEventType.CONTRACT_ADJUSTMENT,
                receipt.effective_at,
                receipt.available_at,
                1,
                adjustment=replace(receipt, original_contract_id="OPT-WRONG"),
            ),
        )


def test_remaining_constructor_receipt_and_event_guards_fail_closed() -> None:
    base = _contract()
    with pytest.raises(OptionsDomainError, match="occ_symbol"):
        replace(base, occ_symbol="bad")
    with pytest.raises(OptionsDomainError, match="root_symbol"):
        replace(base, root_symbol="BAD-ROOT")
    with pytest.raises(OptionsDomainError, match="deliverable_version"):
        replace(base, deliverable_version="!")
    with pytest.raises(OptionsDomainError, match="at least one deliverable"):
        replace(base, deliverables=())
    adjusted = _contract(
        instrument_id="OPT-SPY-C100-ADJ3",
        status=OptionContractStatus.ADJUSTED,
    )
    with pytest.raises(OptionsDomainError, match="cannot precede"):
        OptionContractAdjustment(
            "adjustment-0003",
            base.security.instrument_id,
            adjusted,
            _BASE_TIME + timedelta(days=2),
            _BASE_TIME + timedelta(days=1),
            _SHA_A,
        )
    with pytest.raises(OptionsDomainError, match="requires adjusted"):
        OptionContractAdjustment(
            "adjustment-0004",
            base.security.instrument_id,
            base,
            _BASE_TIME,
            _BASE_TIME,
            _SHA_A,
        )
    with pytest.raises(OptionsDomainError, match="model_version"):
        replace(_bsm_inputs(), model_version="!")
    with pytest.raises(OptionsDomainError, match="available_at cannot precede"):
        OptionLifecycleEvent(
            "event-availability",
            "some-leg",
            OptionLifecycleEventType.EXERCISE,
            _BASE_TIME + timedelta(days=1),
            _BASE_TIME,
            1,
            underlying_price=Decimal("1"),
        )
    with pytest.raises(OptionsDomainError, match="require underlying_price"):
        OptionLifecycleEvent(
            "event-no-spot",
            "some-leg",
            OptionLifecycleEventType.EXERCISE,
            _BASE_TIME,
            _BASE_TIME,
            1,
        )
    with pytest.raises(OptionsDomainError, match="requires adjustment only"):
        OptionLifecycleEvent(
            "event-bad-adjustment",
            "some-leg",
            OptionLifecycleEventType.CONTRACT_ADJUSTMENT,
            _BASE_TIME,
            _BASE_TIME,
            1,
            underlying_price=Decimal("1"),
        )


def test_remaining_strategy_position_and_settlement_guards_fail_closed() -> None:
    call_100 = _contract(instrument_id="OPT-SPY-C100", strike="100")
    call_110 = _contract(instrument_id="OPT-SPY-C110", strike="110")
    qqq_call = replace(
        call_110,
        security=SecurityIdentity("OPT-QQQ-C110", AssetClass.OPTION, "USD"),
        underlying_security=SecurityIdentity("QQQ", AssetClass.ETF, "USD"),
    )
    with pytest.raises(OptionsDomainError, match="share an underlying"):
        DefinedRiskOptionStrategy(
            strategy_id="different-underlying-v1",
            structure=OptionStructure.VERTICAL_DEBIT_SPREAD,
            legs=(
                OptionLeg("long-spy", call_100, PositionSide.LONG, 1),
                OptionLeg("short-qqq", qqq_call, PositionSide.SHORT, 1),
            ),
        )
    with pytest.raises(OptionsDomainError, match="share an expiration"):
        DefinedRiskOptionStrategy(
            strategy_id="different-expiry-v1",
            structure=OptionStructure.VERTICAL_DEBIT_SPREAD,
            legs=(
                OptionLeg("long-first", call_100, PositionSide.LONG, 1),
                OptionLeg(
                    "short-later",
                    replace(call_110, expiration_at=call_110.expiration_at + timedelta(days=1)),
                    PositionSide.SHORT,
                    1,
                ),
            ),
        )
    covered = DefinedRiskOptionStrategy(
        strategy_id="covered-position-v1",
        structure=OptionStructure.COVERED_CALL,
        legs=(OptionLeg("short-call", call_100, PositionSide.SHORT, 1),),
        covered_underlying_shares=Decimal("100"),
    )
    with pytest.raises(OptionsDomainError, match="cannot lose shares"):
        OptionStrategyPosition(
            strategy=covered,
            leg_positions=(OptionLegPosition("short-call", 1),),
            underlying_shares=Decimal("0"),
        )
    unknown_cash = replace(call_100, settlement_type=SettlementType.UNKNOWN)
    unknown_strategy = DefinedRiskOptionStrategy(
        strategy_id="unknown-settlement-v1",
        structure=OptionStructure.LONG_CALL,
        legs=(OptionLeg("long-unknown", unknown_cash, PositionSide.LONG, 1),),
    )
    unknown_position = apply_option_fill(
        position=initialize_option_position(strategy=unknown_strategy),
        fill=OptionFill(
            "fill-unknown",
            "long-unknown",
            OptionTradeAction.BUY_TO_OPEN,
            _BASE_TIME,
            1,
            Decimal("1"),
            Decimal("0"),
        ),
    )
    with pytest.raises(OptionsDomainError, match="mixed or unknown"):
        apply_option_lifecycle(
            position=unknown_position,
            event=OptionLifecycleEvent(
                "event-unknown",
                "long-unknown",
                OptionLifecycleEventType.EXERCISE,
                _BASE_TIME + timedelta(days=2),
                _BASE_TIME + timedelta(days=2),
                1,
                underlying_price=Decimal("120"),
            ),
        )
