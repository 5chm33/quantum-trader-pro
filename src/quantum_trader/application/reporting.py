"""Transparent simulation metrics and reproducible report artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from quantum_trader.application.engine import SimulationResult
from quantum_trader.domain.models import ZERO, Fill, Side, stable_id


@dataclass(frozen=True, slots=True)
class RoundTripTrade:
    """One flat-to-flat long-only trading episode, including every rebalance fill."""

    trade_id: str
    symbol: str
    opened_at: datetime
    closed_at: datetime
    entry_fill_count: int
    exit_fill_count: int
    entry_quantity: int
    exit_quantity: int
    average_entry_price: Decimal
    average_exit_price: Decimal
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    holding_seconds: int

    def as_dict(self) -> dict[str, object]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "entry_fill_count": self.entry_fill_count,
            "exit_fill_count": self.exit_fill_count,
            "entry_quantity": self.entry_quantity,
            "exit_quantity": self.exit_quantity,
            "average_entry_price": str(self.average_entry_price),
            "average_exit_price": str(self.average_exit_price),
            "gross_pnl": str(self.gross_pnl),
            "fees": str(self.fees),
            "net_pnl": str(self.net_pnl),
            "holding_seconds": self.holding_seconds,
        }


@dataclass(slots=True)
class _TradeAccumulator:
    symbol: str
    opened_at: datetime
    first_fill_id: str
    last_fill_id: str
    quantity: int = 0
    entry_fill_count: int = 0
    exit_fill_count: int = 0
    entry_quantity: int = 0
    exit_quantity: int = 0
    entry_notional: Decimal = ZERO
    exit_notional: Decimal = ZERO
    fees: Decimal = ZERO


def calculate_metrics(result: SimulationResult) -> dict[str, Any]:
    """Calculate declared, deterministic metrics from reconciled observations."""

    if not result.equity_curve or not result.market_prices:
        raise ValueError("simulation result contains no observations")
    first = result.equity_curve[0]
    last = result.equity_curve[-1]
    initial_equity = first.equity
    final_equity = last.equity
    total_return = final_equity / initial_equity - Decimal("1")
    price_benchmark_return = result.market_prices[-1][1] / result.market_prices[0][1] - Decimal("1")

    total_return_proxy: Decimal | None = None
    if result.benchmark_prices:
        if len(result.benchmark_prices) != len(result.market_prices):
            raise ValueError("adjusted-close benchmark length does not match market observations")
        if [item[0] for item in result.benchmark_prices] != [
            item[0] for item in result.market_prices
        ]:
            raise ValueError("adjusted-close benchmark timestamps do not match market observations")
        total_return_proxy = result.benchmark_prices[-1][1] / result.benchmark_prices[0][
            1
        ] - Decimal("1")

    returns = [
        float(current.equity / previous.equity - Decimal("1"))
        for previous, current in pairwise(result.equity_curve)
        if previous.equity > 0
    ]
    periods_per_year = _periods_per_year([point.timestamp for point in result.equity_curve])
    sharpe_ratio: float | None = None
    if len(returns) >= 2:
        deviation = statistics.stdev(returns)
        if deviation > 0:
            sharpe_ratio = statistics.mean(returns) / deviation * math.sqrt(periods_per_year)

    elapsed_seconds = (last.timestamp - first.timestamp).total_seconds()
    elapsed_years = elapsed_seconds / (365.2425 * 24 * 60 * 60) if elapsed_seconds > 0 else 0.0
    annualized_return: float | None = None
    if elapsed_seconds >= 30 * 24 * 60 * 60 and total_return > Decimal("-1"):
        annualized_return = float(
            (Decimal("1") + total_return) ** Decimal(str(1 / elapsed_years)) - 1
        )

    peak = first.equity
    maximum_drawdown = Decimal("0")
    for point in result.equity_curve:
        peak = max(peak, point.equity)
        if peak > 0:
            drawdown = point.equity / peak - Decimal("1")
            maximum_drawdown = min(maximum_drawdown, drawdown)

    round_trips, open_round_trips = _round_trip_trades(result.fills)
    trade_metrics = _trade_metrics(round_trips)
    exits, winning_exits, gross_exit_pnl = _exit_statistics(result.fills)
    gross_turnover = sum((fill.gross_notional for fill in result.fills), ZERO)
    total_slippage = sum((fill.slippage for fill in result.fills), ZERO)
    winning_exit_rate = winning_exits / exits if exits else None

    exposure_values = [
        point.market_value / point.equity if point.equity > ZERO else ZERO
        for point in result.equity_curve
    ]
    average_exposure = sum(exposure_values, ZERO) / len(exposure_values)
    time_in_market = Decimal(
        sum(1 for point in result.equity_curve if point.market_value > ZERO)
    ) / Decimal(len(result.equity_curve))
    average_equity = sum((point.equity for point in result.equity_curve), ZERO) / Decimal(
        len(result.equity_curve)
    )
    annualized_turnover: float | None = None
    if elapsed_years > 0 and average_equity > ZERO:
        annualized_turnover = float(gross_turnover / average_equity / Decimal(str(elapsed_years)))

    open_positions = result.final_portfolio.get("positions", {})
    has_open_position = isinstance(open_positions, dict) and bool(open_positions)
    metrics: dict[str, Any] = {
        "run_id": result.run_id,
        "source": result.source_name,
        "start": first.timestamp.isoformat(),
        "end": last.timestamp.isoformat(),
        "observations": result.event_count,
        "initial_equity": str(initial_equity),
        "final_equity": str(final_equity),
        "total_return": _float(total_return),
        "annualized_return": annualized_return,
        "maximum_drawdown": _float(maximum_drawdown),
        "sharpe_ratio_zero_risk_free": sharpe_ratio,
        "sharpe_method": (
            "event-to-event reconciled equity returns; sample standard deviation; zero risk-free "
            "rate; annualization from median observation spacing"
        ),
        "annualization_periods": periods_per_year,
        "buy_and_hold_price_return": _float(price_benchmark_return),
        "buy_and_hold_total_return_proxy": (
            None if total_return_proxy is None else _float(total_return_proxy)
        ),
        "headline_benchmark": (
            "unavailable_missing_adjusted_close"
            if total_return_proxy is None
            else "adjusted_close_total_return_proxy"
        ),
        "excess_return_vs_buy_and_hold_price": _float(total_return - price_benchmark_return),
        "excess_return_vs_total_return_proxy": (
            None if total_return_proxy is None else _float(total_return - total_return_proxy)
        ),
        "average_exposure": _float(average_exposure),
        "time_in_market": _float(time_in_market),
        "annualized_turnover": annualized_turnover,
        "fills": result.fill_count,
        "round_trip_trades": trade_metrics["round_trip_trades"],
        "winning_trades": trade_metrics["winning_trades"],
        "losing_trades": trade_metrics["losing_trades"],
        "breakeven_trades": trade_metrics["breakeven_trades"],
        "trade_win_rate": trade_metrics["trade_win_rate"],
        "trade_expectancy": trade_metrics["trade_expectancy"],
        "profit_factor": trade_metrics["profit_factor"],
        "average_winning_trade": trade_metrics["average_winning_trade"],
        "average_losing_trade": trade_metrics["average_losing_trade"],
        "average_holding_seconds": trade_metrics["average_holding_seconds"],
        "open_round_trips": open_round_trips,
        "exit_fill_diagnostic": {
            "exit_fills": exits,
            "winning_exit_fills": winning_exits,
            "winning_exit_rate": winning_exit_rate,
            "gross_exit_pnl": str(gross_exit_pnl),
            "warning": "exit fills are not independent round-trip trades",
        },
        "gross_turnover": str(gross_turnover),
        "total_fees": str(last.fees),
        "total_modeled_slippage": str(total_slippage),
        "realized_pnl": str(last.realized_pnl),
        "unrealized_pnl": str(last.unrealized_pnl),
        "allowed_intents": result.allowed_intent_count,
        "denied_intents": result.denied_intent_count,
        "rejected_fills": result.rejected_fill_count,
        "pending_orders_at_end": result.pending_order_count,
        "canceled_orders_at_end": result.canceled_order_count,
        "end_of_test_policy": "cancel_pending_mark_positions_to_final_close",
        "open_position_at_end": has_open_position,
        "risk_halted": result.risk_halted,
        "risk_halt_reason": result.risk_halt_reason,
    }
    canonical = json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False)
    metrics["metrics_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return metrics


def write_report(result: SimulationResult, output_dir: str | Path) -> dict[str, Path]:
    """Write canonical JSON, readable Markdown, equity, fills, and trade artifacts."""

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    metrics = calculate_metrics(result)
    benchmark_description = (
        "unavailable because adjusted_close was not supplied; raw close price return is secondary"
        if not result.benchmark_prices
        else "adjusted-close buy-and-hold total-return proxy; raw price return is secondary"
    )
    payload = {
        "methodology": {
            "execution_mode": "offline deterministic simulation",
            "fill_model": "next eligible event open",
            "slippage_bps": result.config["slippage_bps"],
            "execution_price_buffer_bps": result.config["execution_price_buffer_bps"],
            "fee_per_order": result.config["fee_per_order"],
            "fee_per_share": result.config["fee_per_share"],
            "risk_free_rate": 0,
            "headline_benchmark": benchmark_description,
            "end_of_test": "cancel pending orders; mark open positions to the final close",
            "caveat": (
                "simulation results are not live performance or a guarantee of future results"
            ),
        },
        "config": result.config,
        "metrics": metrics,
        "final_portfolio": result.final_portfolio,
    }

    json_path = destination / "simulation_report.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    markdown_path = destination / "simulation_report.md"
    markdown_path.write_text(_markdown_report(payload), encoding="utf-8")

    equity_path = destination / "equity_curve.csv"
    with equity_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(result.equity_curve[0].as_dict()))
        writer.writeheader()
        writer.writerows(point.as_dict() for point in result.equity_curve)

    fills_path = destination / "fills.csv"
    fill_fields = (
        list(result.fills[0].as_dict())
        if result.fills
        else [
            "fill_id",
            "order_id",
            "intent_id",
            "correlation_id",
            "timestamp",
            "symbol",
            "side",
            "quantity",
            "price",
            "fee",
            "slippage",
            "gross_notional",
        ]
    )
    with fills_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fill_fields)
        writer.writeheader()
        writer.writerows(fill.as_dict() for fill in result.fills)

    trades = _round_trip_trades(result.fills)[0]
    trades_path = destination / "round_trip_trades.csv"
    trade_fields = list(trades[0].as_dict()) if trades else _trade_fieldnames()
    with trades_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=trade_fields)
        writer.writeheader()
        writer.writerows(trade.as_dict() for trade in trades)

    return {
        "json": json_path,
        "markdown": markdown_path,
        "equity_csv": equity_path,
        "fills_csv": fills_path,
        "round_trip_trades_csv": trades_path,
    }


def _periods_per_year(timestamps: list[datetime]) -> float:
    if len(timestamps) < 2:
        return 252.0
    gaps = [
        (current - previous).total_seconds()
        for previous, current in pairwise(timestamps)
        if current > previous
    ]
    if not gaps:
        return 252.0
    median_seconds = statistics.median(gaps)
    if median_seconds >= 20 * 60 * 60:
        return 252.0
    regular_session_seconds = 6.5 * 60 * 60
    return max(252.0, 252.0 * regular_session_seconds / median_seconds)


def _round_trip_trades(fills: tuple[Fill, ...]) -> tuple[tuple[RoundTripTrade, ...], int]:
    active: dict[str, _TradeAccumulator] = {}
    completed: list[RoundTripTrade] = []
    for fill in fills:
        accumulator = active.get(fill.symbol)
        if accumulator is None:
            if fill.side is Side.SELL:
                raise ValueError("fill history starts with an impossible sell")
            accumulator = _TradeAccumulator(
                symbol=fill.symbol,
                opened_at=fill.timestamp,
                first_fill_id=fill.fill_id,
                last_fill_id=fill.fill_id,
            )
            active[fill.symbol] = accumulator
        accumulator.last_fill_id = fill.fill_id
        accumulator.fees += fill.fee
        if fill.side is Side.BUY:
            accumulator.quantity += fill.quantity
            accumulator.entry_quantity += fill.quantity
            accumulator.entry_notional += fill.gross_notional
            accumulator.entry_fill_count += 1
            continue

        if fill.quantity > accumulator.quantity:
            raise ValueError("fill history contains an impossible sell quantity")
        accumulator.quantity -= fill.quantity
        accumulator.exit_quantity += fill.quantity
        accumulator.exit_notional += fill.gross_notional
        accumulator.exit_fill_count += 1
        if accumulator.quantity != 0:
            continue
        if accumulator.entry_quantity != accumulator.exit_quantity:
            raise ValueError("closed round trip has mismatched entry and exit quantities")
        gross_pnl = accumulator.exit_notional - accumulator.entry_notional
        completed.append(
            RoundTripTrade(
                trade_id=stable_id(
                    "trade",
                    accumulator.symbol,
                    accumulator.opened_at.isoformat(),
                    fill.timestamp.isoformat(),
                    accumulator.first_fill_id,
                    accumulator.last_fill_id,
                ),
                symbol=accumulator.symbol,
                opened_at=accumulator.opened_at,
                closed_at=fill.timestamp,
                entry_fill_count=accumulator.entry_fill_count,
                exit_fill_count=accumulator.exit_fill_count,
                entry_quantity=accumulator.entry_quantity,
                exit_quantity=accumulator.exit_quantity,
                average_entry_price=(accumulator.entry_notional / accumulator.entry_quantity),
                average_exit_price=accumulator.exit_notional / accumulator.exit_quantity,
                gross_pnl=gross_pnl,
                fees=accumulator.fees,
                net_pnl=gross_pnl - accumulator.fees,
                holding_seconds=int((fill.timestamp - accumulator.opened_at).total_seconds()),
            )
        )
        del active[fill.symbol]
    return tuple(completed), len(active)


def _trade_metrics(trades: tuple[RoundTripTrade, ...]) -> dict[str, Any]:
    winners = [trade.net_pnl for trade in trades if trade.net_pnl > ZERO]
    losers = [trade.net_pnl for trade in trades if trade.net_pnl < ZERO]
    breakeven = sum(1 for trade in trades if trade.net_pnl == ZERO)
    total = len(trades)
    net_pnls = [trade.net_pnl for trade in trades]
    profit_factor: float | None = None
    if losers:
        profit_factor = _float(sum(winners, ZERO) / abs(sum(losers, ZERO)))

    return {
        "round_trip_trades": total,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "breakeven_trades": breakeven,
        "trade_win_rate": len(winners) / total if total else None,
        "trade_expectancy": None if not net_pnls else str(sum(net_pnls, ZERO) / total),
        "profit_factor": profit_factor,
        "average_winning_trade": None if not winners else str(sum(winners, ZERO) / len(winners)),
        "average_losing_trade": None if not losers else str(sum(losers, ZERO) / len(losers)),
        "average_holding_seconds": (
            None if not trades else sum(trade.holding_seconds for trade in trades) / total
        ),
    }


def _exit_statistics(fills: tuple[Fill, ...]) -> tuple[int, int, Decimal]:
    quantity = 0
    average_price = ZERO
    exits = 0
    winning_exits = 0
    gross_pnl = ZERO
    for fill in fills:
        if fill.side is Side.BUY:
            old_cost = average_price * quantity
            quantity += fill.quantity
            average_price = (old_cost + fill.price * fill.quantity) / quantity
            continue
        if fill.quantity > quantity:
            raise ValueError("fill history contains an impossible sell quantity")
        exit_pnl = (fill.price - average_price) * fill.quantity - fill.fee
        gross_pnl += exit_pnl
        exits += 1
        if exit_pnl > 0:
            winning_exits += 1
        quantity -= fill.quantity
        if quantity == 0:
            average_price = ZERO
    return exits, winning_exits, gross_pnl


def _trade_fieldnames() -> list[str]:
    return [
        "trade_id",
        "symbol",
        "opened_at",
        "closed_at",
        "entry_fill_count",
        "exit_fill_count",
        "entry_quantity",
        "exit_quantity",
        "average_entry_price",
        "average_exit_price",
        "gross_pnl",
        "fees",
        "net_pnl",
        "holding_seconds",
    ]


def _float(value: Decimal) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("metric is not finite")
    return number


def _markdown_report(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    values = [
        ("Run ID", metrics["run_id"]),
        ("Source", metrics["source"]),
        ("Period", f"{metrics['start']} to {metrics['end']}"),
        ("Observations", f"{metrics['observations']:,}"),
        ("Initial equity", metrics["initial_equity"]),
        ("Final equity", metrics["final_equity"]),
        ("Strategy return", _percent(metrics["total_return"])),
        (
            "Buy-and-hold total-return proxy",
            _percent(metrics["buy_and_hold_total_return_proxy"]),
        ),
        ("Buy-and-hold price return", _percent(metrics["buy_and_hold_price_return"])),
        (
            "Excess versus total-return proxy",
            _percent(metrics["excess_return_vs_total_return_proxy"]),
        ),
        ("Maximum drawdown", _percent(metrics["maximum_drawdown"])),
        (
            "Sharpe ratio (declared method)",
            _optional_number(metrics["sharpe_ratio_zero_risk_free"]),
        ),
        ("Average exposure", _percent(metrics["average_exposure"])),
        ("Time in market", _percent(metrics["time_in_market"])),
        ("Annualized turnover", _optional_number(metrics["annualized_turnover"])),
        ("Round-trip trades", metrics["round_trip_trades"]),
        ("Trade win rate", _percent(metrics["trade_win_rate"])),
        ("Trade expectancy", metrics["trade_expectancy"] or "n/a"),
        ("Profit factor", _optional_number(metrics["profit_factor"])),
        ("Fills", metrics["fills"]),
        ("Total fees", metrics["total_fees"]),
        ("Modeled slippage", metrics["total_modeled_slippage"]),
        ("Canceled pending orders", metrics["canceled_orders_at_end"]),
        ("Open position at end", metrics["open_position_at_end"]),
        ("Risk halted", metrics["risk_halted"]),
    ]
    lines = [
        "# Simulation Report",
        "",
        "> This is an offline deterministic simulation, not live performance or financial advice.",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    lines.extend(f"| {name} | {value} |" for name, value in values)
    lines.extend(
        [
            "",
            "## Methodology",
            "",
            "Orders use a conservative declared execution-price buffer during risk sizing, then "
            "fill at the next eligible event open with the declared fees and slippage. Pending "
            "orders are canceled at the final observation; open positions are marked to the final "
            "close rather than liquidated at a fabricated price. Round-trip statistics group all "
            "fills from flat to flat. The headline benchmark is available only when every row "
            "supplies adjusted_close; raw close return remains a secondary price diagnostic. "
            "Every decision, order, cancellation, fill, and equity point is retained in the "
            "ledger.",
            "",
            f"**Sharpe assumptions:** {metrics['sharpe_method']}",
            "",
            f"**Metrics checksum:** `{metrics['metrics_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _optional_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    if math.isinf(value):
        return "∞"
    return f"{value:.3f}"
