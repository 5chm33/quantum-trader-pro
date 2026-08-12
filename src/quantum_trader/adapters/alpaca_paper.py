"""Alpaca paper-trading adapter with fail-closed idempotency boundaries."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast
from urllib.parse import quote, urlencode

from quantum_trader.domain.brokerage import (
    BROKER_CLIENT_ORDER_ID_PATTERN,
    AccountStatus,
    ApprovedBrokerOrder,
    BrokerAccountSnapshot,
    BrokerActivityPage,
    BrokerCancelResult,
    BrokerClockSnapshot,
    BrokerFillActivity,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
)
from quantum_trader.domain.execution import ArmedExecutionContext, ExecutionMode, sha256_text
from quantum_trader.domain.models import Side
from quantum_trader.domain.rate_limits import (
    RequestBudgetExceeded,
    SlidingWindowRequestBudget,
)
from quantum_trader.ports.external_broker import (
    ExternalBrokerError,
    ExternalSubmissionAmbiguous,
    ExternalSubmissionRejected,
)

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
PAPER_HOST = "paper-api.alpaca.markets"
_ALLOWED_METHODS = frozenset({"GET", "POST", "DELETE"})


class AlpacaPaperError(ExternalBrokerError):
    """Base error that never contains response bodies or credential values."""


class AlpacaPaperConfigurationError(AlpacaPaperError):
    """The adapter or execution context violates a static paper-only invariant."""


class AlpacaPaperResponseError(AlpacaPaperError):
    """The sandbox returned an unexpected HTTP or payload response."""


class AmbiguousPaperRequest(AlpacaPaperError):
    """The request may have reached the sandbox but no response was observed."""


class UnresolvedPaperSubmission(AlpacaPaperError, ExternalSubmissionAmbiguous):
    """A timed-out submission could not be resolved by deterministic client ID."""


class AlpacaPaperSubmissionRejected(AlpacaPaperError, ExternalSubmissionRejected):
    """The sandbox definitively rejected a submitted paper order."""


@dataclass(frozen=True, slots=True, repr=False)
class AlpacaPaperCredentials:
    """In-memory paper credentials whose representation is always redacted."""

    key_id: str
    secret_key: str

    def __post_init__(self) -> None:
        if not self.key_id or not self.secret_key:
            raise AlpacaPaperConfigurationError("paper credentials must not be empty")
        if any(character.isspace() for character in self.key_id + self.secret_key):
            raise AlpacaPaperConfigurationError("paper credentials must not contain whitespace")

    def __repr__(self) -> str:
        return "AlpacaPaperCredentials(key_id='[REDACTED]', secret_key='[REDACTED]')"


@dataclass(frozen=True, slots=True)
class PaperTransportResponse:
    """Raw-hash-bearing response returned by a paper transport."""

    status_code: int
    payload: object | None
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be a valid HTTP status")
        if len(self.raw_payload_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.raw_payload_sha256
        ):
            raise ValueError("raw_payload_sha256 must be a lowercase SHA-256 digest")


class AlpacaPaperTransport(Protocol):
    """Narrow transport surface used by the adapter and deterministic test doubles."""

    @property
    def base_url(self) -> str:
        """Return the exact configured origin."""

    def request(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str | int | bool] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> PaperTransportResponse:
        """Perform one request without retrying externally visible side effects."""


class AlpacaPaperHttpTransport:
    """Standard-library HTTPS transport pinned to Alpaca's paper origin."""

    def __init__(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        timeout_seconds: float = 10.0,
        request_budget: SlidingWindowRequestBudget | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0 < timeout_seconds <= 60:
            raise AlpacaPaperConfigurationError("timeout_seconds must be in (0, 60]")
        self._credentials = credentials
        self._timeout_seconds = timeout_seconds
        self._request_budget = request_budget or SlidingWindowRequestBudget()
        self._now = now or _system_now

    @property
    def base_url(self) -> str:
        return PAPER_BASE_URL

    def request(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str | int | bool] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> PaperTransportResponse:
        normalized_method = method.upper()
        if normalized_method not in _ALLOWED_METHODS:
            raise AlpacaPaperConfigurationError("unsupported HTTP method")
        if not path.startswith("/v2/") or ".." in path or "://" in path or "?" in path:
            raise AlpacaPaperConfigurationError(
                "paper request path is outside the allowed API root"
            )
        target = path
        if query:
            normalized_query = {
                key: (str(value).lower() if isinstance(value, bool) else value)
                for key, value in query.items()
            }
            target = f"{path}?{urlencode(normalized_query)}"
        encoded_body = None
        if body is not None:
            encoded_body = json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        try:
            self._request_budget.acquire(self._now())
        except (RequestBudgetExceeded, ValueError) as exc:
            raise AlpacaPaperResponseError("local paper request budget denied the call") from exc
        headers = {
            "APCA-API-KEY-ID": self._credentials.key_id,
            "APCA-API-SECRET-KEY": self._credentials.secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "quantum-trader-pro-paper/0.1",
        }
        connection = http.client.HTTPSConnection(PAPER_HOST, timeout=self._timeout_seconds)
        try:
            connection.request(normalized_method, target, body=encoded_body, headers=headers)
            response = connection.getresponse()
            raw_payload = response.read()
        except (TimeoutError, http.client.RemoteDisconnected, OSError) as exc:
            raise AmbiguousPaperRequest("paper request outcome is ambiguous") from exc
        finally:
            connection.close()
        payload: object | None = None
        if raw_payload:
            try:
                payload = json.loads(raw_payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AlpacaPaperResponseError("paper response was not valid JSON") from exc
        return PaperTransportResponse(
            status_code=response.status,
            payload=payload,
            raw_payload_sha256=hashlib.sha256(raw_payload).hexdigest(),
        )


class AlpacaPaperBroker:
    """Normalized paper broker that never infers or permits a live environment."""

    def __init__(
        self,
        *,
        transport: AlpacaPaperTransport,
        expected_account_sha256: str,
        strategy_namespace: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if transport.base_url != PAPER_BASE_URL:
            raise AlpacaPaperConfigurationError("transport must use the exact Alpaca paper origin")
        if len(expected_account_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in expected_account_sha256
        ):
            raise AlpacaPaperConfigurationError(
                "expected_account_sha256 must be a lowercase SHA-256 digest"
            )
        namespace = strategy_namespace.strip()
        if not namespace or not namespace.replace("-", "").replace("_", "").isalnum():
            raise AlpacaPaperConfigurationError("strategy_namespace has invalid characters")
        self._transport = transport
        self._expected_account_sha256 = expected_account_sha256
        self._strategy_namespace = namespace
        self._now = now or _system_now

    @property
    def environment(self) -> ExecutionMode:
        return ExecutionMode.PAPER

    @property
    def account_sha256(self) -> str:
        return self._expected_account_sha256

    def verify_context(self, context: ArmedExecutionContext) -> None:
        now = self._now()
        _require_aware(now, "adapter clock")
        if context.environment is not ExecutionMode.PAPER:
            raise AlpacaPaperConfigurationError("execution context is not paper")
        if context.strategy_namespace != self._strategy_namespace:
            raise AlpacaPaperConfigurationError("strategy namespace does not match adapter")
        if not hmac.compare_digest(
            context.fingerprint.account_sha256,
            self._expected_account_sha256,
        ):
            raise AlpacaPaperConfigurationError("account fingerprint does not match adapter")
        if now < context.armed_at or now >= context.expires_at:
            raise AlpacaPaperConfigurationError("paper execution context is not currently active")

    def get_account(self) -> BrokerAccountSnapshot:
        response = self._request(method="GET", path="/v2/account", expected=(200,))
        payload = _mapping(response.payload, "account")
        account_identifier = _required_string(payload, "id")
        account_sha256 = sha256_text(account_identifier)
        if not hmac.compare_digest(account_sha256, self._expected_account_sha256):
            raise AlpacaPaperResponseError("paper account fingerprint does not match configuration")
        status = _account_status(payload.get("status"))
        trading_blocked = _optional_bool(payload, "trading_blocked") or _optional_bool(
            payload,
            "trade_suspended_by_user",
        )
        return BrokerAccountSnapshot(
            environment=ExecutionMode.PAPER,
            account_sha256=account_sha256,
            status=status,
            trading_blocked=trading_blocked,
            account_blocked=_optional_bool(payload, "account_blocked"),
            transfers_blocked=_optional_bool(payload, "transfers_blocked"),
            cash=_required_decimal(payload, "cash"),
            equity=_required_decimal(payload, "equity"),
            buying_power=_required_decimal(payload, "buying_power"),
            captured_at=self._timestamp(),
            raw_payload_sha256=response.raw_payload_sha256,
        )

    def get_clock(self) -> BrokerClockSnapshot:
        response = self._request(method="GET", path="/v2/clock", expected=(200,))
        payload = _mapping(response.payload, "clock")
        return BrokerClockSnapshot(
            is_open=_required_bool(payload, "is_open"),
            timestamp=_required_datetime(payload, "timestamp"),
            next_open=_required_datetime(payload, "next_open"),
            next_close=_required_datetime(payload, "next_close"),
            raw_payload_sha256=response.raw_payload_sha256,
        )

    def list_positions(self) -> Sequence[BrokerPositionSnapshot]:
        response = self._request(method="GET", path="/v2/positions", expected=(200,))
        payloads = _sequence(response.payload, "positions")
        captured_at = self._timestamp()
        return tuple(
            BrokerPositionSnapshot(
                symbol=_required_string(payload, "symbol"),
                quantity=_required_decimal(payload, "qty"),
                average_entry_price=_required_decimal(payload, "avg_entry_price"),
                market_price=_required_decimal(payload, "current_price"),
                market_value=_required_decimal(payload, "market_value"),
                unrealized_pnl=_required_decimal(payload, "unrealized_pl"),
                captured_at=captured_at,
                raw_payload_sha256=response.raw_payload_sha256,
            )
            for payload in (_mapping(item, "position") for item in payloads)
        )

    def list_open_orders(self) -> Sequence[BrokerOrderSnapshot]:
        response = self._request(
            method="GET",
            path="/v2/orders",
            query={"status": "open", "limit": 500, "direction": "asc", "nested": False},
            expected=(200,),
        )
        return tuple(
            self._normalize_order(_mapping(item, "order"))
            for item in _sequence(response.payload, "orders")
        )

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        _require_client_order_id(client_order_id)
        response = self._transport.request(
            method="GET",
            path="/v2/orders:by_client_order_id",
            query={"client_order_id": client_order_id},
        )
        if response.status_code == 404:
            return None
        self._require_status(response, (200,))
        return self._normalize_order(_mapping(response.payload, "order"))

    def submit_once(
        self,
        *,
        context: ArmedExecutionContext,
        order: ApprovedBrokerOrder,
        submission_journal_sequence: int,
    ) -> BrokerOrderSnapshot:
        self.verify_context(context)
        if submission_journal_sequence <= 0:
            raise AlpacaPaperConfigurationError(
                "a positive durable submission journal sequence is required"
            )
        if order.strategy_namespace != self._strategy_namespace:
            raise AlpacaPaperConfigurationError("order namespace does not match adapter")
        if not hmac.compare_digest(order.account_sha256, self._expected_account_sha256):
            raise AlpacaPaperConfigurationError("order account fingerprint does not match adapter")
        existing = self.get_order_by_client_id(order.client_order_id)
        if existing is not None:
            self._require_order_identity(existing, order)
            return existing
        try:
            response = self._transport.request(
                method="POST",
                path="/v2/orders",
                body=self._order_payload(order),
            )
        except AmbiguousPaperRequest as exc:
            recovered = self.get_order_by_client_id(order.client_order_id)
            if recovered is None:
                raise UnresolvedPaperSubmission(
                    "submission outcome remains unresolved after client-ID lookup"
                ) from exc
            self._require_order_identity(recovered, order)
            return recovered
        if response.status_code != 200:
            recovered = self.get_order_by_client_id(order.client_order_id)
            if recovered is not None:
                self._require_order_identity(recovered, order)
                return recovered
            if response.status_code in {400, 403, 422}:
                raise AlpacaPaperSubmissionRejected(
                    "paper submission was rejected; response body redacted"
                )
            raise UnresolvedPaperSubmission(
                "non-success submission outcome remains unresolved after client-ID lookup"
            )
        snapshot = self._normalize_order(_mapping(response.payload, "order"))
        self._require_order_identity(snapshot, order)
        return snapshot

    def cancel_order(
        self,
        *,
        context: ArmedExecutionContext,
        broker_order_id: str,
    ) -> BrokerCancelResult:
        self.verify_context(context)
        if not broker_order_id:
            raise AlpacaPaperConfigurationError("broker_order_id must not be empty")
        path = f"/v2/orders/{quote(broker_order_id, safe='')}"
        accepted = False
        delete_hash = hashlib.sha256(b"").hexdigest()
        try:
            response = self._transport.request(method="DELETE", path=path)
            delete_hash = response.raw_payload_sha256
            accepted = response.status_code in {200, 204}
            if response.status_code not in {200, 204, 404}:
                self._require_status(response, (200, 204, 404))
        except AmbiguousPaperRequest:
            accepted = False
        observed = self.get_order_by_id(broker_order_id)
        return BrokerCancelResult(
            broker_order_id=broker_order_id,
            requested_at=self._timestamp(),
            accepted=accepted,
            observed_status=(
                observed.status if observed is not None else BrokerOrderStatus.UNKNOWN
            ),
            raw_payload_sha256=delete_hash,
        )

    def list_fill_activities(
        self,
        *,
        after: datetime | None,
        page_token: str | None,
        page_size: int,
    ) -> BrokerActivityPage:
        if not 1 <= page_size <= 100:
            raise AlpacaPaperConfigurationError("page_size must be in [1, 100]")
        query: dict[str, str | int | bool] = {
            "direction": "asc",
            "page_size": page_size,
        }
        if after is not None:
            _require_aware(after, "after")
            query["after"] = after.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if page_token is not None:
            if not page_token.strip():
                raise AlpacaPaperConfigurationError("page_token must not be empty")
            query["page_token"] = page_token
        response = self._request(
            method="GET",
            path="/v2/account/activities/FILL",
            query=query,
            expected=(200,),
        )
        payloads = _sequence(response.payload, "fill activities")
        activities = tuple(
            self._normalize_fill_activity(_mapping(item, "fill activity")) for item in payloads
        )
        next_page_token = activities[-1].activity_id if len(activities) == page_size else None
        return BrokerActivityPage(
            activities=activities,
            next_page_token=next_page_token,
            raw_payload_sha256=response.raw_payload_sha256,
        )

    def _timestamp(self) -> datetime:
        timestamp = self._now()
        _require_aware(timestamp, "adapter clock")
        return timestamp

    def _request(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str | int | bool] | None = None,
        expected: tuple[int, ...],
    ) -> PaperTransportResponse:
        response = self._transport.request(method=method, path=path, query=query)
        self._require_status(response, expected)
        return response

    @staticmethod
    def _require_status(
        response: PaperTransportResponse,
        expected: tuple[int, ...],
    ) -> None:
        if response.status_code not in expected:
            raise AlpacaPaperResponseError(
                f"paper API returned HTTP {response.status_code}; response body redacted"
            )

    @staticmethod
    def _order_payload(order: ApprovedBrokerOrder) -> dict[str, object]:
        payload: dict[str, object] = {
            "symbol": order.symbol.upper(),
            "qty": str(order.quantity),
            "side": order.side.value,
            "type": order.order_type.value,
            "time_in_force": order.time_in_force.value,
            "extended_hours": order.extended_hours,
            "client_order_id": order.client_order_id,
        }
        if order.limit_price is not None:
            payload["limit_price"] = str(order.limit_price)
        return payload

    def get_order_by_id(self, broker_order_id: str) -> BrokerOrderSnapshot | None:
        if not broker_order_id:
            raise AlpacaPaperConfigurationError("broker_order_id must not be empty")
        response = self._transport.request(
            method="GET",
            path=f"/v2/orders/{quote(broker_order_id, safe='')}",
        )
        if response.status_code == 404:
            return None
        self._require_status(response, (200,))
        return self._normalize_order(_mapping(response.payload, "order"))

    @staticmethod
    def _require_order_identity(
        snapshot: BrokerOrderSnapshot,
        order: ApprovedBrokerOrder,
    ) -> None:
        if snapshot.client_order_id != order.client_order_id:
            raise AlpacaPaperResponseError("broker response client order ID does not match")
        if snapshot.symbol.upper() != order.symbol.upper() or snapshot.side is not order.side:
            raise AlpacaPaperResponseError("broker response symbol or side does not match")
        if snapshot.quantity != Decimal(order.quantity):
            raise AlpacaPaperResponseError("broker response quantity does not match")

    @staticmethod
    def _normalize_order(payload: Mapping[str, object]) -> BrokerOrderSnapshot:
        quantity = _required_decimal(payload, "qty")
        filled_quantity = _required_decimal(payload, "filled_qty")
        average_fill_price = _optional_decimal(payload, "filled_avg_price")
        submitted_at = _required_datetime(payload, "submitted_at")
        updated_at = _optional_datetime(payload, "updated_at") or submitted_at
        return BrokerOrderSnapshot(
            broker_order_id=_required_string(payload, "id"),
            client_order_id=_required_string(payload, "client_order_id"),
            status=_order_status(payload.get("status")),
            symbol=_required_string(payload, "symbol"),
            side=_side(payload.get("side")),
            quantity=quantity,
            filled_quantity=filled_quantity,
            average_fill_price=average_fill_price,
            submitted_at=submitted_at,
            updated_at=updated_at,
            raw_payload_sha256=_raw_payload_hash(payload),
            limit_price=_optional_decimal(payload, "limit_price"),
        )

    @staticmethod
    def _normalize_fill_activity(
        payload: Mapping[str, object],
    ) -> BrokerFillActivity:
        activity_id = _required_string(payload, "id")
        return BrokerFillActivity(
            activity_id=activity_id,
            execution_id=activity_id,
            broker_order_id=_required_string(payload, "order_id"),
            client_order_id=None,
            symbol=_required_string(payload, "symbol"),
            side=_side(payload.get("side")),
            quantity=_required_decimal(payload, "qty"),
            price=_required_decimal(payload, "price"),
            fee=None,
            timestamp=_required_datetime(payload, "transaction_time"),
            raw_payload_sha256=_raw_payload_hash(payload),
        )


def _raw_payload_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _system_now() -> datetime:
    return datetime.now(UTC)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise AlpacaPaperResponseError(f"{field_name} must be timezone-aware")


def _mapping(value: object | None, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AlpacaPaperResponseError(f"{field_name} payload must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object | None, field_name: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise AlpacaPaperResponseError(f"{field_name} payload must be an array")
    return cast(Sequence[object], value)


def _required_string(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise AlpacaPaperResponseError(f"{field_name} must be a non-empty string")
    return value


def _required_decimal(payload: Mapping[str, object], field_name: str) -> Decimal:
    value = payload.get(field_name)
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise AlpacaPaperResponseError(f"{field_name} must be decimal-compatible")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise AlpacaPaperResponseError(f"{field_name} is not a decimal") from exc
    if not parsed.is_finite():
        raise AlpacaPaperResponseError(f"{field_name} must be finite")
    return parsed


def _optional_decimal(payload: Mapping[str, object], field_name: str) -> Decimal | None:
    if payload.get(field_name) is None:
        return None
    return _required_decimal(payload, field_name)


def _required_bool(payload: Mapping[str, object], field_name: str) -> bool:
    value = payload.get(field_name)
    if not isinstance(value, bool):
        raise AlpacaPaperResponseError(f"{field_name} must be boolean")
    return value


def _optional_bool(payload: Mapping[str, object], field_name: str) -> bool:
    value = payload.get(field_name, False)
    if not isinstance(value, bool):
        raise AlpacaPaperResponseError(f"{field_name} must be boolean when present")
    return value


def _required_datetime(payload: Mapping[str, object], field_name: str) -> datetime:
    value = _required_string(payload, field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AlpacaPaperResponseError(f"{field_name} is not a valid timestamp") from exc
    _require_aware(parsed, field_name)
    return parsed


def _optional_datetime(payload: Mapping[str, object], field_name: str) -> datetime | None:
    if payload.get(field_name) is None:
        return None
    return _required_datetime(payload, field_name)


def _account_status(value: object) -> AccountStatus:
    normalized = str(value).lower()
    if normalized == "active":
        return AccountStatus.ACTIVE
    if normalized == "submission_failed":
        return AccountStatus.SUBMISSION_FAILED
    if normalized in {"action_required", "approval_pending", "account_updated"}:
        return AccountStatus.ACTION_REQUIRED
    if normalized in {"inactive", "rejected", "disabled"}:
        return AccountStatus.INACTIVE
    return AccountStatus.UNKNOWN


def _order_status(value: object) -> BrokerOrderStatus:
    normalized = str(value).lower()
    try:
        return BrokerOrderStatus(normalized)
    except ValueError:
        return BrokerOrderStatus.UNKNOWN


def _side(value: object) -> Side:
    try:
        return Side(str(value).lower())
    except ValueError as exc:
        raise AlpacaPaperResponseError("side must be buy or sell") from exc


def _require_client_order_id(client_order_id: str) -> None:
    if not BROKER_CLIENT_ORDER_ID_PATTERN.fullmatch(client_order_id):
        raise AlpacaPaperConfigurationError("client_order_id has invalid characters or length")
