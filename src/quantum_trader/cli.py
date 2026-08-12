"""Command-line interface for deterministic trading research workflows."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from importlib.resources import as_file, files
from pathlib import Path

from quantum_trader import __version__
from quantum_trader.adapters.csv_replay import CsvReplayMarketData
from quantum_trader.adapters.simulated_broker import SimulatedBroker
from quantum_trader.adapters.sqlite_store import SQLiteEventStore
from quantum_trader.application.engine import SimulationEngine
from quantum_trader.application.lifecycle import SingleInstanceLock
from quantum_trader.application.reporting import calculate_metrics, write_report
from quantum_trader.config import ExecutionPolicy, SimulationConfig
from quantum_trader.domain.portfolio import Portfolio
from quantum_trader.domain.risk import RiskLimits, RiskManager
from quantum_trader.domain.strategy import MovingAverageCrossover


def decimal_argument(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid decimal value: {value}") from exc
    if not parsed.is_finite():
        raise argparse.ArgumentTypeError("decimal values must be finite")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quantum-trader",
        description="Deterministic trading research engine with an offline-safe default.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser(
        "demo",
        help="Run the bundled offline demo with no data download or broker credentials.",
    )
    demo.add_argument(
        "--output",
        type=Path,
        help=(
            "Optional artifact directory; the default is a unique folder under the current "
            "directory."
        ),
    )

    simulate = subparsers.add_parser(
        "simulate",
        help="Replay a local OHLCV CSV through the strategy, risk, broker, and ledger.",
    )
    simulate.add_argument("--data", type=Path, required=True, help="Local OHLCV CSV path")
    simulate.add_argument("--output", type=Path, required=True, help="Artifact directory")
    simulate.add_argument("--symbol", default="DEMO")
    simulate.add_argument("--mode", default="simulation", choices=["simulation"])
    simulate.add_argument("--initial-cash", type=decimal_argument, default=Decimal("100000"))
    simulate.add_argument("--fast-window", type=int, default=20)
    simulate.add_argument("--slow-window", type=int, default=50)
    simulate.add_argument("--invested-fraction", type=decimal_argument, default=Decimal("0.95"))
    simulate.add_argument("--slippage-bps", type=decimal_argument, default=Decimal("1"))
    simulate.add_argument("--fee-per-order", type=decimal_argument, default=Decimal("0"))
    simulate.add_argument("--fee-per-share", type=decimal_argument, default=Decimal("0"))
    simulate.add_argument("--max-position-fraction", type=decimal_argument, default=Decimal("0.25"))
    simulate.add_argument("--max-order-notional", type=decimal_argument, default=Decimal("25000"))
    simulate.add_argument(
        "--min-cash-reserve-fraction", type=decimal_argument, default=Decimal("0.05")
    )
    simulate.add_argument("--max-drawdown-fraction", type=decimal_argument, default=Decimal("0.15"))
    simulate.add_argument("--max-realized-loss", type=decimal_argument, default=Decimal("5000"))
    simulate.add_argument("--maximum-gap-days", type=int, default=7)
    simulate.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace only existing simulation artifacts inside the chosen output directory.",
    )

    subparsers.add_parser("preflight", help="Verify the local safety boundary and launch support.")
    subparsers.add_parser("version", help="Print the installed package version.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "demo":
            return _demo(arguments)
        if arguments.command == "simulate":
            return _simulate(arguments)
        if arguments.command == "preflight":
            return _preflight()
        if arguments.command == "version":
            print(__version__)
            return 0
        parser.error(f"unsupported command: {arguments.command}")
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _demo(arguments: argparse.Namespace) -> int:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = arguments.output or Path.cwd() / "quantum-trader-demo-runs" / timestamp
    resource = files("quantum_trader").joinpath("data", "demo_daily.csv")
    with as_file(resource) as data_path:
        simulation_arguments = argparse.Namespace(
            data=data_path,
            output=output_dir,
            symbol="DEMO",
            mode="simulation",
            initial_cash=Decimal("100000"),
            fast_window=20,
            slow_window=50,
            invested_fraction=Decimal("0.25"),
            slippage_bps=Decimal("2"),
            fee_per_order=Decimal("0"),
            fee_per_share=Decimal("0.005"),
            max_position_fraction=Decimal("0.25"),
            max_order_notional=Decimal("25000"),
            min_cash_reserve_fraction=Decimal("0.05"),
            max_drawdown_fraction=Decimal("0.30"),
            max_realized_loss=Decimal("10000"),
            maximum_gap_days=7,
            overwrite=False,
        )
        return _simulate(simulation_arguments)


def _simulate(arguments: argparse.Namespace) -> int:
    mode = ExecutionPolicy.require_simulation(arguments.mode)
    output_dir = arguments.output.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_paths = [
        output_dir / "events.sqlite3",
        output_dir / "events.sqlite3-wal",
        output_dir / "events.sqlite3-shm",
        output_dir / "simulation_report.json",
        output_dir / "simulation_report.md",
        output_dir / "equity_curve.csv",
        output_dir / "fills.csv",
    ]
    existing = [path for path in artifact_paths if path.exists()]
    if existing and not arguments.overwrite:
        names = ", ".join(path.name for path in existing)
        raise ValueError(f"output artifacts already exist ({names}); pass --overwrite to replace")
    if arguments.overwrite:
        for path in existing:
            path.unlink()

    limits = RiskLimits(
        max_position_fraction=arguments.max_position_fraction,
        max_order_notional=arguments.max_order_notional,
        min_cash_reserve_fraction=arguments.min_cash_reserve_fraction,
        max_drawdown_fraction=arguments.max_drawdown_fraction,
        max_realized_loss=arguments.max_realized_loss,
    )
    config = SimulationConfig(
        mode=mode,
        symbol=arguments.symbol.upper(),
        initial_cash=arguments.initial_cash,
        fast_window=arguments.fast_window,
        slow_window=arguments.slow_window,
        invested_fraction=arguments.invested_fraction,
        slippage_bps=arguments.slippage_bps,
        fee_per_order=arguments.fee_per_order,
        fee_per_share=arguments.fee_per_share,
        risk_limits=limits,
    )
    market_data = CsvReplayMarketData(
        arguments.data,
        symbol=config.symbol,
        maximum_gap=(
            None if arguments.maximum_gap_days == 0 else timedelta(days=arguments.maximum_gap_days)
        ),
    )
    strategy = MovingAverageCrossover(
        fast_window=config.fast_window,
        slow_window=config.slow_window,
        invested_fraction=config.invested_fraction,
    )
    portfolio = Portfolio(config.initial_cash)
    risk_manager = RiskManager(config.risk_limits)
    broker = SimulatedBroker(
        slippage_bps=config.slippage_bps,
        fee_per_order=config.fee_per_order,
        fee_per_share=config.fee_per_share,
    )

    lock = SingleInstanceLock(output_dir / ".simulation.lock")
    with lock:
        event_store = SQLiteEventStore(output_dir / "events.sqlite3")
        try:
            engine = SimulationEngine(
                config=config,
                market_data=market_data,
                strategy=strategy,
                risk_manager=risk_manager,
                portfolio=portfolio,
                broker=broker,
                event_store=event_store,
            )
            result = engine.run()
            report_paths = write_report(result, output_dir)
            metrics = calculate_metrics(result)
        finally:
            event_store.close()

    summary = {
        "status": "completed",
        "mode": mode.value,
        "run_id": result.run_id,
        "metrics_sha256": metrics["metrics_sha256"],
        "events": result.event_count,
        "fills": result.fill_count,
        "risk_halted": result.risk_halted,
        "report": str(report_paths["markdown"]),
        "ledger": str(output_dir / "events.sqlite3"),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _preflight() -> int:
    ExecutionPolicy.require_simulation("simulation")
    payload = {
        "status": "ready",
        "version": __version__,
        "allowed_modes": ["simulation"],
        "network_required": False,
        "broker_credentials_required": False,
        "live_trading_implemented": False,
        "paper_trading_implemented": False,
        "one_click_demo": True,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
