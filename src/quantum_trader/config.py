"""Typed settings and the non-bypassable simulation-only execution policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from quantum_trader.domain.execution import ExecutionGate, ExecutionMode
from quantum_trader.domain.risk import RiskLimits


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Complete reproducible configuration for one simulation run."""

    symbol: str = "DEMO"
    initial_cash: Decimal = Decimal("100000")
    fast_window: int = 20
    slow_window: int = 50
    invested_fraction: Decimal = Decimal("0.95")
    slippage_bps: Decimal = Decimal("1")
    execution_price_buffer_bps: Decimal = Decimal("1000")
    fee_per_order: Decimal = Decimal("0")
    fee_per_share: Decimal = Decimal("0")
    risk_limits: RiskLimits = field(default_factory=RiskLimits)
    mode: ExecutionMode = ExecutionMode.SIMULATION

    def __post_init__(self) -> None:
        if self.mode is not ExecutionMode.SIMULATION:
            raise ValueError("only offline simulation mode is supported")
        if not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.fast_window < 2 or self.slow_window <= self.fast_window:
            raise ValueError("moving-average windows are invalid")
        if not Decimal("0") < self.invested_fraction <= Decimal("1"):
            raise ValueError("invested_fraction must be in (0, 1]")
        if any(
            value < 0
            for value in (
                self.slippage_bps,
                self.execution_price_buffer_bps,
                self.fee_per_order,
                self.fee_per_share,
            )
        ):
            raise ValueError("slippage, execution buffer, and fees must not be negative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "symbol": self.symbol.upper(),
            "initial_cash": str(self.initial_cash),
            "fast_window": self.fast_window,
            "slow_window": self.slow_window,
            "invested_fraction": str(self.invested_fraction),
            "slippage_bps": str(self.slippage_bps),
            "execution_price_buffer_bps": str(self.execution_price_buffer_bps),
            "fee_per_order": str(self.fee_per_order),
            "fee_per_share": str(self.fee_per_share),
            "risk_limits": {
                "max_position_fraction": str(self.risk_limits.max_position_fraction),
                "max_order_notional": str(self.risk_limits.max_order_notional),
                "min_cash_reserve_fraction": str(self.risk_limits.min_cash_reserve_fraction),
                "max_drawdown_fraction": str(self.risk_limits.max_drawdown_fraction),
                "max_realized_loss": str(self.risk_limits.max_realized_loss),
            },
        }


class ExecutionPolicy:
    """Reject unsupported escalation before adapters are initialized."""

    @staticmethod
    def require_simulation(mode: str | ExecutionMode) -> ExecutionMode:
        return ExecutionGate.require_simulation(mode)
