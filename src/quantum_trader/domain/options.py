"""Defined-risk option instruments, valuation inputs, lifecycle, and multi-leg accounting.

The module is deliberately a research-domain layer.  It models contractual obligations and
accounting consequences, but it does not fetch chains, infer fills, submit orders, or enable
paper/live trading.  Lifecycle events are explicit so an apparent spread-level risk limit never
hides an assignment, residual leg, deliverable adjustment, or cash/share movement.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from math import erf, exp, log, pi, sqrt

from quantum_trader.domain.research_data import (
    AssetClass,
    DataAvailability,
    DataProvenance,
    RecordIdentity,
    SecurityIdentity,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWO = Decimal("2")
_365 = Decimal("365")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$")
_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OptionsDomainError(ValueError):
    """Raised when an option record, strategy, or lifecycle event is unsafe or ambiguous."""


class OptionRight(StrEnum):
    """Contract holder right and writer obligation direction."""

    CALL = "call"
    PUT = "put"


class ExerciseStyle(StrEnum):
    """Contract exercise timing convention retained from the listed series definition."""

    AMERICAN = "american"
    EUROPEAN = "european"
    BERMUDAN = "bermudan"
    UNKNOWN = "unknown"


class SettlementType(StrEnum):
    """How a contract resolves after exercise, assignment, or expiry."""

    PHYSICAL = "physical"
    CASH = "cash"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class OptionContractStatus(StrEnum):
    """Point-in-time listing status; it is not inferred from the current calendar."""

    ACTIVE = "active"
    EXPIRED = "expired"
    ADJUSTED = "adjusted"
    SUSPENDED = "suspended"
    DELISTED = "delisted"


class DeliverableType(StrEnum):
    """Component of an adjusted option deliverable."""

    SECURITY = "security"
    CASH = "cash"
    OTHER = "other"


class PositionSide(StrEnum):
    """Direction of one strategy leg after opening fills."""

    LONG = "long"
    SHORT = "short"


class OptionStructure(StrEnum):
    """Only structures allowed by the frozen initial research policy."""

    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    VERTICAL_DEBIT_SPREAD = "vertical_debit_spread"
    VERTICAL_CREDIT_SPREAD = "vertical_credit_spread"
    COVERED_CALL = "covered_call"
    CASH_SECURED_PUT = "cash_secured_put"


class OptionTradeAction(StrEnum):
    """Explicit opening and closing action for a named option leg."""

    BUY_TO_OPEN = "buy_to_open"
    SELL_TO_OPEN = "sell_to_open"
    BUY_TO_CLOSE = "buy_to_close"
    SELL_TO_CLOSE = "sell_to_close"


class OptionLifecycleEventType(StrEnum):
    """Terminal or adjustment lifecycle action, never silently inferred."""

    EXERCISE = "exercise"
    ASSIGNMENT = "assignment"
    EXPIRE_WORTHLESS = "expire_worthless"
    CONTRACT_ADJUSTMENT = "contract_adjustment"


class OptionPositionStatus(StrEnum):
    """Derived position state after partial fills and lifecycle resolutions."""

    NEW = "new"
    PARTIALLY_OPEN = "partially_open"
    OPEN = "open"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class OptionDeliverable:
    """One security, cash, or other component delivered per exercised contract."""

    deliverable_type: DeliverableType
    quantity: Decimal
    instrument_id: str | None = None
    currency: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        _finite(self.quantity, "deliverable quantity", positive=True)
        if self.deliverable_type is DeliverableType.SECURITY:
            if self.instrument_id is None:
                raise OptionsDomainError("security deliverables require instrument_id")
            object.__setattr__(
                self, "instrument_id", _normalized_identifier(self.instrument_id, "instrument_id")
            )
            if self.currency is not None:
                raise OptionsDomainError("security deliverables cannot declare cash currency")
        elif self.deliverable_type is DeliverableType.CASH:
            if self.instrument_id is not None:
                raise OptionsDomainError("cash deliverables cannot declare instrument_id")
            object.__setattr__(self, "currency", _currency(self.currency, "currency"))
        elif self.instrument_id is None and self.description is None:
            raise OptionsDomainError("other deliverables require an identifier or description")
        if self.description is not None and not self.description.strip():
            raise OptionsDomainError("deliverable description must not be blank when present")


@dataclass(frozen=True, slots=True)
class OptionContract:
    """Immutable point-in-time listed-option identity and contractual deliverable."""

    identity: RecordIdentity
    security: SecurityIdentity
    underlying_security: SecurityIdentity
    availability: DataAvailability
    provenance: DataProvenance
    occ_symbol: str
    root_symbol: str
    right: OptionRight
    strike: Decimal
    expiration_at: datetime
    last_trade_at: datetime
    contract_multiplier: Decimal
    exercise_style: ExerciseStyle
    settlement_type: SettlementType
    settlement_currency: str
    deliverable_version: str
    deliverables: tuple[OptionDeliverable, ...]
    status: OptionContractStatus

    def __post_init__(self) -> None:
        if self.security.asset_class is not AssetClass.OPTION:
            raise OptionsDomainError("option contract security must use AssetClass.OPTION")
        if self.underlying_security.asset_class not in {
            AssetClass.EQUITY,
            AssetClass.ETF,
            AssetClass.INDEX,
        }:
            raise OptionsDomainError("option underlying must be an equity, ETF, or index")
        occ_symbol = self.occ_symbol.strip().upper()
        if not 15 <= len(occ_symbol) <= 30 or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ." for character in occ_symbol
        ):
            raise OptionsDomainError("occ_symbol is invalid")
        root_symbol = self.root_symbol.strip().upper()
        if not 1 <= len(root_symbol) <= 8 or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789." for character in root_symbol
        ):
            raise OptionsDomainError("root_symbol is invalid")
        _finite(self.strike, "strike", positive=True)
        expiration_at = _utc(self.expiration_at, "expiration_at")
        last_trade_at = _utc(self.last_trade_at, "last_trade_at")
        if last_trade_at > expiration_at:
            raise OptionsDomainError("last_trade_at cannot follow expiration_at")
        _finite(self.contract_multiplier, "contract_multiplier", positive=True)
        settlement_currency = _currency(self.settlement_currency, "settlement_currency")
        if settlement_currency != self.security.currency:
            raise OptionsDomainError("settlement currency must match the option security currency")
        if _VERSION.fullmatch(self.deliverable_version) is None:
            raise OptionsDomainError("deliverable_version is invalid")
        if not self.deliverables:
            raise OptionsDomainError("option contract requires at least one deliverable")
        if self.settlement_type is SettlementType.PHYSICAL and not any(
            item.deliverable_type is DeliverableType.SECURITY for item in self.deliverables
        ):
            raise OptionsDomainError("physical settlement requires a security deliverable")
        if self.settlement_type is SettlementType.CASH and not any(
            item.deliverable_type is DeliverableType.CASH for item in self.deliverables
        ):
            raise OptionsDomainError("cash settlement requires a cash deliverable")
        object.__setattr__(self, "occ_symbol", occ_symbol)
        object.__setattr__(self, "root_symbol", root_symbol)
        object.__setattr__(self, "expiration_at", expiration_at)
        object.__setattr__(self, "last_trade_at", last_trade_at)
        object.__setattr__(self, "settlement_currency", settlement_currency)

    @property
    def underlying_deliverable_quantity(self) -> Decimal:
        """Return the security quantity per contract for physical settlement accounting."""

        return sum(
            (
                item.quantity
                for item in self.deliverables
                if item.deliverable_type is DeliverableType.SECURITY
                and item.instrument_id == self.underlying_security.instrument_id
            ),
            _ZERO,
        )


@dataclass(frozen=True, slots=True)
class OptionContractAdjustment:
    """Immutable receipt linking a superseded series to a revised deliverable contract."""

    adjustment_id: str
    original_contract_id: str
    adjusted_contract: OptionContract
    effective_at: datetime
    available_at: datetime
    occ_memo_sha256: str

    def __post_init__(self) -> None:
        _identifier(self.adjustment_id, "adjustment_id")
        _identifier(self.original_contract_id, "original_contract_id")
        effective_at = _utc(self.effective_at, "effective_at")
        available_at = _utc(self.available_at, "available_at")
        if available_at < effective_at:
            raise OptionsDomainError("adjustment available_at cannot precede effective_at")
        if self.adjusted_contract.status is not OptionContractStatus.ADJUSTED:
            raise OptionsDomainError("adjusted contract receipt requires adjusted contract status")
        _sha256(self.occ_memo_sha256, "occ_memo_sha256")
        object.__setattr__(self, "effective_at", effective_at)
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True, slots=True)
class ValuationSource:
    """One causal source record used by an independent valuation calculation."""

    record_id: str
    available_at: datetime
    content_sha256: str

    def __post_init__(self) -> None:
        _identifier(self.record_id, "record_id")
        object.__setattr__(self, "available_at", _utc(self.available_at, "available_at"))
        _sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True, slots=True)
class BlackScholesInputs:
    """Causally retained market inputs for a Black-Scholes-Merton sensitivity calculation."""

    calculation_at: datetime
    observed_option_price: Decimal
    underlying_price: Decimal
    risk_free_rate: Decimal
    dividend_yield: Decimal
    implied_volatility: Decimal
    time_to_expiration_years: Decimal
    option_quote_source: ValuationSource
    underlying_source: ValuationSource
    rate_source: ValuationSource
    dividend_source: ValuationSource
    model_version: str = "bsm-european-v1"

    def __post_init__(self) -> None:
        calculation_at = _utc(self.calculation_at, "calculation_at")
        _finite(self.observed_option_price, "observed_option_price", nonnegative=True)
        _finite(self.underlying_price, "underlying_price", positive=True)
        _finite(self.risk_free_rate, "risk_free_rate")
        _finite(self.dividend_yield, "dividend_yield")
        _finite(self.implied_volatility, "implied_volatility", positive=True)
        _finite(self.time_to_expiration_years, "time_to_expiration_years", positive=True)
        if _VERSION.fullmatch(self.model_version) is None:
            raise OptionsDomainError("model_version is invalid")
        sources = (
            self.option_quote_source,
            self.underlying_source,
            self.rate_source,
            self.dividend_source,
        )
        if len({source.record_id for source in sources}) != len(sources):
            raise OptionsDomainError("valuation source record_ids must be unique")
        if any(source.available_at > calculation_at for source in sources):
            raise OptionsDomainError("valuation source was unavailable at calculation_at")
        object.__setattr__(self, "calculation_at", calculation_at)


@dataclass(frozen=True, slots=True)
class OptionGreeks:
    """Independent model output with explicit American-style limitation disclosure."""

    contract_id: str
    calculation_at: datetime
    model_version: str
    model_price: Decimal
    price_residual: Decimal
    delta: Decimal
    gamma: Decimal
    vega: Decimal
    theta_per_day: Decimal
    rho: Decimal
    limitation_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.contract_id, "contract_id")
        object.__setattr__(self, "calculation_at", _utc(self.calculation_at, "calculation_at"))
        if _VERSION.fullmatch(self.model_version) is None:
            raise OptionsDomainError("model_version is invalid")
        for field_name in (
            "model_price",
            "price_residual",
            "delta",
            "gamma",
            "vega",
            "theta_per_day",
            "rho",
        ):
            _finite(getattr(self, field_name), field_name)
        if self.model_price < _ZERO or self.gamma < _ZERO or self.vega < _ZERO:
            raise OptionsDomainError("option price, gamma, and vega must be nonnegative")
        if len(set(self.limitation_flags)) != len(self.limitation_flags):
            raise OptionsDomainError("limitation_flags must be unique")


def black_scholes_greeks(*, contract: OptionContract, inputs: BlackScholesInputs) -> OptionGreeks:
    """Calculate European BSM sensitivities and flag unsupported early-exercise behavior."""

    if inputs.calculation_at > contract.expiration_at:
        raise OptionsDomainError("cannot value an option after its expiration timestamp")
    spot = float(inputs.underlying_price)
    strike = float(contract.strike)
    rate = float(inputs.risk_free_rate)
    dividend = float(inputs.dividend_yield)
    volatility = float(inputs.implied_volatility)
    time = float(inputs.time_to_expiration_years)
    sqrt_time = sqrt(time)
    d1 = (log(spot / strike) + (rate - dividend + (volatility**2) / 2) * time) / (
        volatility * sqrt_time
    )
    d2 = d1 - volatility * sqrt_time
    normal_d1 = _normal_cdf(d1)
    normal_d2 = _normal_cdf(d2)
    density_d1 = _normal_density(d1)
    discount_dividend = exp(-dividend * time)
    discount_rate = exp(-rate * time)
    common = -spot * discount_dividend * density_d1 * volatility / (_TWO_FLOAT * sqrt_time)
    if contract.right is OptionRight.CALL:
        price = spot * discount_dividend * normal_d1 - strike * discount_rate * normal_d2
        delta = discount_dividend * normal_d1
        theta = (
            common
            - rate * strike * discount_rate * normal_d2
            + dividend * spot * discount_dividend * normal_d1
        )
        rho = strike * time * discount_rate * normal_d2
    else:
        price = strike * discount_rate * _normal_cdf(-d2) - spot * discount_dividend * _normal_cdf(
            -d1
        )
        delta = discount_dividend * (normal_d1 - 1.0)
        theta = (
            common
            + rate * strike * discount_rate * _normal_cdf(-d2)
            - dividend * spot * discount_dividend * _normal_cdf(-d1)
        )
        rho = -strike * time * discount_rate * _normal_cdf(-d2)
    gamma = discount_dividend * density_d1 / (spot * volatility * sqrt_time)
    vega = spot * discount_dividend * density_d1 * sqrt_time
    limitation_flags = (
        ("european_exercise_assumption",)
        if contract.exercise_style is not ExerciseStyle.EUROPEAN
        else ()
    )
    model_price = _decimal_float(price, "model_price")
    return OptionGreeks(
        contract_id=contract.security.instrument_id,
        calculation_at=inputs.calculation_at,
        model_version=inputs.model_version,
        model_price=model_price,
        price_residual=model_price - inputs.observed_option_price,
        delta=_decimal_float(delta, "delta"),
        gamma=_decimal_float(gamma, "gamma"),
        vega=_decimal_float(vega, "vega"),
        theta_per_day=_decimal_float(theta / float(_365), "theta_per_day"),
        rho=_decimal_float(rho, "rho"),
        limitation_flags=limitation_flags,
    )


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """One intended contract position in a policy-approved strategy definition."""

    leg_id: str
    contract: OptionContract
    side: PositionSide
    contracts: int

    def __post_init__(self) -> None:
        _identifier(self.leg_id, "leg_id")
        if self.contracts < 1:
            raise OptionsDomainError("leg contracts must be positive")


@dataclass(frozen=True, slots=True)
class DefinedRiskOptionStrategy:
    """Static option strategy definition restricted to the frozen initial options scope."""

    strategy_id: str
    structure: OptionStructure
    legs: tuple[OptionLeg, ...]
    covered_underlying_shares: Decimal = _ZERO
    cash_collateral: Decimal = _ZERO

    def __post_init__(self) -> None:
        _identifier(self.strategy_id, "strategy_id")
        _finite(self.covered_underlying_shares, "covered_underlying_shares", nonnegative=True)
        _finite(self.cash_collateral, "cash_collateral", nonnegative=True)
        if len({leg.leg_id for leg in self.legs}) != len(self.legs):
            raise OptionsDomainError("strategy leg_ids must be unique")
        if not self.legs:
            raise OptionsDomainError("option strategy requires at least one leg")
        if self.structure in {OptionStructure.LONG_CALL, OptionStructure.LONG_PUT}:
            _validate_single_long(self)
        elif self.structure in {
            OptionStructure.VERTICAL_DEBIT_SPREAD,
            OptionStructure.VERTICAL_CREDIT_SPREAD,
        }:
            _validate_vertical(self)
        elif self.structure is OptionStructure.COVERED_CALL:
            _validate_covered_call(self)
        elif self.structure is OptionStructure.CASH_SECURED_PUT:
            _validate_cash_secured_put(self)
        else:
            raise OptionsDomainError("option strategy structure is unsupported")

    @property
    def maximum_width_cash(self) -> Decimal | None:
        """Return width exposure for a vertical before premium, or None for other structures."""

        if self.structure not in {
            OptionStructure.VERTICAL_DEBIT_SPREAD,
            OptionStructure.VERTICAL_CREDIT_SPREAD,
        }:
            return None
        low, high = sorted(leg.contract.strike for leg in self.legs)
        multiplier = self.legs[0].contract.contract_multiplier
        return (high - low) * multiplier * self.legs[0].contracts


@dataclass(frozen=True, slots=True)
class OptionLegPosition:
    """Current opened contract count for one predeclared leg after partial fills."""

    leg_id: str
    open_contracts: int = 0

    def __post_init__(self) -> None:
        _identifier(self.leg_id, "leg_id")
        if self.open_contracts < 0:
            raise OptionsDomainError("open_contracts must be nonnegative")


@dataclass(frozen=True, slots=True)
class OptionFill:
    """Observed leg-level fill; theoretical fills are intentionally not represented."""

    fill_id: str
    leg_id: str
    action: OptionTradeAction
    filled_at: datetime
    contracts: int
    premium_per_share: Decimal
    fee: Decimal

    def __post_init__(self) -> None:
        _identifier(self.fill_id, "fill_id")
        _identifier(self.leg_id, "leg_id")
        object.__setattr__(self, "filled_at", _utc(self.filled_at, "filled_at"))
        if self.contracts < 1:
            raise OptionsDomainError("fill contracts must be positive")
        _finite(self.premium_per_share, "premium_per_share", nonnegative=True)
        _finite(self.fee, "fee", nonnegative=True)


@dataclass(frozen=True, slots=True)
class OptionLifecycleEvent:
    """Explicit exercise, assignment, or worthless-expiry receipt for one open leg."""

    event_id: str
    leg_id: str
    event_type: OptionLifecycleEventType
    occurred_at: datetime
    available_at: datetime
    contracts: int
    underlying_price: Decimal | None = None
    adjustment: OptionContractAdjustment | None = None

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event_id")
        _identifier(self.leg_id, "leg_id")
        occurred_at = _utc(self.occurred_at, "occurred_at")
        available_at = _utc(self.available_at, "available_at")
        if available_at < occurred_at:
            raise OptionsDomainError("lifecycle available_at cannot precede occurred_at")
        if self.contracts < 1:
            raise OptionsDomainError("lifecycle contracts must be positive")
        if self.event_type is OptionLifecycleEventType.CONTRACT_ADJUSTMENT:
            if self.adjustment is None or self.underlying_price is not None:
                raise OptionsDomainError("contract adjustment event requires adjustment only")
        else:
            if self.adjustment is not None:
                raise OptionsDomainError("only contract adjustment events may include adjustment")
            if self.underlying_price is None:
                raise OptionsDomainError(
                    "exercise, assignment, and expiry require underlying_price"
                )
            _finite(self.underlying_price, "underlying_price", positive=True)
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True, slots=True)
class OptionStrategyPosition:
    """Reconciled leg, cash, collateral, and underlying-share state for one strategy."""

    strategy: DefinedRiskOptionStrategy
    leg_positions: tuple[OptionLegPosition, ...]
    premium_cashflow: Decimal = _ZERO
    lifecycle_cashflow: Decimal = _ZERO
    fees: Decimal = _ZERO
    underlying_shares: Decimal = _ZERO
    fills: tuple[OptionFill, ...] = ()
    lifecycle_events: tuple[OptionLifecycleEvent, ...] = ()

    def __post_init__(self) -> None:
        expected_ids = tuple(sorted(leg.leg_id for leg in self.strategy.legs))
        actual_ids = tuple(position.leg_id for position in self.leg_positions)
        if actual_ids != expected_ids:
            raise OptionsDomainError("leg_positions must match canonical strategy leg order")
        intended = {leg.leg_id: leg.contracts for leg in self.strategy.legs}
        if any(
            position.open_contracts > intended[position.leg_id] for position in self.leg_positions
        ):
            raise OptionsDomainError("leg position exceeds predeclared contract quantity")
        for field_name in ("premium_cashflow", "lifecycle_cashflow", "fees", "underlying_shares"):
            _finite(getattr(self, field_name), field_name)
        if self.fees < _ZERO:
            raise OptionsDomainError("fees must be nonnegative")
        if self.underlying_shares < _ZERO:
            raise OptionsDomainError("underlying_shares must be nonnegative")
        if self.strategy.structure is OptionStructure.COVERED_CALL:
            short_leg = self.strategy.legs[0]
            open_short_contracts = next(
                item.open_contracts
                for item in self.leg_positions
                if item.leg_id == short_leg.leg_id
            )
            required_shares = short_leg.contract.underlying_deliverable_quantity * Decimal(
                open_short_contracts
            )
            if self.underlying_shares < required_shares:
                raise OptionsDomainError("position cannot lose shares covering open short calls")
        if len({fill.fill_id for fill in self.fills}) != len(self.fills):
            raise OptionsDomainError("fill ids must be unique")
        if len({event.event_id for event in self.lifecycle_events}) != len(self.lifecycle_events):
            raise OptionsDomainError("lifecycle event ids must be unique")

    @property
    def net_cashflow(self) -> Decimal:
        """Return premium plus lifecycle cash movements less retained fees."""

        return self.premium_cashflow + self.lifecycle_cashflow - self.fees

    @property
    def status(self) -> OptionPositionStatus:
        open_contracts = tuple(position.open_contracts for position in self.leg_positions)
        targets = tuple(
            leg.contracts for leg in sorted(self.strategy.legs, key=lambda leg: leg.leg_id)
        )
        if all(quantity == 0 for quantity in open_contracts):
            if not self.fills and not self.lifecycle_events:
                return OptionPositionStatus.NEW
            return OptionPositionStatus.CLOSED
        if all(current == target for current, target in zip(open_contracts, targets, strict=True)):
            return OptionPositionStatus.OPEN
        if any(
            fill.action in {OptionTradeAction.BUY_TO_CLOSE, OptionTradeAction.SELL_TO_CLOSE}
            for fill in self.fills
        ):
            return OptionPositionStatus.PARTIALLY_CLOSED
        return OptionPositionStatus.PARTIALLY_OPEN


def initialize_option_position(*, strategy: DefinedRiskOptionStrategy) -> OptionStrategyPosition:
    """Create a zero-fill position with required covered-call inventory retained explicitly."""

    return OptionStrategyPosition(
        strategy=strategy,
        leg_positions=tuple(
            OptionLegPosition(leg_id=leg.leg_id)
            for leg in sorted(strategy.legs, key=lambda leg: leg.leg_id)
        ),
        underlying_shares=strategy.covered_underlying_shares,
        fills=(),
    )


def apply_option_fill(
    *, position: OptionStrategyPosition, fill: OptionFill
) -> OptionStrategyPosition:
    """Apply an observed partial leg fill without allowing transient naked short exposure."""

    leg = _leg(position.strategy, fill.leg_id)
    expected_actions = (
        {OptionTradeAction.BUY_TO_OPEN, OptionTradeAction.SELL_TO_CLOSE}
        if leg.side is PositionSide.LONG
        else {OptionTradeAction.SELL_TO_OPEN, OptionTradeAction.BUY_TO_CLOSE}
    )
    if fill.action not in expected_actions:
        raise OptionsDomainError("fill action is incompatible with strategy leg side")
    current = _leg_position(position, fill.leg_id)
    opening = fill.action in {OptionTradeAction.BUY_TO_OPEN, OptionTradeAction.SELL_TO_OPEN}
    updated_open = (
        current.open_contracts + fill.contracts
        if opening
        else current.open_contracts - fill.contracts
    )
    if updated_open < 0:
        raise OptionsDomainError("cannot close more contracts than are open")
    if updated_open > leg.contracts:
        raise OptionsDomainError("fill exceeds predeclared leg contract quantity")
    updated_positions = tuple(
        OptionLegPosition(
            leg_id=item.leg_id,
            open_contracts=updated_open if item.leg_id == fill.leg_id else item.open_contracts,
        )
        for item in position.leg_positions
    )
    _validate_live_short_coverage(strategy=position.strategy, leg_positions=updated_positions)
    multiplier_cash = (
        leg.contract.contract_multiplier * Decimal(fill.contracts) * fill.premium_per_share
    )
    premium_delta = (
        -multiplier_cash
        if fill.action in {OptionTradeAction.BUY_TO_OPEN, OptionTradeAction.BUY_TO_CLOSE}
        else multiplier_cash
    )
    return OptionStrategyPosition(
        strategy=position.strategy,
        leg_positions=updated_positions,
        premium_cashflow=position.premium_cashflow + premium_delta,
        lifecycle_cashflow=position.lifecycle_cashflow,
        fees=position.fees + fill.fee,
        underlying_shares=position.underlying_shares,
        fills=(*position.fills, fill),
        lifecycle_events=position.lifecycle_events,
    )


def apply_option_lifecycle(
    *, position: OptionStrategyPosition, event: OptionLifecycleEvent
) -> OptionStrategyPosition:
    """Resolve one leg via explicit exercise, assignment, expiry, or adjustment receipt."""

    leg = _leg(position.strategy, event.leg_id)
    current = _leg_position(position, event.leg_id)
    if event.event_id in {existing.event_id for existing in position.lifecycle_events}:
        raise OptionsDomainError("lifecycle event_id has already been applied")
    if event.event_type is OptionLifecycleEventType.CONTRACT_ADJUSTMENT:
        _validate_adjustment_event(leg=leg, event=event)
        return OptionStrategyPosition(
            strategy=position.strategy,
            leg_positions=position.leg_positions,
            premium_cashflow=position.premium_cashflow,
            lifecycle_cashflow=position.lifecycle_cashflow,
            fees=position.fees,
            underlying_shares=position.underlying_shares,
            fills=position.fills,
            lifecycle_events=(*position.lifecycle_events, event),
        )
    if event.contracts > current.open_contracts:
        raise OptionsDomainError("lifecycle event exceeds open contracts")
    if event.event_type is OptionLifecycleEventType.EXERCISE and leg.side is not PositionSide.LONG:
        raise OptionsDomainError("only long option legs can be exercised")
    if (
        event.event_type is OptionLifecycleEventType.ASSIGNMENT
        and leg.side is not PositionSide.SHORT
    ):
        raise OptionsDomainError("only short option legs can be assigned")
    if event.event_type is OptionLifecycleEventType.EXPIRE_WORTHLESS:
        if event.occurred_at < leg.contract.expiration_at:
            raise OptionsDomainError("worthless expiry cannot precede contract expiration")
        if _intrinsic_value(leg.contract, event.underlying_price) > _ZERO:
            raise OptionsDomainError("in-the-money option cannot be silently expired worthless")
        cash_delta = _ZERO
        shares_delta = _ZERO
    else:
        cash_delta, shares_delta = _exercise_assignment_cash_and_shares(
            leg=leg,
            event_type=event.event_type,
            contracts=event.contracts,
            existing_shares=position.underlying_shares,
            underlying_price=event.underlying_price,
        )
    updated_positions = tuple(
        OptionLegPosition(
            leg_id=item.leg_id,
            open_contracts=item.open_contracts - event.contracts
            if item.leg_id == event.leg_id
            else item.open_contracts,
        )
        for item in position.leg_positions
    )
    _validate_live_short_coverage(strategy=position.strategy, leg_positions=updated_positions)
    return OptionStrategyPosition(
        strategy=position.strategy,
        leg_positions=updated_positions,
        premium_cashflow=position.premium_cashflow,
        lifecycle_cashflow=position.lifecycle_cashflow + cash_delta,
        fees=position.fees,
        underlying_shares=position.underlying_shares + shares_delta,
        fills=position.fills,
        lifecycle_events=(*position.lifecycle_events, event),
    )


def _validate_single_long(strategy: DefinedRiskOptionStrategy) -> None:
    if len(strategy.legs) != 1:
        raise OptionsDomainError("single-long strategy requires exactly one option leg")
    leg = strategy.legs[0]
    required_right = (
        OptionRight.CALL if strategy.structure is OptionStructure.LONG_CALL else OptionRight.PUT
    )
    if leg.side is not PositionSide.LONG or leg.contract.right is not required_right:
        raise OptionsDomainError("single-long strategy has an incompatible leg")
    if strategy.covered_underlying_shares != _ZERO or strategy.cash_collateral != _ZERO:
        raise OptionsDomainError("single-long strategy cannot declare coverage or collateral")


def _validate_vertical(strategy: DefinedRiskOptionStrategy) -> None:
    if len(strategy.legs) != 2:
        raise OptionsDomainError("vertical spread requires exactly two option legs")
    first, second = strategy.legs
    if first.contracts != second.contracts:
        raise OptionsDomainError("vertical legs must use equal contract counts")
    contracts = (first.contract, second.contract)
    if len({contract.underlying_security.instrument_id for contract in contracts}) != 1:
        raise OptionsDomainError("vertical legs must share an underlying")
    if len({contract.expiration_at for contract in contracts}) != 1:
        raise OptionsDomainError("vertical legs must share an expiration")
    if len({contract.right for contract in contracts}) != 1:
        raise OptionsDomainError("vertical legs must share a right")
    if first.contract.strike == second.contract.strike:
        raise OptionsDomainError("vertical legs must use distinct strikes")
    long_leg = next((leg for leg in strategy.legs if leg.side is PositionSide.LONG), None)
    short_leg = next((leg for leg in strategy.legs if leg.side is PositionSide.SHORT), None)
    if long_leg is None or short_leg is None:
        raise OptionsDomainError("vertical spread requires one long and one short leg")
    right = long_leg.contract.right
    debit_expected = (
        right is OptionRight.CALL and long_leg.contract.strike < short_leg.contract.strike
    ) or (right is OptionRight.PUT and long_leg.contract.strike > short_leg.contract.strike)
    if (strategy.structure is OptionStructure.VERTICAL_DEBIT_SPREAD) != debit_expected:
        raise OptionsDomainError("vertical debit or credit direction is inconsistent with strikes")
    if strategy.covered_underlying_shares != _ZERO or strategy.cash_collateral != _ZERO:
        raise OptionsDomainError("vertical spread cannot declare standalone coverage or collateral")


def _validate_covered_call(strategy: DefinedRiskOptionStrategy) -> None:
    if len(strategy.legs) != 1:
        raise OptionsDomainError("covered call requires exactly one option leg")
    leg = strategy.legs[0]
    if leg.side is not PositionSide.SHORT or leg.contract.right is not OptionRight.CALL:
        raise OptionsDomainError("covered call requires one short call")
    required_shares = leg.contract.underlying_deliverable_quantity * Decimal(leg.contracts)
    if required_shares <= _ZERO or strategy.covered_underlying_shares < required_shares:
        raise OptionsDomainError("covered call requires sufficient underlying share coverage")
    if strategy.cash_collateral != _ZERO:
        raise OptionsDomainError("covered call cannot declare put collateral")


def _validate_cash_secured_put(strategy: DefinedRiskOptionStrategy) -> None:
    if len(strategy.legs) != 1:
        raise OptionsDomainError("cash-secured put requires exactly one option leg")
    leg = strategy.legs[0]
    if leg.side is not PositionSide.SHORT or leg.contract.right is not OptionRight.PUT:
        raise OptionsDomainError("cash-secured put requires one short put")
    required_cash = leg.contract.strike * leg.contract.contract_multiplier * Decimal(leg.contracts)
    if strategy.cash_collateral < required_cash:
        raise OptionsDomainError("cash-secured put requires full strike collateral")
    if strategy.covered_underlying_shares != _ZERO:
        raise OptionsDomainError("cash-secured put cannot declare call share coverage")


def _validate_live_short_coverage(
    *, strategy: DefinedRiskOptionStrategy, leg_positions: Sequence[OptionLegPosition]
) -> None:
    positions = {item.leg_id: item.open_contracts for item in leg_positions}
    if strategy.structure in {
        OptionStructure.VERTICAL_DEBIT_SPREAD,
        OptionStructure.VERTICAL_CREDIT_SPREAD,
    }:
        long_leg = next(leg for leg in strategy.legs if leg.side is PositionSide.LONG)
        short_leg = next(leg for leg in strategy.legs if leg.side is PositionSide.SHORT)
        if positions[short_leg.leg_id] > positions[long_leg.leg_id]:
            raise OptionsDomainError("partial fills would leave a vertical short leg unprotected")
    if strategy.structure is OptionStructure.COVERED_CALL:
        short_leg = strategy.legs[0]
        required = short_leg.contract.underlying_deliverable_quantity * Decimal(
            positions[short_leg.leg_id]
        )
        if strategy.covered_underlying_shares < required:
            raise OptionsDomainError("partial covered-call fill exceeds retained share coverage")
    if strategy.structure is OptionStructure.CASH_SECURED_PUT:
        short_leg = strategy.legs[0]
        required = (
            short_leg.contract.strike
            * short_leg.contract.contract_multiplier
            * Decimal(positions[short_leg.leg_id])
        )
        if strategy.cash_collateral < required:
            raise OptionsDomainError("partial cash-secured-put fill exceeds retained collateral")


def _exercise_assignment_cash_and_shares(
    *,
    leg: OptionLeg,
    event_type: OptionLifecycleEventType,
    contracts: int,
    existing_shares: Decimal,
    underlying_price: Decimal | None,
) -> tuple[Decimal, Decimal]:
    if event_type not in {OptionLifecycleEventType.EXERCISE, OptionLifecycleEventType.ASSIGNMENT}:
        raise OptionsDomainError("invalid event type for exercise/assignment settlement")
    if underlying_price is None:
        raise OptionsDomainError("exercise/assignment requires underlying_price")
    if leg.contract.settlement_type is SettlementType.CASH:
        intrinsic_cash = _intrinsic_value(leg.contract, underlying_price)
        notional = intrinsic_cash * leg.contract.contract_multiplier * Decimal(contracts)
        return (notional if leg.side is PositionSide.LONG else -notional, _ZERO)
    if leg.contract.settlement_type is not SettlementType.PHYSICAL:
        raise OptionsDomainError("mixed or unknown settlement requires a separate lifecycle model")
    shares = leg.contract.underlying_deliverable_quantity * Decimal(contracts)
    if shares <= _ZERO:
        raise OptionsDomainError(
            "physical settlement requires underlying security deliverable quantity"
        )
    strike_cash = leg.contract.strike * shares
    if leg.contract.right is OptionRight.CALL:
        cash_delta = -strike_cash if leg.side is PositionSide.LONG else strike_cash
        shares_delta = shares if leg.side is PositionSide.LONG else -shares
    else:
        cash_delta = strike_cash if leg.side is PositionSide.LONG else -strike_cash
        shares_delta = -shares if leg.side is PositionSide.LONG else shares
    if existing_shares + shares_delta < _ZERO:
        raise OptionsDomainError(
            "exercise or assignment would require unmodeled short underlying shares"
        )
    return cash_delta, shares_delta


def _validate_adjustment_event(*, leg: OptionLeg, event: OptionLifecycleEvent) -> None:
    adjustment = event.adjustment
    if adjustment is None:
        raise OptionsDomainError("contract adjustment receipt is missing")
    if adjustment.original_contract_id != leg.contract.security.instrument_id:
        raise OptionsDomainError("contract adjustment does not reference the affected leg contract")
    if event.contracts != leg.contracts:
        raise OptionsDomainError("contract adjustment must retain full predeclared leg quantity")
    if (
        event.occurred_at != adjustment.effective_at
        or event.available_at != adjustment.available_at
    ):
        raise OptionsDomainError(
            "lifecycle adjustment timestamps must match the adjustment receipt"
        )


def _leg(strategy: DefinedRiskOptionStrategy, leg_id: str) -> OptionLeg:
    for leg in strategy.legs:
        if leg.leg_id == leg_id:
            return leg
    raise OptionsDomainError("unknown strategy leg_id")


def _leg_position(position: OptionStrategyPosition, leg_id: str) -> OptionLegPosition:
    for item in position.leg_positions:
        if item.leg_id == leg_id:
            return item
    raise OptionsDomainError("unknown strategy leg position")


def _intrinsic_value(contract: OptionContract, underlying_price: Decimal | None) -> Decimal:
    if underlying_price is None:
        raise OptionsDomainError("underlying_price is required")
    return (
        max(underlying_price - contract.strike, _ZERO)
        if contract.right is OptionRight.CALL
        else max(contract.strike - underlying_price, _ZERO)
    )


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _normal_density(value: float) -> float:
    return exp(-(value**2) / 2.0) / sqrt(2.0 * pi)


def _decimal_float(value: float, field_name: str) -> Decimal:
    if value != value or value in {float("inf"), float("-inf")}:
        raise OptionsDomainError(f"{field_name} must be finite")
    return Decimal(str(value))


def _identifier(value: str, field_name: str) -> None:
    if _IDENTIFIER.fullmatch(value.strip()) is None:
        raise OptionsDomainError(f"{field_name} is invalid")


def _normalized_identifier(value: str, field_name: str) -> str:
    _identifier(value, field_name)
    return value.strip().upper()


def _currency(value: str | None, field_name: str) -> str:
    if value is None:
        raise OptionsDomainError(f"{field_name} is required")
    currency = value.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise OptionsDomainError(f"{field_name} must be a three-letter code")
    return currency


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OptionsDomainError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _sha256(value: str, field_name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise OptionsDomainError(f"{field_name} must be a lowercase SHA-256 digest")


def _finite(
    value: Decimal, field_name: str, *, positive: bool = False, nonnegative: bool = False
) -> Decimal:
    if not value.is_finite():
        raise OptionsDomainError(f"{field_name} must be finite")
    if positive and value <= _ZERO:
        raise OptionsDomainError(f"{field_name} must be positive")
    if nonnegative and value < _ZERO:
        raise OptionsDomainError(f"{field_name} must be nonnegative")
    return value


_TWO_FLOAT = 2.0
