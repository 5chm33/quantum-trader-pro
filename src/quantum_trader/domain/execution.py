"""Execution profiles and fail-closed operator arming contracts."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from quantum_trader.domain.models import stable_id

PAPER_ACKNOWLEDGEMENT = "I AUTHORIZE PAPER ORDERS ONLY"
MAX_ARMING_TTL = timedelta(hours=24)


class ExecutionMode(StrEnum):
    """Known execution profiles; availability is enforced separately."""

    SIMULATION = "simulation"
    PAPER = "paper"
    LIVE = "live"


class GateState(StrEnum):
    """External execution gate states exposed to operators and evidence."""

    DISARMED = "disarmed"
    ARMED = "armed"
    EXPIRED = "expired"
    HALTED = "halted"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


def sha256_text(value: str) -> str:
    """Return a stable non-secret fingerprint for configuration metadata."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionFingerprint:
    """Bind authorization to exact code, configuration, and broker account."""

    code_sha256: str
    configuration_sha256: str
    account_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code_sha256", _require_sha256(self.code_sha256, "code_sha256"))
        object.__setattr__(
            self,
            "configuration_sha256",
            _require_sha256(self.configuration_sha256, "configuration_sha256"),
        )
        object.__setattr__(
            self,
            "account_sha256",
            _require_sha256(self.account_sha256, "account_sha256"),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "code_sha256": self.code_sha256,
            "configuration_sha256": self.configuration_sha256,
            "account_sha256": self.account_sha256,
        }


@dataclass(frozen=True, slots=True)
class ArmingRecord:
    """Expiring operator authorization bound to one paper account and build."""

    record_id: str
    environment: ExecutionMode
    strategy_namespace: str
    fingerprint: ExecutionFingerprint
    issued_at: datetime
    expires_at: datetime
    acknowledgement_sha256: str

    @classmethod
    def issue_paper(
        cls,
        *,
        strategy_namespace: str,
        fingerprint: ExecutionFingerprint,
        issued_at: datetime,
        ttl: timedelta,
        acknowledgement: str,
    ) -> ArmingRecord:
        """Create an expiring paper-only record after an exact acknowledgement."""

        _require_aware(issued_at, "issued_at")
        namespace = strategy_namespace.strip()
        if not namespace or not namespace.replace("-", "").replace("_", "").isalnum():
            raise ValueError("strategy_namespace must contain only letters, numbers, '-' or '_'")
        if acknowledgement != PAPER_ACKNOWLEDGEMENT:
            raise ValueError("paper acknowledgement did not match the required text")
        if ttl <= timedelta(0) or ttl > MAX_ARMING_TTL:
            raise ValueError("paper arming TTL must be positive and no longer than 24 hours")
        expires_at = issued_at + ttl
        acknowledgement_sha256 = sha256_text(acknowledgement)
        return cls(
            record_id=stable_id(
                "paper-arming",
                namespace,
                fingerprint.code_sha256,
                fingerprint.configuration_sha256,
                fingerprint.account_sha256,
                issued_at.isoformat(),
                expires_at.isoformat(),
            ),
            environment=ExecutionMode.PAPER,
            strategy_namespace=namespace,
            fingerprint=fingerprint,
            issued_at=issued_at,
            expires_at=expires_at,
            acknowledgement_sha256=acknowledgement_sha256,
        )

    def __post_init__(self) -> None:
        if self.environment is not ExecutionMode.PAPER:
            raise ValueError("this build can issue arming records only for paper execution")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("arming record expiry must be after issuance")
        _require_sha256(self.acknowledgement_sha256, "acknowledgement_sha256")
        if not self.record_id:
            raise ValueError("record_id must not be empty")

    def state_at(self, now: datetime) -> GateState:
        _require_aware(now, "now")
        if now < self.issued_at:
            return GateState.DISARMED
        if now >= self.expires_at:
            return GateState.EXPIRED
        return GateState.ARMED

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "environment": self.environment.value,
            "strategy_namespace": self.strategy_namespace,
            "fingerprint": self.fingerprint.as_dict(),
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "acknowledgement_sha256": self.acknowledgement_sha256,
        }


@dataclass(frozen=True, slots=True)
class BrokerPreflight:
    """External-state checks that must all pass immediately before arming."""

    environment_verified: bool
    account_verified: bool
    account_active: bool
    account_unblocked: bool
    reconciliation_complete: bool
    broker_clock_verified: bool
    market_data_fresh: bool
    durable_journal_ready: bool
    secret_source_secure: bool
    unresolved_submissions: int = 0
    unexplained_orders: int = 0
    unexplained_positions: int = 0

    def __post_init__(self) -> None:
        for name in (
            "unresolved_submissions",
            "unexplained_orders",
            "unexplained_positions",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")

    def failures(self) -> tuple[str, ...]:
        checks = {
            "environment_not_verified": self.environment_verified,
            "account_not_verified": self.account_verified,
            "account_not_active": self.account_active,
            "account_blocked": self.account_unblocked,
            "reconciliation_incomplete": self.reconciliation_complete,
            "broker_clock_not_verified": self.broker_clock_verified,
            "market_data_stale": self.market_data_fresh,
            "durable_journal_unavailable": self.durable_journal_ready,
            "secret_source_insecure": self.secret_source_secure,
            "unresolved_submissions": self.unresolved_submissions == 0,
            "unexplained_orders": self.unexplained_orders == 0,
            "unexplained_positions": self.unexplained_positions == 0,
        }
        return tuple(name for name, passed in checks.items() if not passed)

    @property
    def ready(self) -> bool:
        return not self.failures()

    def as_dict(self) -> dict[str, Any]:
        return {
            "environment_verified": self.environment_verified,
            "account_verified": self.account_verified,
            "account_active": self.account_active,
            "account_unblocked": self.account_unblocked,
            "reconciliation_complete": self.reconciliation_complete,
            "broker_clock_verified": self.broker_clock_verified,
            "market_data_fresh": self.market_data_fresh,
            "durable_journal_ready": self.durable_journal_ready,
            "secret_source_secure": self.secret_source_secure,
            "unresolved_submissions": self.unresolved_submissions,
            "unexplained_orders": self.unexplained_orders,
            "unexplained_positions": self.unexplained_positions,
            "failures": list(self.failures()),
            "ready": self.ready,
        }


@dataclass(frozen=True, slots=True)
class ArmedExecutionContext:
    """Validated paper-only capability passed to an external adapter."""

    record_id: str
    environment: ExecutionMode
    strategy_namespace: str
    fingerprint: ExecutionFingerprint
    armed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.environment is not ExecutionMode.PAPER:
            raise ValueError("only paper execution contexts are available")
        _require_aware(self.armed_at, "armed_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.armed_at:
            raise ValueError("execution context must remain valid after arming")

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "environment": self.environment.value,
            "strategy_namespace": self.strategy_namespace,
            "fingerprint": self.fingerprint.as_dict(),
            "armed_at": self.armed_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


class ExecutionGate:
    """Non-bypassable profile and fingerprint validation."""

    @staticmethod
    def require_simulation(mode: str | ExecutionMode) -> ExecutionMode:
        try:
            parsed = ExecutionMode(mode)
        except ValueError as exc:
            raise ValueError("unsupported execution mode") from exc
        if parsed is not ExecutionMode.SIMULATION:
            raise ValueError("this command permits only offline simulation")
        return parsed

    @staticmethod
    def arm_paper(
        *,
        requested_mode: str | ExecutionMode,
        record: ArmingRecord,
        expected_namespace: str,
        expected_fingerprint: ExecutionFingerprint,
        preflight: BrokerPreflight,
        now: datetime,
    ) -> ArmedExecutionContext:
        try:
            parsed = ExecutionMode(requested_mode)
        except ValueError as exc:
            raise ValueError("unsupported execution mode") from exc
        if parsed is ExecutionMode.LIVE:
            raise ValueError("live execution is unavailable in this build")
        if parsed is not ExecutionMode.PAPER:
            raise ValueError("paper arming requires requested mode 'paper'")
        if record.state_at(now) is not GateState.ARMED:
            raise ValueError("paper arming record is not currently active")
        if record.environment is not parsed:
            raise ValueError("arming record environment does not match")
        if record.strategy_namespace != expected_namespace:
            raise ValueError("arming record strategy namespace does not match")
        for name, actual, expected in (
            (
                "code",
                record.fingerprint.code_sha256,
                expected_fingerprint.code_sha256,
            ),
            (
                "configuration",
                record.fingerprint.configuration_sha256,
                expected_fingerprint.configuration_sha256,
            ),
            (
                "account",
                record.fingerprint.account_sha256,
                expected_fingerprint.account_sha256,
            ),
        ):
            if not hmac.compare_digest(actual, expected):
                raise ValueError(f"arming record {name} fingerprint does not match")
        failures = preflight.failures()
        if failures:
            raise ValueError(f"paper preflight failed: {', '.join(failures)}")
        return ArmedExecutionContext(
            record_id=record.record_id,
            environment=parsed,
            strategy_namespace=record.strategy_namespace,
            fingerprint=record.fingerprint,
            armed_at=now,
            expires_at=record.expires_at,
        )

    @staticmethod
    def require_live(*_: object, **__: object) -> None:
        raise ValueError("live execution is unavailable in this build")
