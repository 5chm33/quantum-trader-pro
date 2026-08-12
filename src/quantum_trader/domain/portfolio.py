"""Portfolio accounting and target-position translation."""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

from quantum_trader.domain.models import (
    ZERO,
    EquityPoint,
    Fill,
    MarketEvent,
    OrderIntent,
    Position,
    Side,
    Signal,
    decimal_value,
)


class Portfolio:
    """Own all mutable cash and position state for one simulation run."""

    def __init__(self, initial_cash: Decimal | int | float | str) -> None:
        starting_cash = decimal_value(initial_cash)
        if starting_cash <= ZERO or not starting_cash.is_finite():
            raise ValueError("initial_cash must be finite and positive")
        self.initial_cash = starting_cash
        self.cash = starting_cash
        self.positions: dict[str, Position] = {}
        self.realized_pnl = ZERO
        self.total_fees = ZERO

    def position_quantity(self, symbol: str) -> int:
        position = self.positions.get(symbol)
        return position.quantity if position else 0

    def apply_fill(self, fill: Fill) -> None:
        """Apply one fill atomically and enforce cash/position invariants."""

        position = self.positions.setdefault(fill.symbol, Position(symbol=fill.symbol))
        notional = fill.gross_notional

        if fill.side is Side.BUY:
            total_cost = notional + fill.fee
            if total_cost > self.cash:
                raise ValueError("buy fill would create a negative cash balance")
            old_cost_basis = position.average_price * position.quantity
            new_quantity = position.quantity + fill.quantity
            position.average_price = (old_cost_basis + notional) / new_quantity
            position.quantity = new_quantity
            self.cash -= total_cost
        else:
            if fill.quantity > position.quantity:
                raise ValueError("sell fill exceeds the current position")
            self.realized_pnl += (fill.price - position.average_price) * fill.quantity
            position.quantity -= fill.quantity
            self.cash += notional - fill.fee
            if position.quantity == 0:
                position.average_price = ZERO

        self.total_fees += fill.fee
        self._validate_state()

    def equity_point(self, event: MarketEvent) -> EquityPoint:
        """Mark the portfolio to one event and return a reconciled snapshot."""

        market_value = ZERO
        unrealized_pnl = ZERO
        for symbol, position in self.positions.items():
            if position.quantity == 0:
                continue
            if symbol != event.symbol:
                raise ValueError(f"no current price supplied for open position {symbol}")
            market_value += event.close * position.quantity
            unrealized_pnl += (event.close - position.average_price) * position.quantity
        equity = self.cash + market_value
        return EquityPoint(
            timestamp=event.timestamp,
            cash=self.cash,
            market_value=market_value,
            equity=equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized_pnl,
            fees=self.total_fees,
        )

    def order_for_target(self, signal: Signal, event: MarketEvent) -> OrderIntent | None:
        """Translate a target fraction into a long-only share delta."""

        if signal.symbol != event.symbol:
            raise ValueError("signal and market-event symbols must match")
        snapshot = self.equity_point(event)
        target_value = snapshot.equity * signal.target_fraction
        target_quantity = int((target_value / event.close).to_integral_value(rounding=ROUND_FLOOR))
        current_quantity = self.position_quantity(event.symbol)
        quantity_delta = target_quantity - current_quantity
        if quantity_delta == 0:
            return None

        side = Side.BUY if quantity_delta > 0 else Side.SELL
        return OrderIntent.create(
            correlation_id=signal.correlation_id,
            timestamp=signal.timestamp,
            symbol=signal.symbol,
            side=side,
            quantity=abs(quantity_delta),
            reference_price=event.close,
            rationale=signal.rationale,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "initial_cash": str(self.initial_cash),
            "cash": str(self.cash),
            "realized_pnl": str(self.realized_pnl),
            "total_fees": str(self.total_fees),
            "positions": {
                symbol: position.as_dict()
                for symbol, position in sorted(self.positions.items())
                if position.quantity != 0
            },
        }

    def _validate_state(self) -> None:
        if self.cash < ZERO or not self.cash.is_finite():
            raise ValueError("portfolio cash invariant failed")
        for position in self.positions.values():
            if position.quantity < 0:
                raise ValueError("short positions are not supported")
            if position.quantity > 0 and position.average_price <= ZERO:
                raise ValueError("open positions require a positive average price")
