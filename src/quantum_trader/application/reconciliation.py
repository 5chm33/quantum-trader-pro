"""Fail-closed paper account, order, fill, activity, and position reconciliation."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from quantum_trader.domain.brokerage import (
    BrokerFillActivity,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
    is_owned_client_order_id,
)
from quantum_trader.domain.execution import ExecutionMode
from quantum_trader.domain.models import Side
from quantum_trader.ports.broker_journal import BrokerJournal
from quantum_trader.ports.external_broker import ExternalBroker


class ReconciliationError(RuntimeError):
    """Reconciliation could not produce a complete trustworthy snapshot."""


class IssueSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


@dataclass(frozen=True, slots=True)
class ReconciliationIssue:
    """Redacted mismatch that can disarm paper execution without leaking account data."""

    code: str
    severity: IssueSeverity
    subject_sha256: str
    expected: str | None = None
    observed: str | None = None

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("reconciliation issue code must not be empty")
        if len(self.subject_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.subject_sha256
        ):
            raise ValueError("subject_sha256 must be a lowercase SHA-256 digest")

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "subject_sha256": self.subject_sha256,
            "expected": self.expected,
            "observed": self.observed,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """One complete reconciliation result tied to normalized broker state."""

    timestamp: datetime
    ready: bool
    account_sha256: str
    account_permits_new_exposure: bool
    open_order_count: int
    position_count: int
    activity_count: int
    new_execution_count: int
    resolved_submission_count: int
    unresolved_submission_count: int
    activity_checkpoint_sha256: str | None
    expected_positions: Mapping[str, Decimal]
    broker_positions: Mapping[str, Decimal]
    issues: tuple[ReconciliationIssue, ...]

    def __post_init__(self) -> None:
        _aware(self.timestamp, "timestamp")
        counts = (
            self.open_order_count,
            self.position_count,
            self.activity_count,
            self.new_execution_count,
            self.resolved_submission_count,
            self.unresolved_submission_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("reconciliation counts must not be negative")
        if self.ready != (not self.issues):
            raise ValueError("ready must exactly reflect the absence of reconciliation issues")

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "ready": self.ready,
            "account_sha256": self.account_sha256,
            "account_permits_new_exposure": self.account_permits_new_exposure,
            "open_order_count": self.open_order_count,
            "position_count": self.position_count,
            "activity_count": self.activity_count,
            "new_execution_count": self.new_execution_count,
            "resolved_submission_count": self.resolved_submission_count,
            "unresolved_submission_count": self.unresolved_submission_count,
            "activity_checkpoint_sha256": self.activity_checkpoint_sha256,
            "expected_positions": {
                symbol: str(quantity)
                for symbol, quantity in sorted(self.expected_positions.items())
            },
            "broker_positions": {
                symbol: str(quantity) for symbol, quantity in sorted(self.broker_positions.items())
            },
            "issues": [issue.as_dict() for issue in self.issues],
        }


class PaperReconciler:
    """Rebuild local paper projections from broker-authoritative normalized reads."""

    def __init__(
        self,
        *,
        broker: ExternalBroker,
        journal: BrokerJournal,
        strategy_namespace: str,
        activity_after: datetime,
        page_size: int = 100,
        maximum_pages: int = 1000,
    ) -> None:
        namespace = strategy_namespace.strip()
        if not namespace:
            raise ValueError("strategy_namespace must not be empty")
        if broker.environment is not ExecutionMode.PAPER:
            raise ValueError("paper reconciliation requires a paper broker")
        _aware(activity_after, "activity_after")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be in [1, 100]")
        if maximum_pages <= 0:
            raise ValueError("maximum_pages must be positive")
        self._broker = broker
        self._journal = journal
        self._strategy_namespace = namespace
        self._activity_after = activity_after.astimezone(UTC)
        self._page_size = page_size
        self._maximum_pages = maximum_pages

    def reconcile(self, *, timestamp: datetime) -> ReconciliationReport:
        _aware(timestamp, "timestamp")
        if self._journal.integrity_check().lower() != "ok":
            raise ReconciliationError("broker journal integrity check failed")

        account = self._broker.get_account()
        if account.environment is not ExecutionMode.PAPER:
            raise ReconciliationError("broker returned a non-paper account snapshot")
        if account.account_sha256 != self._broker.account_sha256:
            raise ReconciliationError("broker account fingerprint changed")
        positions = tuple(self._broker.list_positions())
        open_orders = tuple(self._broker.list_open_orders())
        known_client_ids = self._journal.known_client_order_ids()
        unresolved = tuple(self._journal.unresolved_submissions())
        resolutions: dict[str, str] = {}
        order_cache: dict[str, BrokerOrderSnapshot] = {
            order.broker_order_id: order for order in open_orders
        }
        issues: list[ReconciliationIssue] = []

        for submission in unresolved:
            broker_order = self._broker.get_order_by_client_id(submission.client_order_id)
            if broker_order is None:
                issues.append(
                    _issue(
                        "unresolved_submission",
                        IssueSeverity.HIGH,
                        submission.client_order_id,
                        expected="broker order located by deterministic client ID",
                        observed="not found",
                    )
                )
                continue
            resolutions[submission.client_order_id] = broker_order.broker_order_id
            order_cache[broker_order.broker_order_id] = broker_order

        for order in open_orders:
            owned = is_owned_client_order_id(
                order.client_order_id,
                self._strategy_namespace,
            )
            if not owned:
                issues.append(
                    _issue(
                        "foreign_open_order",
                        IssueSeverity.CRITICAL,
                        order.broker_order_id,
                        expected="strategy-owned order only",
                        observed="foreign client namespace",
                    )
                )
            elif (
                order.client_order_id not in known_client_ids
                and order.client_order_id not in resolutions
            ):
                issues.append(
                    _issue(
                        "unexplained_owned_order",
                        IssueSeverity.CRITICAL,
                        order.broker_order_id,
                        expected="durable local submission",
                        observed="missing",
                    )
                )
            if order.status is BrokerOrderStatus.UNKNOWN:
                issues.append(
                    _issue(
                        "unknown_order_status",
                        IssueSeverity.HIGH,
                        order.broker_order_id,
                        expected="known normalized status",
                        observed="unknown",
                    )
                )

        activities, checkpoint = self._read_all_fill_pages()
        normalized_activities: list[BrokerFillActivity] = []
        for activity in activities:
            normalized = activity
            if activity.client_order_id is None:
                broker_order = order_cache.get(activity.broker_order_id)
                if broker_order is None:
                    broker_order = self._broker.get_order_by_id(activity.broker_order_id)
                    if broker_order is not None:
                        order_cache[broker_order.broker_order_id] = broker_order
                if broker_order is None:
                    issues.append(
                        _issue(
                            "activity_order_missing",
                            IssueSeverity.CRITICAL,
                            activity.execution_id,
                            expected="broker order for fill activity",
                            observed="not found",
                        )
                    )
                else:
                    normalized = replace(
                        activity,
                        client_order_id=broker_order.client_order_id,
                    )
            normalized_activities.append(normalized)

        previous_fills = tuple(self._journal.all_fills())
        previous_by_execution = {fill.execution_id: fill for fill in previous_fills}
        new_execution_count = sum(
            activity.execution_id not in previous_by_execution for activity in normalized_activities
        )
        merged_fills = dict(previous_by_execution)
        merged_fills.update({activity.execution_id: activity for activity in normalized_activities})
        expected_positions = _project_owned_positions(
            tuple(merged_fills.values()),
            self._strategy_namespace,
            known_client_ids | frozenset(resolutions),
            issues,
        )
        broker_positions = _position_map(positions, issues)
        _compare_positions(expected_positions, broker_positions, issues)

        if not account.permits_new_exposure:
            issues.append(
                _issue(
                    "account_not_permitted",
                    IssueSeverity.CRITICAL,
                    account.account_sha256,
                    expected="active, unblocked account with nonnegative cash and buying power",
                    observed="account gate failed",
                )
            )

        unresolved_count = len(unresolved) - len(resolutions)
        checkpoint_hash = _hash_identifier(checkpoint) if checkpoint is not None else None
        report = ReconciliationReport(
            timestamp=timestamp.astimezone(UTC),
            ready=not issues,
            account_sha256=account.account_sha256,
            account_permits_new_exposure=account.permits_new_exposure,
            open_order_count=len(open_orders),
            position_count=len(positions),
            activity_count=len(normalized_activities),
            new_execution_count=new_execution_count,
            resolved_submission_count=len(resolutions),
            unresolved_submission_count=unresolved_count,
            activity_checkpoint_sha256=checkpoint_hash,
            expected_positions=expected_positions,
            broker_positions=broker_positions,
            issues=tuple(issues),
        )
        self._journal.apply_reconciliation(
            account=account,
            orders=tuple(order_cache.values()),
            positions=positions,
            fills=tuple(normalized_activities),
            submission_resolutions=resolutions,
            activity_checkpoint=checkpoint,
            timestamp=timestamp.astimezone(UTC),
            report=report.as_dict(),
        )
        if self._journal.integrity_check().lower() != "ok":
            raise ReconciliationError("broker journal integrity failed after commit")
        return report

    def _read_all_fill_pages(self) -> tuple[tuple[BrokerFillActivity, ...], str | None]:
        page_token = self._journal.activity_checkpoint()
        observed_tokens: set[str] = set()
        activities: list[BrokerFillActivity] = []
        checkpoint = page_token
        for _ in range(self._maximum_pages):
            page = self._broker.list_fill_activities(
                after=self._activity_after,
                page_token=page_token,
                page_size=self._page_size,
            )
            activities.extend(page.activities)
            if page.activities:
                checkpoint = page.activities[-1].activity_id
            if page.next_page_token is None:
                return tuple(activities), checkpoint
            if page.next_page_token in observed_tokens or page.next_page_token == page_token:
                raise ReconciliationError("broker activity pagination did not advance")
            observed_tokens.add(page.next_page_token)
            page_token = page.next_page_token
        raise ReconciliationError("broker activity pagination exceeded the configured limit")


def _project_owned_positions(
    fills: Sequence[BrokerFillActivity],
    strategy_namespace: str,
    known_client_ids: frozenset[str],
    issues: list[ReconciliationIssue],
) -> dict[str, Decimal]:
    quantities: dict[str, Decimal] = {}
    for fill in sorted(fills, key=lambda value: (value.timestamp, value.execution_id)):
        client_order_id = fill.client_order_id
        if client_order_id is None:
            continue
        if not is_owned_client_order_id(client_order_id, strategy_namespace):
            continue
        if client_order_id not in known_client_ids:
            issues.append(
                _issue(
                    "unexplained_owned_fill",
                    IssueSeverity.CRITICAL,
                    fill.execution_id,
                    expected="durable local submission",
                    observed="missing",
                )
            )
        symbol = fill.symbol.upper()
        signed_quantity = fill.quantity if fill.side is Side.BUY else -fill.quantity
        quantities[symbol] = quantities.get(symbol, Decimal("0")) + signed_quantity
    return {symbol: quantity for symbol, quantity in quantities.items() if quantity != 0}


def _position_map(
    positions: Sequence[BrokerPositionSnapshot],
    issues: list[ReconciliationIssue],
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for position in positions:
        symbol = position.symbol.upper()
        if symbol in result:
            issues.append(
                _issue(
                    "duplicate_broker_position",
                    IssueSeverity.CRITICAL,
                    symbol,
                    expected="one position per symbol",
                    observed="duplicate",
                )
            )
        result[symbol] = result.get(symbol, Decimal("0")) + position.quantity
    return {symbol: quantity for symbol, quantity in result.items() if quantity != 0}


def _compare_positions(
    expected: Mapping[str, Decimal],
    observed: Mapping[str, Decimal],
    issues: list[ReconciliationIssue],
) -> None:
    for symbol in sorted(set(expected) | set(observed)):
        expected_quantity = expected.get(symbol, Decimal("0"))
        observed_quantity = observed.get(symbol, Decimal("0"))
        if expected_quantity != observed_quantity:
            issues.append(
                _issue(
                    "position_mismatch",
                    IssueSeverity.CRITICAL,
                    symbol,
                    expected=str(expected_quantity),
                    observed=str(observed_quantity),
                )
            )


def _issue(
    code: str,
    severity: IssueSeverity,
    subject: str,
    *,
    expected: str | None,
    observed: str | None,
) -> ReconciliationIssue:
    return ReconciliationIssue(
        code=code,
        severity=severity,
        subject_sha256=_hash_identifier(subject),
        expected=expected,
        observed=observed,
    )


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
