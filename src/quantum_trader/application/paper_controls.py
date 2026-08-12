"""Application service that assembles a complete paper pre-trade assessment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quantum_trader.application.reconciliation import ReconciliationReport
from quantum_trader.domain.brokerage import ApprovedBrokerOrder
from quantum_trader.domain.execution import ExecutionMode
from quantum_trader.domain.market_controls import (
    PaperControlDecision,
    PaperControlLimits,
    PaperPreTradeState,
    evaluate_paper_pretrade,
)
from quantum_trader.ports.broker_journal import BrokerJournal
from quantum_trader.ports.control_data import PaperControlData
from quantum_trader.ports.external_broker import ExternalBroker

ZERO = Decimal("0")


class PaperControlAssessmentError(RuntimeError):
    """The control service could not form a trustworthy complete assessment."""


class ReconciliationService(Protocol):
    def reconcile(self, *, timestamp: datetime) -> ReconciliationReport:
        """Return one complete broker reconciliation report."""


@dataclass(frozen=True, slots=True)
class PaperPreTradeAssessment:
    reconciliation: ReconciliationReport
    decision: PaperControlDecision

    @property
    def ready(self) -> bool:
        return self.reconciliation.ready and self.decision.allowed


class PaperPreTradeController:
    """Perform every read and pure control needed immediately before submission."""

    def __init__(
        self,
        *,
        broker: ExternalBroker,
        journal: BrokerJournal,
        control_data: PaperControlData,
        reconciler: ReconciliationService,
        strategy_namespace: str,
        limits: PaperControlLimits,
    ) -> None:
        namespace = strategy_namespace.strip()
        if not namespace:
            raise ValueError("strategy_namespace must not be empty")
        if broker.environment is not ExecutionMode.PAPER:
            raise ValueError("paper pre-trade controls require a paper broker")
        try:
            eastern = ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError as exc:
            raise PaperControlAssessmentError("America/New_York timezone data is required") from exc
        self._broker = broker
        self._journal = journal
        self._control_data = control_data
        self._reconciler = reconciler
        self._strategy_namespace = namespace
        self._limits = limits
        self._eastern = eastern

    def assess(
        self,
        *,
        order: ApprovedBrokerOrder,
        timestamp: datetime,
    ) -> PaperPreTradeAssessment:
        _aware(timestamp)
        reconciliation = self._reconciler.reconcile(timestamp=timestamp)
        if not reconciliation.ready:
            return PaperPreTradeAssessment(
                reconciliation=reconciliation,
                decision=_denied("reconciliation_not_ready"),
            )
        if reconciliation.account_sha256 != self._broker.account_sha256:
            raise PaperControlAssessmentError("reconciled account fingerprint changed")

        account = self._broker.get_account()
        if account.account_sha256 != reconciliation.account_sha256:
            raise PaperControlAssessmentError("broker account changed after reconciliation")
        clock = self._broker.get_clock()
        positions = tuple(self._broker.list_positions())
        open_orders = tuple(self._broker.list_open_orders())
        trade_date = timestamp.astimezone(self._eastern).date()
        calendar = self._control_data.get_calendar_day(trade_date)
        if calendar is None:
            return PaperPreTradeAssessment(
                reconciliation=reconciliation,
                decision=_denied("market_calendar_unavailable"),
            )
        asset = self._control_data.get_asset(order.symbol)
        quote = self._control_data.get_latest_quote(order.symbol)
        decision = evaluate_paper_pretrade(
            PaperPreTradeState(
                now=timestamp,
                strategy_namespace=self._strategy_namespace,
                account=account,
                clock=clock,
                calendar=calendar,
                asset=asset,
                quote=quote,
                order=order,
                positions=positions,
                open_orders=open_orders,
                submission_timestamps=tuple(self._journal.submission_timestamps()),
                reconciliation_ready=reconciliation.ready,
                reconciliation_timestamp=reconciliation.timestamp,
            ),
            self._limits,
        )
        return PaperPreTradeAssessment(
            reconciliation=reconciliation,
            decision=decision,
        )


def _denied(reason: str) -> PaperControlDecision:
    return PaperControlDecision(
        allowed=False,
        reasons=(reason,),
        candidate_notional=ZERO,
        committed_open_buy_notional=ZERO,
        projected_gross_exposure=ZERO,
        projected_symbol_exposure=ZERO,
        projected_cash=ZERO,
        recent_order_count=0,
        session_order_count=0,
    )


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("assessment timestamp must be timezone-aware")
