"""Transparent simulation metrics and reproducible report artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from quantum_trader.application.engine import SimulationResult
from quantum_trader.domain.models import Fill, Side


def calculate_metrics(result: SimulationResult) -> dict[str, Any]:
    """Calculate declared, deterministic metrics from the reconciled equity curve."""

    if not result.equity_curve or not result.market_prices:
        raise ValueError("simulation result contains no observations")
    first = result.equity_curve[0]
    last = result.equity_curve[-1]
    initial_equity = first.equity
    final_equity = last.equity
    total_return = final_equity / initial_equity - Decimal("1")
    benchmark_return = result.market_prices[-1][1] / result.market_prices[0][1] - Decimal("1")

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

    elapsed_seconds = (
        result.equity_curve[-1].timestamp - result.equity_curve[0].timestamp
    ).total_seconds()
    annualized_return: float | None = None
    if elapsed_seconds >= 30 * 24 * 60 * 60 and total_return > Decimal("-1"):
        years = elapsed_seconds / (365.2425 * 24 * 60 * 60)
        annualized_return = float((Decimal("1") + total_return) ** Decimal(str(1 / years)) - 1)

    peak = result.equity_curve[0].equity
    maximum_drawdown = Decimal("0")
    for point in result.equity_curve:
        peak = max(peak, point.equity)
        if peak > 0:
            drawdown = point.equity / peak - Decimal("1")
            maximum_drawdown = min(maximum_drawdown, drawdown)

    exits, winning_exits, gross_exit_pnl = _exit_statistics(result.fills)
    gross_turnover = sum((fill.gross_notional for fill in result.fills), Decimal("0"))
    total_slippage = sum((fill.slippage for fill in result.fills), Decimal("0"))
    winning_exit_rate = winning_exits / exits if exits else None

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
        "annualization_periods": periods_per_year,
        "buy_and_hold_price_return": _float(benchmark_return),
        "excess_return_vs_buy_and_hold_price": _float(total_return - benchmark_return),
        "fills": result.fill_count,
        "exit_fills": exits,
        "winning_exit_fills": winning_exits,
        "winning_exit_rate": winning_exit_rate,
        "gross_exit_pnl": str(gross_exit_pnl),
        "gross_turnover": str(gross_turnover),
        "total_fees": str(last.fees),
        "total_modeled_slippage": str(total_slippage),
        "realized_pnl": str(last.realized_pnl),
        "unrealized_pnl": str(last.unrealized_pnl),
        "allowed_intents": result.allowed_intent_count,
        "denied_intents": result.denied_intent_count,
        "rejected_fills": result.rejected_fill_count,
        "pending_orders_at_end": result.pending_order_count,
        "risk_halted": result.risk_halted,
        "risk_halt_reason": result.risk_halt_reason,
    }
    canonical = json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False)
    metrics["metrics_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return metrics


def write_report(result: SimulationResult, output_dir: str | Path) -> dict[str, Path]:
    """Write canonical JSON, readable Markdown, equity CSV, and fills CSV artifacts."""

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    metrics = calculate_metrics(result)
    payload = {
        "methodology": {
            "execution_mode": "offline deterministic simulation",
            "fill_model": "next eligible event open",
            "slippage_bps": result.config["slippage_bps"],
            "fee_per_order": result.config["fee_per_order"],
            "fee_per_share": result.config["fee_per_share"],
            "risk_free_rate": 0,
            "benchmark": (
                "unadjusted buy-and-hold price return over identical source observations; "
                "dividends excluded"
            ),
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
    markdown_path.write_text(
        _markdown_report(payload),
        encoding="utf-8",
    )

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

    return {
        "json": json_path,
        "markdown": markdown_path,
        "equity_csv": equity_path,
        "fills_csv": fills_path,
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


def _exit_statistics(fills: tuple[Fill, ...]) -> tuple[int, int, Decimal]:
    quantity = 0
    average_price = Decimal("0")
    exits = 0
    winning_exits = 0
    gross_pnl = Decimal("0")
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
            average_price = Decimal("0")
    return exits, winning_exits, gross_pnl


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
        ("Total return", _percent(metrics["total_return"])),
        (
            "Buy-and-hold price return (dividends excluded)",
            _percent(metrics["buy_and_hold_price_return"]),
        ),
        ("Maximum drawdown", _percent(metrics["maximum_drawdown"])),
        ("Sharpe ratio", _optional_number(metrics["sharpe_ratio_zero_risk_free"])),
        ("Fills", metrics["fills"]),
        ("Total fees", metrics["total_fees"]),
        ("Modeled slippage", metrics["total_modeled_slippage"]),
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
            "Orders are generated from explicit historical observations, pass through fail-closed "
            "risk checks, and fill at the next eligible event open with the declared fee and "
            "slippage model. The benchmark is the unadjusted buy-and-hold price return over the "
            "identical source period; dividends are excluded. "
            "Every decision, order, fill, and equity point is retained in the SQLite event ledger.",
            "",
            f"**Metrics checksum:** `{metrics['metrics_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _optional_number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"
