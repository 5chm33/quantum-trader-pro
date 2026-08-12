"""Fail-closed risk evaluation for simulated and broker-bound order intents."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from quantum_trader.domain.models import ZERO, EquityPoint, Fill, OrderIntent, RiskDecision, Side
from quantum_trader.domain.portfolio import Portfolio

_BASIS_POINTS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Explicit limits applied to every order intent and realized fill."""

    max_position_fraction: Decimal = Decimal("0.25")
    max_order_notional: Decimal = Decimal("25000")
    min_cash_reserve_fraction: Decimal = Decimal("0.05")
    max_drawdown_fraction: Decimal = Decimal("0.15")
    max_realized_loss: Decimal = Decimal("5000")

    def __post_init__(self) -> None:
        fractions = (
            self.max_position_fraction,
            self.min_cash_reserve_fraction,
            self.max_drawdown_fraction,
        )
        if any(not ZERO < value < Decimal("1") for value in fractions):
            raise ValueError("risk fractions must be between zero and one")
        if self.max_order_notional <= ZERO or self.max_realized_loss <= ZERO:
            raise ValueError("risk notional and loss limits must be positive")


class RiskManager:
    """Stateful risk manager with cost-aware sizing and emergency halts."""

    def __init__(
        self,
        limits: RiskLimits,
        *,
        slippage_bps: Decimal = ZERO,
        execution_price_buffer_bps: Decimal = ZERO,
        fee_per_order: Decimal = ZERO,
        fee_per_share: Decimal = ZERO,
    ) -> None:
        cost_inputs = (
            slippage_bps,
            execution_price_buffer_bps,
            fee_per_order,
            fee_per_share,
        )
        if any(value < ZERO or not value.is_finite() for value in cost_inputs):
            raise ValueError("risk execution assumptions must be finite and non-negative")
        self.limits = limits
        self.slippage_bps = slippage_bps
        self.execution_price_buffer_bps = execution_price_buffer_bps
        self.fee_per_order = fee_per_order
        self.fee_per_share = fee_per_share
        self.peak_equity = ZERO
        self.halted = False
        self.halt_reason: str | None = None
        self._seen_intents: set[str] = set()

    def observe(self, portfolio: Portfolio, snapshot: EquityPoint) -> None:
        """Update circuit breakers from every reconciled equity observation."""

        self.peak_equity = max(self.peak_equity, snapshot.equity)
        net_realized_pnl = portfolio.realized_pnl - portfolio.total_fees
        if snapshot.equity <= ZERO:
            self._halt("non_positive_equity")
        elif net_realized_pnl <= -self.limits.max_realized_loss:
            self._halt("maximum_realized_loss_exceeded")
        elif self.peak_equity > ZERO:
            drawdown = (self.peak_equity - snapshot.equity) / self.peak_equity
            if drawdown >= self.limits.max_drawdown_fraction:
                self._halt("maximum_drawdown_exceeded")

    def observe_fill(self, fill: Fill, portfolio: Portfolio) -> None:
        """Recheck realized execution cost, exposure, and reserve at the actual fill price."""

        if fill.side is Side.SELL:
            return
        if fill.gross_notional + fill.fee > self.limits.max_order_notional:
            self._halt("post_fill_order_commitment_exceeded")
        position_value = portfolio.position_quantity(fill.symbol) * fill.price
        if position_value <= ZERO:
            return
        equity_at_fill = portfolio.cash + position_value
        if equity_at_fill <= ZERO:
            self._halt("post_fill_non_positive_equity")
            return
        if position_value / equity_at_fill > self.limits.max_position_fraction:
            self._halt("post_fill_position_limit_exceeded")
        required_reserve = equity_at_fill * self.limits.min_cash_reserve_fraction
        if portfolio.cash < required_reserve:
            self._halt("post_fill_cash_reserve_breached")

    def evaluate(
        self,
        intent: OrderIntent,
        portfolio: Portfolio,
        snapshot: EquityPoint,
    ) -> RiskDecision:
        """Evaluate one intent against a conservative next-execution bound."""

        if intent.intent_id in self._seen_intents:
            return self._deny(intent, "duplicate_intent")
        self._seen_intents.add(intent.intent_id)
        self.observe(portfolio, snapshot)

        if intent.side is Side.SELL:
            approved_exit = min(intent.quantity, portfolio.position_quantity(intent.symbol))
            if approved_exit <= 0:
                return self._deny(intent, "no_position_available_to_reduce")
            return RiskDecision(
                allowed=True,
                reason=("approved_risk_reducing_exit" if self.halted else "approved"),
                approved_quantity=approved_exit,
                intent_id=intent.intent_id,
                correlation_id=intent.correlation_id,
            )

        if self.halted:
            return self._deny(intent, self.halt_reason or "emergency_halt")

        conservative_price = self._conservative_buy_price(intent.reference_price)
        maximum_by_order = self._shares_for_total_cost(
            self.limits.max_order_notional,
            conservative_price,
        )
        approved = min(intent.quantity, maximum_by_order)

        maximum_position_value = snapshot.equity * self.limits.max_position_fraction
        current_value = portfolio.position_quantity(intent.symbol) * conservative_price
        remaining_position_value = max(ZERO, maximum_position_value - current_value)
        approved = min(
            approved,
            self._shares_for_value(remaining_position_value, conservative_price),
        )

        required_reserve = snapshot.equity * self.limits.min_cash_reserve_fraction
        spendable_cash = max(ZERO, portfolio.cash - required_reserve)
        approved = min(
            approved,
            self._shares_for_total_cost(spendable_cash, conservative_price),
        )
        if approved <= 0:
            return self._deny(intent, "risk_limits_reduce_quantity_to_zero")
        uses_conservative_assumptions = any(
            value > ZERO
            for value in (
                self.slippage_bps,
                self.execution_price_buffer_bps,
                self.fee_per_order,
                self.fee_per_share,
            )
        )
        if approved == intent.quantity:
            reason = (
                "approved_with_conservative_execution_bound"
                if uses_conservative_assumptions
                else "approved"
            )
        else:
            reason = (
                "approved_with_conservative_quantity_reduction"
                if uses_conservative_assumptions
                else "approved_with_quantity_reduction"
            )
        return RiskDecision(
            allowed=True,
            reason=reason,
            approved_quantity=approved,
            intent_id=intent.intent_id,
            correlation_id=intent.correlation_id,
        )

    def _conservative_buy_price(self, reference_price: Decimal) -> Decimal:
        total_bps = self.slippage_bps + self.execution_price_buffer_bps
        return reference_price * (Decimal("1") + total_bps / _BASIS_POINTS)

    def _shares_for_total_cost(self, value: Decimal, price: Decimal) -> int:
        available = value - self.fee_per_order
        per_share_cost = price + self.fee_per_share
        return self._shares_for_value(available, per_share_cost)

    @staticmethod
    def _shares_for_value(value: Decimal, price: Decimal) -> int:
        if value <= ZERO:
            return 0
        return int((value / price).to_integral_value(rounding=ROUND_FLOOR))

    @staticmethod
    def _deny(intent: OrderIntent, reason: str) -> RiskDecision:
        return RiskDecision(
            allowed=False,
            reason=reason,
            approved_quantity=0,
            intent_id=intent.intent_id,
            correlation_id=intent.correlation_id,
        )

    def _halt(self, reason: str) -> None:
        if not self.halted:
            self.halted = True
            self.halt_reason = reason
