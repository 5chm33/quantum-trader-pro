from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quantum_trader.application.engine import SimulationResult
from quantum_trader.application.reporting import calculate_metrics, write_report
from quantum_trader.domain.models import EquityPoint, Fill, Side


def timestamp(day: int) -> datetime:
    return datetime(2024, 1, day, tzinfo=UTC)


def equity(day: int, value: str, market_value: str = "0") -> EquityPoint:
    total = Decimal(value)
    market = Decimal(market_value)
    return EquityPoint(
        timestamp=timestamp(day),
        cash=total - market,
        market_value=market,
        equity=total,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        fees=Decimal("0"),
    )


def fill(day: int, side: Side, quantity: int, price: str, fee: str = "1") -> Fill:
    label = f"{day}-{side.value}-{quantity}-{price}"
    return Fill(
        fill_id=f"fill-{label}",
        order_id=f"order-{label}",
        intent_id=f"intent-{label}",
        correlation_id=f"event-{day}",
        timestamp=timestamp(day),
        symbol="TEST",
        side=side,
        quantity=quantity,
        price=Decimal(price),
        fee=Decimal(fee),
        slippage=Decimal("0"),
    )


def result(
    *,
    points: tuple[EquityPoint, ...],
    prices: tuple[Decimal, ...],
    benchmark_prices: tuple[Decimal, ...] = (),
    fills: tuple[Fill, ...] = (),
    final_positions: dict[str, object] | None = None,
) -> SimulationResult:
    benchmark_series = (
        tuple(
            (point.timestamp, price) for point, price in zip(points, benchmark_prices, strict=True)
        )
        if benchmark_prices
        else ()
    )
    return SimulationResult(
        run_id="run-test",
        source_name="fixture:reporting",
        config={
            "slippage_bps": "1",
            "execution_price_buffer_bps": "1000",
            "fee_per_order": "1",
            "fee_per_share": "0",
        },
        event_count=len(points),
        signal_count=len(points),
        intent_count=len(fills),
        allowed_intent_count=len(fills),
        denied_intent_count=0,
        fill_count=len(fills),
        rejected_fill_count=0,
        pending_order_count=0,
        canceled_order_count=0,
        equity_curve=points,
        fills=fills,
        market_prices=tuple(
            (point.timestamp, price) for point, price in zip(points, prices, strict=True)
        ),
        benchmark_prices=benchmark_series,
        final_portfolio={
            "initial_cash": "1000",
            "cash": str(points[-1].cash),
            "realized_pnl": "0",
            "total_fees": "0",
            "positions": final_positions or {},
        },
        risk_halted=False,
        risk_halt_reason=None,
    )


def test_adjusted_close_is_headline_benchmark_when_complete() -> None:
    simulation = result(
        points=(equity(1, "1000"), equity(2, "1050")),
        prices=(Decimal("100"), Decimal("110")),
        benchmark_prices=(Decimal("80"), Decimal("100")),
    )

    metrics = calculate_metrics(simulation)

    assert metrics["headline_benchmark"] == "adjusted_close_total_return_proxy"
    assert metrics["total_return"] == pytest.approx(0.05)
    assert metrics["buy_and_hold_price_return"] == pytest.approx(0.10)
    assert metrics["buy_and_hold_total_return_proxy"] == pytest.approx(0.25)
    assert metrics["excess_return_vs_total_return_proxy"] == pytest.approx(-0.20)


def test_round_trip_attribution_groups_partial_fills_and_writes_trade_csv(
    tmp_path: Path,
) -> None:
    fills = (
        fill(1, Side.BUY, 4, "100"),
        fill(2, Side.BUY, 6, "110"),
        fill(3, Side.SELL, 5, "120"),
        fill(4, Side.SELL, 5, "90"),
        fill(5, Side.BUY, 2, "50"),
        fill(6, Side.SELL, 2, "60"),
    )
    simulation = result(
        points=tuple(
            equity(day, value)
            for day, value in enumerate(("1000", "990", "1005", "986", "986", "1004"), start=1)
        ),
        prices=tuple(Decimal(value) for value in ("100", "110", "120", "90", "50", "60")),
        fills=fills,
    )

    metrics = calculate_metrics(simulation)
    paths = write_report(simulation, tmp_path)

    assert metrics["round_trip_trades"] == 2
    assert metrics["winning_trades"] == 1
    assert metrics["losing_trades"] == 1
    assert metrics["trade_win_rate"] == pytest.approx(0.5)
    assert Decimal(metrics["trade_expectancy"]) == Decimal("2")
    assert metrics["profit_factor"] == pytest.approx(float(Decimal("18") / Decimal("14")))
    assert Decimal(metrics["average_winning_trade"]) == Decimal("18")
    assert Decimal(metrics["average_losing_trade"]) == Decimal("-14")

    with paths["round_trip_trades_csv"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["entry_fill_count"] == "2"
    assert rows[0]["exit_fill_count"] == "2"
    assert rows[0]["net_pnl"] == "-14"
    assert rows[1]["net_pnl"] == "18"


def test_incomplete_position_is_not_misreported_as_closed_trade() -> None:
    simulation = result(
        points=(equity(1, "1000"), equity(2, "1010", "110")),
        prices=(Decimal("100"), Decimal("110")),
        fills=(fill(1, Side.BUY, 1, "100"),),
        final_positions={"TEST": {"symbol": "TEST", "quantity": 1, "average_price": "100"}},
    )

    metrics = calculate_metrics(simulation)

    assert metrics["round_trip_trades"] == 0
    assert metrics["open_round_trips"] == 1
    assert metrics["trade_win_rate"] is None
    assert metrics["open_position_at_end"] is True
