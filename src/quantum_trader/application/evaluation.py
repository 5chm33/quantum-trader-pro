"""Preregistered walk-forward evaluation and one-time locked holdout."""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from quantum_trader.adapters.csv_replay import CsvReplayMarketData
from quantum_trader.adapters.simulated_broker import SimulatedBroker
from quantum_trader.application.engine import SimulationEngine, SimulationResult
from quantum_trader.application.reporting import calculate_metrics
from quantum_trader.config import SimulationConfig
from quantum_trader.domain.models import MarketEvent, Signal, stable_id
from quantum_trader.domain.portfolio import Portfolio
from quantum_trader.domain.risk import RiskLimits, RiskManager
from quantum_trader.domain.strategy import MovingAverageCrossover


@dataclass(frozen=True, slots=True)
class Candidate:
    """One frozen strategy specification from the protocol grid."""

    fast_window: int
    slow_window: int
    invested_fraction: Decimal

    @property
    def candidate_id(self) -> str:
        return stable_id(
            "candidate",
            self.fast_window,
            self.slow_window,
            self.invested_fraction,
        )


@dataclass(frozen=True, slots=True)
class PanelAsset:
    """A checksummed, fully validated market-event sequence."""

    symbol: str
    role: str
    csv_path: Path
    csv_sha256: str
    events: tuple[MarketEvent, ...]


@dataclass(slots=True)
class DigestEventStore:
    """Memory-bounded event evidence for high-volume evaluation trials."""

    sequence: int = 0
    _digest: Any = field(init=False, repr=False)
    event_counts: Counter[str] = field(init=False)

    def __post_init__(self) -> None:
        self._digest = hashlib.sha256()
        self.event_counts = Counter()

    @property
    def hexdigest(self) -> str:
        return str(self._digest.hexdigest())

    def append(
        self,
        *,
        event_type: str,
        timestamp: datetime,
        correlation_id: str,
        payload: Mapping[str, Any],
    ) -> int:
        self.sequence += 1
        canonical = json.dumps(
            {
                "sequence": self.sequence,
                "event_type": event_type,
                "timestamp": timestamp.isoformat(),
                "correlation_id": correlation_id,
                "payload": dict(payload),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self._digest.update(canonical.encode("utf-8"))
        self._digest.update(b"\n")
        self.event_counts[event_type] += 1
        return self.sequence

    def iter_events(self) -> Iterator[dict[str, Any]]:
        return iter(())

    def close(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class SequenceMarketData:
    """A deterministic in-memory market-data adapter for one trial slice."""

    events: tuple[MarketEvent, ...]
    source_name: str

    def stream(self) -> Iterator[MarketEvent]:
        yield from self.events


class EvaluationStrategy:
    """Warm the strategy on context bars without allowing pre-evaluation orders."""

    def __init__(self, candidate: Candidate, evaluation_start: datetime) -> None:
        self._delegate = MovingAverageCrossover(
            fast_window=candidate.fast_window,
            slow_window=candidate.slow_window,
            invested_fraction=candidate.invested_fraction,
        )
        self._evaluation_start = evaluation_start

    @property
    def name(self) -> str:
        return f"{self._delegate.name}_walk_forward"

    def on_market_event(self, event: MarketEvent) -> Signal:
        signal = self._delegate.on_market_event(event)
        if event.timestamp >= self._evaluation_start:
            return signal
        return Signal(
            timestamp=event.timestamp,
            symbol=event.symbol,
            target_fraction=Decimal("0"),
            rationale="research_context_only; trading disabled before evaluation window",
            correlation_id=event.correlation_id,
        )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_protocol(path: Path) -> dict[str, Any]:
    protocol = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    required = {
        "protocol_id",
        "protocol_version",
        "data",
        "windows",
        "candidate_grid",
        "base_configuration",
        "cost_scenarios",
        "selection",
        "robustness",
        "locked_holdout",
        "promotion_gates",
    }
    missing = sorted(required - protocol.keys())
    if missing:
        raise ValueError(f"protocol missing required keys: {', '.join(missing)}")
    if not protocol["data"].get("adjusted_close_required"):
        raise ValueError("protocol must require adjusted_close for headline evaluation")
    windows = protocol["windows"]
    for key in (
        "train_bars",
        "validation_bars",
        "test_bars",
        "step_bars",
        "locked_holdout_bars",
        "minimum_folds_per_asset",
    ):
        if int(windows[key]) <= 0:
            raise ValueError(f"protocol window {key} must be positive")
    if not any(item["name"] == "base" for item in protocol["cost_scenarios"]):
        raise ValueError("protocol cost scenarios must include base")
    enumerate_candidates(protocol)
    return protocol


def enumerate_candidates(protocol: Mapping[str, Any]) -> tuple[Candidate, ...]:
    grid = protocol["candidate_grid"]
    candidates = {
        Candidate(int(fast), int(slow), Decimal(str(fraction)))
        for fast in grid["fast_windows"]
        for slow in grid["slow_windows"]
        for fraction in grid["invested_fractions"]
        if int(fast) < int(slow)
    }
    if not candidates:
        raise ValueError("protocol candidate grid produced no valid candidates")
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.fast_window,
                item.slow_window,
                item.invested_fraction,
            ),
        )
    )


def load_panel(
    *,
    protocol: Mapping[str, Any],
    protocol_path: Path,
    data_dir: Path,
) -> tuple[PanelAsset, ...]:
    manifest_path = data_dir / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    protocol_digest = sha256_file(protocol_path)
    if manifest.get("protocol_id") != protocol["protocol_id"]:
        raise ValueError("source manifest protocol ID does not match")
    if manifest.get("protocol_sha256") != protocol_digest:
        raise ValueError("source manifest protocol digest does not match")
    manifest_assets = {item["symbol"]: item for item in manifest.get("assets", [])}
    maximum_gap = timedelta(days=int(protocol["data"]["maximum_gap_days"]))
    panel: list[PanelAsset] = []
    for declared in protocol["data"]["assets"]:
        symbol = str(declared["symbol"])
        item = manifest_assets.get(symbol)
        if item is None:
            raise ValueError(f"source manifest is missing {symbol}")
        csv_path = data_dir / str(item["normalized_csv"])
        actual_digest = sha256_file(csv_path)
        if actual_digest != item["csv_sha256"]:
            raise ValueError(f"source checksum mismatch for {symbol}")
        events = tuple(
            CsvReplayMarketData(
                csv_path,
                symbol=symbol,
                maximum_gap=maximum_gap,
            ).stream()
        )
        if any(event.adjusted_close is None for event in events):
            raise ValueError(f"{symbol} does not have a complete adjusted-close series")
        panel.append(
            PanelAsset(
                symbol=symbol,
                role=str(declared["role"]),
                csv_path=csv_path,
                csv_sha256=actual_digest,
                events=events,
            )
        )
    return tuple(panel)


def _decimal(mapping: Mapping[str, Any], key: str) -> Decimal:
    value = Decimal(str(mapping[key]))
    if not value.is_finite():
        raise ValueError(f"configuration {key} must be finite")
    return value


def _run_slice(
    *,
    protocol: Mapping[str, Any],
    asset: PanelAsset,
    candidate: Candidate,
    context_start: int,
    evaluation_start: int,
    evaluation_end: int,
    cost_multiplier: Decimal,
    execution_buffer_bps: Decimal,
) -> tuple[SimulationResult, dict[str, Any], DigestEventStore]:
    if not 0 <= context_start < evaluation_start < evaluation_end <= len(asset.events):
        raise ValueError("invalid evaluation slice indices")
    base = protocol["base_configuration"]
    limits = RiskLimits(
        max_position_fraction=_decimal(base, "max_position_fraction"),
        max_order_notional=_decimal(base, "max_order_notional"),
        min_cash_reserve_fraction=_decimal(base, "min_cash_reserve_fraction"),
        max_drawdown_fraction=_decimal(base, "max_drawdown_fraction"),
        max_realized_loss=_decimal(base, "max_realized_loss"),
    )
    slippage = _decimal(base, "slippage_bps") * cost_multiplier
    fee_per_order = _decimal(base, "fee_per_order") * cost_multiplier
    fee_per_share = _decimal(base, "fee_per_share") * cost_multiplier
    config = SimulationConfig(
        symbol=asset.symbol,
        initial_cash=_decimal(base, "initial_cash"),
        fast_window=candidate.fast_window,
        slow_window=candidate.slow_window,
        invested_fraction=candidate.invested_fraction,
        slippage_bps=slippage,
        execution_price_buffer_bps=execution_buffer_bps,
        fee_per_order=fee_per_order,
        fee_per_share=fee_per_share,
        risk_limits=limits,
    )
    trial_events = asset.events[context_start:evaluation_end]
    evaluation_timestamp = asset.events[evaluation_start].timestamp
    source_name = (
        f"walk-forward:{asset.symbol}:sha256:{asset.csv_sha256}:"
        f"context:{context_start}:evaluation:{evaluation_start}:{evaluation_end}"
    )
    store = DigestEventStore()
    result = SimulationEngine(
        config=config,
        market_data=SequenceMarketData(trial_events, source_name),
        strategy=EvaluationStrategy(candidate, evaluation_timestamp),
        risk_manager=RiskManager(
            limits,
            slippage_bps=slippage,
            execution_price_buffer_bps=execution_buffer_bps,
            fee_per_order=fee_per_order,
            fee_per_share=fee_per_share,
        ),
        portfolio=Portfolio(config.initial_cash),
        broker=SimulatedBroker(
            slippage_bps=slippage,
            fee_per_order=fee_per_order,
            fee_per_share=fee_per_share,
        ),
        event_store=store,
        evaluation_start=evaluation_timestamp,
    ).run()
    return result, calculate_metrics(result), store


def _trial_row(
    *,
    protocol: Mapping[str, Any],
    asset: PanelAsset,
    candidate: Candidate,
    fold_id: str,
    stage: str,
    scenario: str,
    context_start: int,
    evaluation_start: int,
    evaluation_end: int,
    cost_multiplier: Decimal,
    execution_buffer_bps: Decimal,
) -> dict[str, Any]:
    result, metrics, store = _run_slice(
        protocol=protocol,
        asset=asset,
        candidate=candidate,
        context_start=context_start,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        cost_multiplier=cost_multiplier,
        execution_buffer_bps=execution_buffer_bps,
    )
    excess = metrics["excess_return_vs_total_return_proxy"]
    selection = protocol["selection"]["eligibility"]
    drawdown_floor = float(selection["maximum_drawdown_floor"])
    eligible = (
        excess is not None
        and result.risk_halted is bool(selection["risk_halted"])
        and result.pending_order_count == int(selection["pending_orders_at_end"])
        and float(metrics["maximum_drawdown"]) >= drawdown_floor
    )
    trial_id = stable_id(
        "research-trial",
        protocol["protocol_id"],
        asset.symbol,
        fold_id,
        stage,
        scenario,
        candidate.candidate_id,
        context_start,
        evaluation_start,
        evaluation_end,
        cost_multiplier,
        execution_buffer_bps,
    )
    return {
        "trial_id": trial_id,
        "protocol_id": protocol["protocol_id"],
        "symbol": asset.symbol,
        "fold_id": fold_id,
        "stage": stage,
        "scenario": scenario,
        "candidate_id": candidate.candidate_id,
        "fast_window": candidate.fast_window,
        "slow_window": candidate.slow_window,
        "invested_fraction": str(candidate.invested_fraction),
        "context_start_index": context_start,
        "evaluation_start_index": evaluation_start,
        "evaluation_end_index_exclusive": evaluation_end,
        "evaluation_start": asset.events[evaluation_start].timestamp.isoformat(),
        "evaluation_end": asset.events[evaluation_end - 1].timestamp.isoformat(),
        "cost_multiplier": str(cost_multiplier),
        "execution_price_buffer_bps": str(execution_buffer_bps),
        "eligible_for_selection": eligible,
        "run_id": result.run_id,
        "event_digest": store.hexdigest,
        "event_count": result.event_count,
        "fill_count": result.fill_count,
        "round_trip_trades": metrics["round_trip_trades"],
        "open_round_trips": metrics["open_round_trips"],
        "total_return": metrics["total_return"],
        "annualized_return": metrics["annualized_return"],
        "maximum_drawdown": metrics["maximum_drawdown"],
        "headline_benchmark": metrics["headline_benchmark"],
        "buy_and_hold_total_return_proxy": metrics["buy_and_hold_total_return_proxy"],
        "excess_return_vs_total_return_proxy": excess,
        "average_exposure": metrics["average_exposure"],
        "time_in_market": metrics["time_in_market"],
        "annualized_turnover": metrics["annualized_turnover"],
        "trade_win_rate": metrics["trade_win_rate"],
        "trade_expectancy": metrics["trade_expectancy"],
        "profit_factor": metrics["profit_factor"],
        "risk_halted": result.risk_halted,
        "risk_halt_reason": result.risk_halt_reason or "",
        "pending_orders_at_end": result.pending_order_count,
        "canceled_orders_at_end": result.canceled_order_count,
        "open_position_at_end": metrics["open_position_at_end"],
        "final_cash": result.final_portfolio["cash"],
        "negative_cash": Decimal(str(result.final_portfolio["cash"])) < 0,
    }


def _selection_key(row: Mapping[str, Any]) -> tuple[float, float, float, str]:
    return (
        -float(row["excess_return_vs_total_return_proxy"]),
        abs(float(row["maximum_drawdown"])),
        float(row["annualized_turnover"]),
        str(row["candidate_id"]),
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _median(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) not in (None, "")]
    return statistics.median(values) if values else 0.0


def _gate_summary(
    *,
    protocol: Mapping[str, Any],
    panel: Sequence[PanelAsset],
    test_rows: Sequence[Mapping[str, Any]],
    selections: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    gates = protocol["promotion_gates"]
    base_rows = [row for row in test_rows if row["scenario"] == "base"]
    cost_2x = [row for row in test_rows if row["scenario"] == "cost_2x"]
    cost_5x = [row for row in test_rows if row["scenario"] == "cost_5x"]
    minimum_folds = int(gates["minimum_folds_per_asset"])
    fold_counts = Counter(str(row["symbol"]) for row in base_rows)
    complete = all(fold_counts[asset.symbol] >= minimum_folds for asset in panel)
    positive_share = (
        sum(float(row["excess_return_vs_total_return_proxy"]) > 0 for row in base_rows)
        / len(base_rows)
        if base_rows
        else 0.0
    )
    minimum_drawdown = min(
        (float(row["maximum_drawdown"]) for row in base_rows),
        default=0.0,
    )
    risk_halts = sum(bool(row["risk_halted"]) for row in test_rows)
    pending = sum(int(row["pending_orders_at_end"]) for row in test_rows)
    negative_cash = any(bool(row["negative_cash"]) for row in test_rows)
    checks = {
        "all_assets_complete": complete,
        "minimum_folds_per_asset": complete,
        "median_base_test_excess_return_minimum": _median(
            base_rows, "excess_return_vs_total_return_proxy"
        )
        >= float(gates["median_base_test_excess_return_minimum"]),
        "positive_base_test_excess_share_minimum": positive_share
        >= float(gates["positive_base_test_excess_share_minimum"]),
        "median_cost_2x_test_excess_return_minimum": _median(
            cost_2x, "excess_return_vs_total_return_proxy"
        )
        >= float(gates["median_cost_2x_test_excess_return_minimum"]),
        "median_cost_5x_test_excess_return_minimum": _median(
            cost_5x, "excess_return_vs_total_return_proxy"
        )
        >= float(gates["median_cost_5x_test_excess_return_minimum"]),
        "maximum_test_drawdown_floor": minimum_drawdown
        >= float(gates["maximum_test_drawdown_floor"]),
        "risk_halts_allowed": risk_halts <= int(gates["risk_halts_allowed"]),
        "pending_orders_allowed": pending <= int(gates["pending_orders_allowed"]),
        "negative_cash_allowed": not negative_cash,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "asset_fold_counts": dict(sorted(fold_counts.items())),
            "selection_count": len(selections),
            "base_test_count": len(base_rows),
            "median_base_test_excess_return": _median(
                base_rows, "excess_return_vs_total_return_proxy"
            ),
            "positive_base_test_excess_share": positive_share,
            "median_cost_2x_test_excess_return": _median(
                cost_2x, "excess_return_vs_total_return_proxy"
            ),
            "median_cost_5x_test_excess_return": _median(
                cost_5x, "excess_return_vs_total_return_proxy"
            ),
            "minimum_test_drawdown": minimum_drawdown,
            "risk_halts": risk_halts,
            "pending_orders": pending,
            "negative_cash": negative_cash,
        },
    }


def _asset_summary(
    panel: Sequence[PanelAsset],
    test_rows: Sequence[Mapping[str, Any]],
    selections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for asset in panel:
        rows = [
            row for row in test_rows if row["symbol"] == asset.symbol and row["scenario"] == "base"
        ]
        selected = [row for row in selections if row["symbol"] == asset.symbol]
        frequencies = Counter(str(row["candidate_id"]) for row in selected)
        positive_share = (
            sum(float(row["excess_return_vs_total_return_proxy"]) > 0 for row in rows) / len(rows)
            if rows
            else 0.0
        )
        summaries.append(
            {
                "symbol": asset.symbol,
                "role": asset.role,
                "observations": len(asset.events),
                "csv_sha256": asset.csv_sha256,
                "folds": len(rows),
                "median_base_test_excess_return": _median(
                    rows, "excess_return_vs_total_return_proxy"
                ),
                "positive_base_test_excess_share": positive_share,
                "median_total_return": _median(rows, "total_return"),
                "median_maximum_drawdown": _median(rows, "maximum_drawdown"),
                "most_selected_candidate_id": (
                    sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))[0][0]
                    if frequencies
                    else ""
                ),
            }
        )
    return summaries


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    lines = [
        "# Preregistered Walk-Forward Evaluation",
        "",
        (
            "> This report is historical research evidence, not a forecast, "
            "live-performance record, or capital-deployment authorization."
        ),
        "",
        "## Run Identity",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Protocol | `{summary['protocol_id']}` |",
        f"| Protocol SHA-256 | `{summary['protocol_sha256']}` |",
        f"| Candidate trials | {summary['trial_counts']['validation']} |",
        f"| Out-of-sample cost trials | {summary['trial_counts']['test']} |",
        f"| Robustness trials | {summary['trial_counts']['robustness']} |",
        (
            "| Pre-holdout promotion gate | **"
            f"{'PASS' if summary['promotion_gate']['passed'] else 'FAIL'}** |"
        ),
        "",
        "## Asset Results",
        "",
        (
            "| Asset | Folds | Median base excess | Positive fold share | "
            "Median return | Median drawdown |"
        ),
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in summary["assets"]:
        lines.append(
            f"| {item['symbol']} | {item['folds']} | "
            f"{item['median_base_test_excess_return']:.2%} | "
            f"{item['positive_base_test_excess_share']:.2%} | "
            f"{item['median_total_return']:.2%} | "
            f"{item['median_maximum_drawdown']:.2%} |"
        )
    lines.extend(
        [
            "",
            "## Gate Checks",
            "",
            "| Check | Result |",
            "|---|---|",
        ]
    )
    for name, passed in summary["promotion_gate"]["checks"].items():
        lines.append(f"| `{name}` | {'PASS' if passed else 'FAIL'} |")
    lines.extend(
        [
            "",
            "## Evidence Boundary",
            "",
            (
                "The final 252-observation holdout remains unopened. This pre-holdout result "
                "cannot be promoted as final A+ research acceptance until the one-time lockbox "
                "command records its receipt and separate gates. A failed gate remains part of "
                "the public evidence record and must not be repaired by changing this protocol "
                "after seeing the result."
            ),
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_walk_forward(
    *,
    protocol_path: Path,
    data_dir: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    protocol_path = protocol_path.expanduser().resolve()
    data_dir = data_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    protocol = load_protocol(protocol_path)
    panel = load_panel(protocol=protocol, protocol_path=protocol_path, data_dir=data_dir)
    candidates = enumerate_candidates(protocol)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "protocol_snapshot": output_dir / "protocol_snapshot.json",
        "trial_ledger": output_dir / "trial_ledger.csv",
        "fold_selections": output_dir / "fold_selections.csv",
        "test_results": output_dir / "test_results.csv",
        "robustness_results": output_dir / "robustness_results.csv",
        "summary": output_dir / "summary.json",
        "report": output_dir / "report.md",
    }
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise ValueError("evaluation artifacts already exist; pass overwrite explicitly")
    for path in existing:
        path.unlink()

    windows = protocol["windows"]
    train_bars = int(windows["train_bars"])
    validation_bars = int(windows["validation_bars"])
    test_bars = int(windows["test_bars"])
    step_bars = int(windows["step_bars"])
    holdout_bars = int(windows["locked_holdout_bars"])
    base_buffer = _decimal(protocol["base_configuration"], "execution_price_buffer_bps")
    cost_scenarios = tuple(
        (str(item["name"]), Decimal(str(item["multiplier"]))) for item in protocol["cost_scenarios"]
    )

    trial_rows: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []
    robustness_rows: list[dict[str, Any]] = []
    for asset in panel:
        pre_holdout_end = len(asset.events) - holdout_bars
        fold_number = 0
        fold_start = 0
        while fold_start + train_bars + validation_bars + test_bars <= pre_holdout_end:
            validation_start = fold_start + train_bars
            validation_end = validation_start + validation_bars
            test_start = validation_end
            test_end = test_start + test_bars
            fold_id = stable_id(
                "walk-forward-fold",
                protocol["protocol_id"],
                asset.symbol,
                fold_number,
                fold_start,
                test_end,
            )
            fold_trials = [
                _trial_row(
                    protocol=protocol,
                    asset=asset,
                    candidate=candidate,
                    fold_id=fold_id,
                    stage="validation",
                    scenario="base",
                    context_start=fold_start,
                    evaluation_start=validation_start,
                    evaluation_end=validation_end,
                    cost_multiplier=Decimal("1"),
                    execution_buffer_bps=base_buffer,
                )
                for candidate in candidates
            ]
            trial_rows.extend(fold_trials)
            eligible = [row for row in fold_trials if row["eligible_for_selection"]]
            if not eligible:
                selections.append(
                    {
                        "symbol": asset.symbol,
                        "fold_id": fold_id,
                        "fold_number": fold_number,
                        "status": "no_eligible_candidate",
                        "candidate_id": "",
                        "validation_trial_id": "",
                        "validation_start": asset.events[validation_start].timestamp.isoformat(),
                        "validation_end": asset.events[validation_end - 1].timestamp.isoformat(),
                        "test_start": asset.events[test_start].timestamp.isoformat(),
                        "test_end": asset.events[test_end - 1].timestamp.isoformat(),
                    }
                )
                fold_start += step_bars
                fold_number += 1
                continue
            selected = min(eligible, key=_selection_key)
            candidate = next(
                item for item in candidates if item.candidate_id == selected["candidate_id"]
            )
            selections.append(
                {
                    "symbol": asset.symbol,
                    "fold_id": fold_id,
                    "fold_number": fold_number,
                    "status": "selected",
                    "candidate_id": candidate.candidate_id,
                    "fast_window": candidate.fast_window,
                    "slow_window": candidate.slow_window,
                    "invested_fraction": str(candidate.invested_fraction),
                    "validation_trial_id": selected["trial_id"],
                    "validation_excess_return": selected["excess_return_vs_total_return_proxy"],
                    "validation_start": asset.events[validation_start].timestamp.isoformat(),
                    "validation_end": asset.events[validation_end - 1].timestamp.isoformat(),
                    "test_start": asset.events[test_start].timestamp.isoformat(),
                    "test_end": asset.events[test_end - 1].timestamp.isoformat(),
                }
            )
            for scenario_name, multiplier in cost_scenarios:
                row = _trial_row(
                    protocol=protocol,
                    asset=asset,
                    candidate=candidate,
                    fold_id=fold_id,
                    stage="test",
                    scenario=scenario_name,
                    context_start=fold_start,
                    evaluation_start=test_start,
                    evaluation_end=test_end,
                    cost_multiplier=multiplier,
                    execution_buffer_bps=base_buffer,
                )
                row["selected_on_validation_trial_id"] = selected["trial_id"]
                test_rows.append(row)

            for buffer_value in protocol["robustness"]["execution_price_buffer_bps"]:
                buffer_decimal = Decimal(str(buffer_value))
                if buffer_decimal == base_buffer:
                    continue
                row = _trial_row(
                    protocol=protocol,
                    asset=asset,
                    candidate=candidate,
                    fold_id=fold_id,
                    stage="robustness",
                    scenario=f"execution_buffer_{buffer_value}",
                    context_start=fold_start,
                    evaluation_start=test_start,
                    evaluation_end=test_end,
                    cost_multiplier=Decimal("1"),
                    execution_buffer_bps=buffer_decimal,
                )
                row["selected_on_validation_trial_id"] = selected["trial_id"]
                robustness_rows.append(row)

            for offset_value in protocol["robustness"]["start_offset_bars"]:
                offset = int(offset_value)
                if offset == 0 or test_end + offset > pre_holdout_end:
                    continue
                row = _trial_row(
                    protocol=protocol,
                    asset=asset,
                    candidate=candidate,
                    fold_id=fold_id,
                    stage="robustness",
                    scenario=f"start_offset_{offset}",
                    context_start=fold_start + offset,
                    evaluation_start=test_start + offset,
                    evaluation_end=test_end + offset,
                    cost_multiplier=Decimal("1"),
                    execution_buffer_bps=base_buffer,
                )
                row["selected_on_validation_trial_id"] = selected["trial_id"]
                robustness_rows.append(row)

            fold_start += step_bars
            fold_number += 1

    promotion = _gate_summary(
        protocol=protocol,
        panel=panel,
        test_rows=test_rows,
        selections=[row for row in selections if row["status"] == "selected"],
    )
    summary = {
        "protocol_id": protocol["protocol_id"],
        "protocol_version": protocol["protocol_version"],
        "protocol_sha256": sha256_file(protocol_path),
        "holdout_status": "locked",
        "trial_counts": {
            "validation": len(trial_rows),
            "test": len(test_rows),
            "robustness": len(robustness_rows),
            "total": len(trial_rows) + len(test_rows) + len(robustness_rows),
        },
        "assets": _asset_summary(panel, test_rows, selections),
        "promotion_gate": promotion,
        "artifacts": {name: path.name for name, path in outputs.items()},
    }
    outputs["protocol_snapshot"].write_text(
        json.dumps(protocol, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_csv(outputs["trial_ledger"], trial_rows)
    _write_csv(outputs["fold_selections"], selections)
    _write_csv(outputs["test_results"], test_rows)
    _write_csv(outputs["robustness_results"], robustness_rows)
    outputs["summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_report(outputs["report"], summary)
    return summary


def _aggregate_holdout_candidates(
    trial_rows: Sequence[Mapping[str, str]],
    symbol: str,
) -> list[tuple[str, float, float, float]]:
    grouped: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in trial_rows:
        if row["symbol"] == symbol and row["stage"] == "validation":
            grouped[row["candidate_id"]].append(row)
    ranked: list[tuple[str, float, float, float]] = []
    for candidate_id, rows in grouped.items():
        eligible = [row for row in rows if row["eligible_for_selection"] == "True"]
        if not eligible:
            continue
        ranked.append(
            (
                candidate_id,
                _median(eligible, "excess_return_vs_total_return_proxy"),
                abs(_median(eligible, "maximum_drawdown")),
                _median(eligible, "annualized_turnover"),
            )
        )
    return sorted(ranked, key=lambda item: (-item[1], item[2], item[3], item[0]))


def _repository_commit(protocol_path: Path) -> str:
    git_dir = protocol_path.parent.parent / ".git"
    head_path = git_dir / "HEAD"
    if not head_path.is_file():
        return "unavailable"
    head = head_path.read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head
    ref_path = git_dir / head.removeprefix("ref: ")
    return ref_path.read_text(encoding="utf-8").strip() if ref_path.is_file() else "unavailable"


def run_locked_holdout(
    *,
    protocol_path: Path,
    data_dir: Path,
    evaluation_dir: Path,
    confirmation: str,
) -> dict[str, Any]:
    protocol_path = protocol_path.expanduser().resolve()
    data_dir = data_dir.expanduser().resolve()
    evaluation_dir = evaluation_dir.expanduser().resolve()
    protocol = load_protocol(protocol_path)
    if confirmation != protocol["protocol_id"]:
        raise ValueError("holdout confirmation must exactly match the protocol ID")
    receipt_path = evaluation_dir / protocol["locked_holdout"]["receipt_filename"]
    if receipt_path.exists():
        raise ValueError("locked holdout has already been opened for this evaluation directory")
    required = [
        evaluation_dir / "summary.json",
        evaluation_dir / "trial_ledger.csv",
        evaluation_dir / "protocol_snapshot.json",
    ]
    if any(not path.is_file() for path in required):
        raise ValueError("complete pre-holdout artifacts are required before opening the lockbox")
    pre_summary = json.loads(required[0].read_text(encoding="utf-8"))
    protocol_digest = sha256_file(protocol_path)
    if pre_summary.get("protocol_sha256") != protocol_digest:
        raise ValueError("pre-holdout summary protocol digest does not match")
    receipt: dict[str, Any] = {
        "status": "opened",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": protocol_digest,
        "opened_at": datetime.now(UTC).isoformat(),
        "repository_commit": _repository_commit(protocol_path),
        "preholdout_summary_sha256": sha256_file(required[0]),
        "trial_ledger_sha256": sha256_file(required[1]),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    panel = load_panel(protocol=protocol, protocol_path=protocol_path, data_dir=data_dir)
    candidates = {item.candidate_id: item for item in enumerate_candidates(protocol)}
    trial_rows = _read_csv(required[1])
    holdout_bars = int(protocol["windows"]["locked_holdout_bars"])
    context_bars = int(protocol["windows"]["train_bars"]) + int(
        protocol["windows"]["validation_bars"]
    )
    base_buffer = _decimal(protocol["base_configuration"], "execution_price_buffer_bps")
    cost_scenarios = tuple(
        (str(item["name"]), Decimal(str(item["multiplier"]))) for item in protocol["cost_scenarios"]
    )
    rows: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    for asset in panel:
        ranked = _aggregate_holdout_candidates(trial_rows, asset.symbol)
        if not ranked:
            selections.append({"symbol": asset.symbol, "status": "no_eligible_aggregate_candidate"})
            continue
        candidate_id = ranked[0][0]
        candidate = candidates[candidate_id]
        evaluation_start = len(asset.events) - holdout_bars
        evaluation_end = len(asset.events)
        context_start = max(0, evaluation_start - context_bars)
        selections.append(
            {
                "symbol": asset.symbol,
                "status": "selected",
                "candidate_id": candidate_id,
                "fast_window": candidate.fast_window,
                "slow_window": candidate.slow_window,
                "invested_fraction": str(candidate.invested_fraction),
                "aggregate_validation_median_excess_return": ranked[0][1],
            }
        )
        for scenario_name, multiplier in cost_scenarios:
            row = _trial_row(
                protocol=protocol,
                asset=asset,
                candidate=candidate,
                fold_id="locked-holdout",
                stage="locked_holdout",
                scenario=scenario_name,
                context_start=context_start,
                evaluation_start=evaluation_start,
                evaluation_end=evaluation_end,
                cost_multiplier=multiplier,
                execution_buffer_bps=base_buffer,
            )
            rows.append(row)

    holdout_results_path = evaluation_dir / "holdout_results.csv"
    holdout_selections_path = evaluation_dir / "holdout_selections.csv"
    _write_csv(holdout_results_path, rows)
    _write_csv(holdout_selections_path, selections)
    base_rows = [row for row in rows if row["scenario"] == "base"]
    gates = protocol["promotion_gates"]
    positive_share = (
        sum(float(row["excess_return_vs_total_return_proxy"]) > 0 for row in base_rows)
        / len(base_rows)
        if base_rows
        else 0.0
    )
    checks = {
        "all_assets_complete": len(base_rows) == len(panel),
        "locked_holdout_median_excess_return_minimum": _median(
            base_rows, "excess_return_vs_total_return_proxy"
        )
        >= float(gates["locked_holdout_median_excess_return_minimum"]),
        "locked_holdout_positive_asset_share_minimum": positive_share
        >= float(gates["locked_holdout_positive_asset_share_minimum"]),
        "risk_halts_allowed": sum(bool(row["risk_halted"]) for row in rows)
        <= int(gates["risk_halts_allowed"]),
        "pending_orders_allowed": sum(int(row["pending_orders_at_end"]) for row in rows)
        <= int(gates["pending_orders_allowed"]),
        "negative_cash_allowed": not any(bool(row["negative_cash"]) for row in rows),
    }
    summary = {
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": protocol_digest,
        "preholdout_gate_passed": bool(pre_summary["promotion_gate"]["passed"]),
        "holdout_gate": {
            "passed": all(checks.values()),
            "checks": checks,
            "observed": {
                "asset_count": len(base_rows),
                "median_base_excess_return": _median(
                    base_rows, "excess_return_vs_total_return_proxy"
                ),
                "positive_base_asset_share": positive_share,
            },
        },
        "overall_research_acceptance": bool(pre_summary["promotion_gate"]["passed"])
        and all(checks.values()),
    }
    summary_path = evaluation_dir / "holdout_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt.update(
        {
            "status": "completed",
            "completed_at": datetime.now(UTC).isoformat(),
            "holdout_results_sha256": sha256_file(holdout_results_path),
            "holdout_selections_sha256": sha256_file(holdout_selections_path),
            "holdout_summary_sha256": sha256_file(summary_path),
            "overall_research_acceptance": summary["overall_research_acceptance"],
        }
    )
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
