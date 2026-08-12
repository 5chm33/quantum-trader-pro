"""Paper-only operator approvals and durable kill-switch state contracts."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from quantum_trader.domain.execution import ExecutionFingerprint, ExecutionMode

MAX_OPERATOR_APPROVAL_TTL = timedelta(minutes=10)
MIN_OPERATOR_KEY_BYTES = 32


class OperatorAction(StrEnum):
    RESUME = "resume"
    CANCEL_OWNED_ORDERS = "cancel_owned_orders"
    FLATTEN_OWNED_POSITIONS = "flatten_owned_positions"


class OperatorActionState(StrEnum):
    AUTHORIZED = "authorized"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


ACKNOWLEDGEMENTS: dict[OperatorAction, str] = {
    OperatorAction.RESUME: "I AUTHORIZE PAPER EXECUTION TO RESUME",
    OperatorAction.CANCEL_OWNED_ORDERS: ("I AUTHORIZE CANCELLATION OF BOT-OWNED PAPER ORDERS"),
    OperatorAction.FLATTEN_OWNED_POSITIONS: ("I AUTHORIZE CLOSING BOT-OWNED PAPER POSITIONS"),
}


@dataclass(frozen=True, slots=True)
class OperatorApproval:
    """One expiring HMAC-authenticated approval for one paper-only action."""

    approval_id: str
    action: OperatorAction
    environment: ExecutionMode
    strategy_namespace: str
    fingerprint: ExecutionFingerprint
    issued_at: datetime
    expires_at: datetime
    nonce: str
    acknowledgement_sha256: str
    signature_sha256: str

    @classmethod
    def issue(
        cls,
        *,
        action: OperatorAction,
        strategy_namespace: str,
        fingerprint: ExecutionFingerprint,
        issued_at: datetime,
        ttl: timedelta,
        nonce: str,
        acknowledgement: str,
        control_key: bytes,
    ) -> OperatorApproval:
        _aware(issued_at, "issued_at")
        _control_key(control_key)
        namespace = _namespace(strategy_namespace)
        normalized_nonce = _nonce(nonce)
        if acknowledgement != ACKNOWLEDGEMENTS[action]:
            raise ValueError("operator acknowledgement did not match the required action text")
        if ttl <= timedelta(0) or ttl > MAX_OPERATOR_APPROVAL_TTL:
            raise ValueError("operator approval TTL must be positive and no longer than 10 minutes")
        expires_at = issued_at + ttl
        acknowledgement_sha256 = hashlib.sha256(acknowledgement.encode()).hexdigest()
        unsigned = {
            "action": action.value,
            "environment": ExecutionMode.PAPER.value,
            "strategy_namespace": namespace,
            "fingerprint": fingerprint.as_dict(),
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "nonce": normalized_nonce,
            "acknowledgement_sha256": acknowledgement_sha256,
        }
        approval_id = hashlib.sha256(_canonical(unsigned)).hexdigest()
        signature = hmac.new(
            control_key,
            _canonical({**unsigned, "approval_id": approval_id}),
            hashlib.sha256,
        ).hexdigest()
        return cls(
            approval_id=approval_id,
            action=action,
            environment=ExecutionMode.PAPER,
            strategy_namespace=namespace,
            fingerprint=fingerprint,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=normalized_nonce,
            acknowledgement_sha256=acknowledgement_sha256,
            signature_sha256=signature,
        )

    def __post_init__(self) -> None:
        if self.environment is not ExecutionMode.PAPER:
            raise ValueError("operator approvals are paper-only")
        _namespace(self.strategy_namespace)
        _aware(self.issued_at, "issued_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("operator approval expiry must follow issuance")
        if self.expires_at - self.issued_at > MAX_OPERATOR_APPROVAL_TTL:
            raise ValueError("operator approval exceeds maximum TTL")
        _nonce(self.nonce)
        _sha256(self.approval_id, "approval_id")
        _sha256(self.acknowledgement_sha256, "acknowledgement_sha256")
        _sha256(self.signature_sha256, "signature_sha256")

    def verify(
        self,
        *,
        expected_action: OperatorAction,
        expected_namespace: str,
        expected_fingerprint: ExecutionFingerprint,
        control_key: bytes,
        now: datetime,
    ) -> None:
        _control_key(control_key)
        _aware(now, "now")
        if now < self.issued_at or now >= self.expires_at:
            raise ValueError("operator approval is not currently active")
        if self.action is not expected_action:
            raise ValueError("operator approval action does not match")
        if self.strategy_namespace != _namespace(expected_namespace):
            raise ValueError("operator approval strategy namespace does not match")
        for name, actual, expected in (
            ("code", self.fingerprint.code_sha256, expected_fingerprint.code_sha256),
            (
                "configuration",
                self.fingerprint.configuration_sha256,
                expected_fingerprint.configuration_sha256,
            ),
            (
                "account",
                self.fingerprint.account_sha256,
                expected_fingerprint.account_sha256,
            ),
        ):
            if not hmac.compare_digest(actual, expected):
                raise ValueError(f"operator approval {name} fingerprint does not match")
        expected_signature = hmac.new(
            control_key,
            _canonical(self._signed_payload()),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(self.signature_sha256, expected_signature):
            raise ValueError("operator approval signature does not match")

    def _unsigned_payload(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "environment": self.environment.value,
            "strategy_namespace": self.strategy_namespace,
            "fingerprint": self.fingerprint.as_dict(),
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "nonce": self.nonce,
            "acknowledgement_sha256": self.acknowledgement_sha256,
        }

    def _signed_payload(self) -> dict[str, Any]:
        return {**self._unsigned_payload(), "approval_id": self.approval_id}

    def as_dict(self) -> dict[str, Any]:
        return {
            **self._signed_payload(),
            "signature_sha256": self.signature_sha256,
        }


@dataclass(frozen=True, slots=True)
class OperatorControlState:
    """Durable no-new-orders switch; a new store always starts paused."""

    paused: bool
    sequence: int
    changed_at: datetime
    reason_code: str
    reason_sha256: str
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("operator state sequence must not be negative")
        _aware(self.changed_at, "changed_at")
        _reason_code(self.reason_code)
        _sha256(self.reason_sha256, "reason_sha256")
        if self.approval_id is not None:
            _sha256(self.approval_id, "approval_id")

    def as_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "sequence": self.sequence,
            "changed_at": self.changed_at.isoformat(),
            "reason_code": self.reason_code,
            "reason_sha256": self.reason_sha256,
            "approval_id": self.approval_id,
        }


@dataclass(frozen=True, slots=True)
class OperatorActionRecord:
    approval_id: str
    action: OperatorAction
    state: OperatorActionState
    started_at: datetime
    updated_at: datetime
    summary_sha256: str | None = None

    def __post_init__(self) -> None:
        _sha256(self.approval_id, "approval_id")
        _aware(self.started_at, "started_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.started_at:
            raise ValueError("operator action update precedes its start")
        if self.summary_sha256 is not None:
            _sha256(self.summary_sha256, "summary_sha256")
        if self.state in {OperatorActionState.COMPLETED, OperatorActionState.FAILED}:
            if self.summary_sha256 is None:
                raise ValueError("terminal operator action requires summary_sha256")
        elif self.summary_sha256 is not None:
            raise ValueError("nonterminal operator action must not define summary_sha256")

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "action": self.action.value,
            "state": self.state.value,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "summary_sha256": self.summary_sha256,
        }


def hash_operator_reason(reason: str) -> str:
    normalized = reason.strip()
    if not normalized:
        raise ValueError("operator reason must not be empty")
    return hashlib.sha256(normalized.encode()).hexdigest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


def _namespace(value: str) -> str:
    normalized = value.strip()
    if not normalized or not normalized.replace("-", "").replace("_", "").isalnum():
        raise ValueError("strategy_namespace has invalid characters")
    return normalized


def _nonce(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 32 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("operator nonce must be 32 lowercase hexadecimal characters")
    return normalized


def _reason_code(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 64 or not normalized.replace("_", "").isalnum():
        raise ValueError("reason_code has invalid characters")
    return normalized


def _control_key(value: bytes) -> None:
    if len(value) < MIN_OPERATOR_KEY_BYTES:
        raise ValueError("operator control key must contain at least 32 bytes")
