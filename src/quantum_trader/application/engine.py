"""Deterministic simulation orchestration with complete event recording."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import chain
from typing import Any

from quantum_trader.config import SimulationConfig
from quantum_trader.domain.clock import ReplayClock
from quantum_trader.domain.models import EquityPoint, Fill, RiskDecision, Signal, stable_id
from quantum_trader.domain.portfolio import Portfolio
from quantum_trader.domain.risk import RiskManager
from quantum_trader.domain.strategy import Strategy
from quantum_trader.ports.broker import Broker
from quantum_trader.ports.event_store import EventStore
from quantum_trader.ports.market_data import MarketDataProvider


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """In-memory result returned after a finite deterministic replay."""

    run_id: str
    source_name: str
    config: dict[str, Any]
    event_count: int
    signal_count: int
    intent_count: int
    allowed_intent_count: int
    denied_intent_count: int
    fill_count: int
    rejected_fill_count: int
    pending_order_count: int
    canceled_order_count: int
    equity_curve: tuple[EquityPoint, ...]
    fills: tuple[Fill, ...]
    market_prices: tuple[tuple[datetime, Decimal], ...]
    benchmark_prices: tuple[tuple[datetime, Decimal], ...]
    final_portfolio: dict[str, object]
    risk_halted: bool
    risk_halt_reason: str | None


class SimulationEngine:
    """Coordinate pure strategy, risk, portfolio, broker, data, and storage ports."""

    def __init__(
        self,
        *,
        config: SimulationConfig,
        market_data: MarketDataProvider,
        strategy: Strategy,
        risk_manager: RiskManager,
        portfolio: Portfolio,
        broker: Broker,
        event_store: EventStore,
        clock: ReplayClock | None = None,
        evaluation_start: datetime | None = None,
    ) -> None:
        self.config = config
        self.market_data = market_data
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.portfolio = portfolio
        self.broker = broker
        self.event_store = event_store
        self.clock = clock or ReplayClock()
        if evaluation_start is not None and evaluation_start.tzinfo is None:
            raise ValueError("evaluation_start must be timezone-aware")
        self.evaluation_start = evaluation_start

    def run(self) -> SimulationResult:
        config_payload = self.config.as_dict()
        run_id = stable_id(
            "run",
            self.market_data.source_name,
            json.dumps(config_payload, sort_keys=True, separators=(",", ":")),
            self.evaluation_start.isoformat() if self.evaluation_start is not None else "all",
        )
        iterator = iter(self.market_data.stream())
        try:
            first_event = next(iterator)
        except StopIteration as exc:
            raise ValueError("market-data provider yielded no events") from exc

        self.event_store.append(
            event_type="run_started",
            timestamp=first_event.timestamp,
            correlation_id=run_id,
            payload={
                "run_id": run_id,
                "source": self.market_data.source_name,
                "strategy": self.strategy.name,
                "config": config_payload,
                "evaluation_start": (
                    self.evaluation_start.isoformat() if self.evaluation_start is not None else None
                ),
            },
        )

        event_count = 0
        signal_count = 0
        intent_count = 0
        allowed_count = 0
        denied_count = 0
        rejected_fill_count = 0
        equity_curve: list[EquityPoint] = []
        fills: list[Fill] = []
        market_prices: list[tuple[datetime, Decimal]] = []
        benchmark_prices: list[tuple[datetime, Decimal]] = []
        adjusted_benchmark_available: bool | None = None
        last_timestamp = first_event.timestamp

        for event in chain((first_event,), iterator):
            self.clock.advance(event.timestamp)
            last_timestamp = event.timestamp
            in_evaluation = (
                self.evaluation_start is None or event.timestamp >= self.evaluation_start
            )
            if in_evaluation:
                event_count += 1
                market_prices.append((event.timestamp, event.close))
            event_has_adjusted_close = event.adjusted_close is not None
            if adjusted_benchmark_available is None:
                adjusted_benchmark_available = event_has_adjusted_close
            elif adjusted_benchmark_available is not event_has_adjusted_close:
                raise ValueError("adjusted-close benchmark values must be supplied for every event")
            if event.adjusted_close is not None and in_evaluation:
                benchmark_prices.append((event.timestamp, event.adjusted_close))
            self.event_store.append(
                event_type="market_event",
                timestamp=event.timestamp,
                correlation_id=event.correlation_id,
                payload=event.as_dict(),
            )

            for fill in self.broker.on_market_event(event):
                try:
                    self.portfolio.apply_fill(fill)
                except Exception as exc:  # fail closed at the accounting boundary
                    rejected_fill_count += 1
                    self.risk_manager.halted = True
                    self.risk_manager.halt_reason = "portfolio_rejected_fill"
                    self.event_store.append(
                        event_type="fill_rejected",
                        timestamp=fill.timestamp,
                        correlation_id=fill.correlation_id,
                        payload={
                            "fill": fill.as_dict(),
                            "reason": type(exc).__name__,
                        },
                    )
                else:
                    prior_halt_reason = self.risk_manager.halt_reason
                    self.risk_manager.observe_fill(fill, self.portfolio)
                    fills.append(fill)
                    self.event_store.append(
                        event_type="fill",
                        timestamp=fill.timestamp,
                        correlation_id=fill.correlation_id,
                        payload=fill.as_dict(),
                    )
                    if self.risk_manager.halt_reason != prior_halt_reason:
                        self.event_store.append(
                            event_type="risk_halt",
                            timestamp=fill.timestamp,
                            correlation_id=fill.correlation_id,
                            payload={"reason": self.risk_manager.halt_reason},
                        )

            snapshot = self.portfolio.equity_point(event)
            if in_evaluation:
                equity_curve.append(snapshot)
            self.event_store.append(
                event_type="equity",
                timestamp=event.timestamp,
                correlation_id=event.correlation_id,
                payload=snapshot.as_dict(),
            )

            prior_halt_reason = self.risk_manager.halt_reason
            try:
                self.risk_manager.observe(self.portfolio, snapshot)
            except Exception as exc:
                self.risk_manager.halted = True
                self.risk_manager.halt_reason = "risk_observation_error"
                self.event_store.append(
                    event_type="risk_observation_error",
                    timestamp=event.timestamp,
                    correlation_id=event.correlation_id,
                    payload={"reason": type(exc).__name__},
                )
            if self.risk_manager.halt_reason != prior_halt_reason:
                self.event_store.append(
                    event_type="risk_halt",
                    timestamp=event.timestamp,
                    correlation_id=event.correlation_id,
                    payload={"reason": self.risk_manager.halt_reason},
                )

            signal = self.strategy.on_market_event(event)
            if in_evaluation:
                signal_count += 1
            self.event_store.append(
                event_type="signal",
                timestamp=signal.timestamp,
                correlation_id=signal.correlation_id,
                payload=signal.as_dict(),
            )

            if not in_evaluation:
                continue

            effective_signal = signal
            if (
                self.risk_manager.halted
                and self.portfolio.position_quantity(event.symbol) > 0
                and signal.target_fraction != 0
            ):
                effective_signal = Signal(
                    timestamp=event.timestamp,
                    symbol=event.symbol,
                    target_fraction=Decimal("0"),
                    rationale=f"risk_override:{self.risk_manager.halt_reason or 'emergency_halt'}",
                    correlation_id=event.correlation_id,
                )
                self.event_store.append(
                    event_type="risk_override_signal",
                    timestamp=effective_signal.timestamp,
                    correlation_id=effective_signal.correlation_id,
                    payload=effective_signal.as_dict(),
                )

            intent = self.portfolio.order_for_target(effective_signal, event)
            if intent is None:
                continue
            intent_count += 1
            self.event_store.append(
                event_type="order_intent",
                timestamp=intent.timestamp,
                correlation_id=intent.correlation_id,
                payload=intent.as_dict(),
            )

            try:
                decision = self.risk_manager.evaluate(intent, self.portfolio, snapshot)
            except Exception as exc:  # never convert a risk error into an approval
                self.risk_manager.halted = True
                self.risk_manager.halt_reason = "risk_evaluation_error"
                decision = RiskDecision(
                    allowed=False,
                    reason=f"risk_evaluation_error:{type(exc).__name__}",
                    approved_quantity=0,
                    intent_id=intent.intent_id,
                    correlation_id=intent.correlation_id,
                )
            self.event_store.append(
                event_type="risk_decision",
                timestamp=intent.timestamp,
                correlation_id=intent.correlation_id,
                payload=decision.as_dict(),
            )

            if not decision.allowed:
                denied_count += 1
                continue
            allowed_count += 1
            order_id = self.broker.submit(intent, decision)
            self.event_store.append(
                event_type="order_accepted",
                timestamp=intent.timestamp,
                correlation_id=intent.correlation_id,
                payload={
                    "order_id": order_id,
                    "intent_id": intent.intent_id,
                    "approved_quantity": decision.approved_quantity,
                },
            )

        if not equity_curve:
            raise ValueError("evaluation boundary excluded every market event")

        canceled_order_ids = tuple(self.broker.cancel_all())
        for order_id in canceled_order_ids:
            self.event_store.append(
                event_type="order_canceled_end_of_test",
                timestamp=last_timestamp,
                correlation_id=run_id,
                payload={
                    "order_id": order_id,
                    "policy": "cancel_pending_mark_positions_to_final_close",
                },
            )

        self.event_store.append(
            event_type="run_completed",
            timestamp=last_timestamp,
            correlation_id=run_id,
            payload={
                "run_id": run_id,
                "events": event_count,
                "signals": signal_count,
                "intents": intent_count,
                "allowed_intents": allowed_count,
                "denied_intents": denied_count,
                "fills": len(fills),
                "rejected_fills": rejected_fill_count,
                "pending_orders": self.broker.pending_order_count,
                "canceled_orders": len(canceled_order_ids),
                "end_of_test_policy": "cancel_pending_mark_positions_to_final_close",
                "risk_halted": self.risk_manager.halted,
                "risk_halt_reason": self.risk_manager.halt_reason,
                "final_portfolio": self.portfolio.as_dict(),
            },
        )

        return SimulationResult(
            run_id=run_id,
            source_name=self.market_data.source_name,
            config=config_payload,
            event_count=event_count,
            signal_count=signal_count,
            intent_count=intent_count,
            allowed_intent_count=allowed_count,
            denied_intent_count=denied_count,
            fill_count=len(fills),
            rejected_fill_count=rejected_fill_count,
            pending_order_count=self.broker.pending_order_count,
            canceled_order_count=len(canceled_order_ids),
            equity_curve=tuple(equity_curve),
            fills=tuple(fills),
            market_prices=tuple(market_prices),
            benchmark_prices=tuple(benchmark_prices),
            final_portfolio=self.portfolio.as_dict(),
            risk_halted=self.risk_manager.halted,
            risk_halt_reason=self.risk_manager.halt_reason,
        )
