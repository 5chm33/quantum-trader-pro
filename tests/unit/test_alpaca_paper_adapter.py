from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantum_trader.adapters.alpaca_paper import (
    PAPER_BASE_URL,
    AlpacaPaperBroker,
    AlpacaPaperConfigurationError,
    AlpacaPaperCredentials,
    AlpacaPaperHttpTransport,
    AlpacaPaperResponseError,
    AmbiguousPaperRequest,
    PaperTransportResponse,
    UnresolvedPaperSubmission,
)
from quantum_trader.domain.brokerage import (
    ApprovedBrokerOrder,
    BrokerOrderStatus,
    BrokerOrderType,
    TimeInForce,
)
from quantum_trader.domain.execution import (
    PAPER_ACKNOWLEDGEMENT,
    ArmingRecord,
    BrokerPreflight,
    ExecutionFingerprint,
    ExecutionGate,
    ExecutionMode,
    sha256_text,
)
from quantum_trader.domain.models import OrderIntent, RiskDecision, Side

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
ACCOUNT_ID = "paper-account-fixture"
ACCOUNT_HASH = sha256_text(ACCOUNT_ID)
A = "a" * 64
B = "b" * 64
RAW = "d" * 64


@dataclass(slots=True)
class ExpectedCall:
    method: str
    path: str
    response: PaperTransportResponse | Exception
    query: Mapping[str, str | int | bool] | None = None
    body: Mapping[str, object] | None = None


@dataclass(slots=True)
class ScriptedTransport:
    expected: list[ExpectedCall]
    base_url: str = PAPER_BASE_URL
    calls: list[
        tuple[str, str, Mapping[str, str | int | bool] | None, Mapping[str, object] | None]
    ] = field(default_factory=list)

    def request(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str | int | bool] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> PaperTransportResponse:
        self.calls.append((method, path, query, body))
        if not self.expected:
            raise AssertionError(f"unexpected transport call: {method} {path}")
        expected = self.expected.pop(0)
        assert method == expected.method
        assert path == expected.path
        assert query == expected.query
        assert body == expected.body
        if isinstance(expected.response, Exception):
            raise expected.response
        return expected.response


def fixture_credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(
        key_id="fixture-key",
        secret_key="-".join(("fixture", "secret")),
    )


def response(payload: object | None, status_code: int = 200) -> PaperTransportResponse:
    raw = (
        b""
        if payload is None
        else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return PaperTransportResponse(
        status_code=status_code,
        payload=payload,
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
    )


def ready_preflight() -> BrokerPreflight:
    return BrokerPreflight(
        environment_verified=True,
        account_verified=True,
        account_active=True,
        account_unblocked=True,
        reconciliation_complete=True,
        broker_clock_verified=True,
        market_data_fresh=True,
        durable_journal_ready=True,
        secret_source_secure=True,
    )


def armed_context(*, expires_at: datetime | None = None):
    fingerprint = ExecutionFingerprint(A, B, ACCOUNT_HASH)
    record = ArmingRecord.issue_paper(
        strategy_namespace="qtpro-paper",
        fingerprint=fingerprint,
        issued_at=NOW,
        ttl=timedelta(hours=1),
        acknowledgement=PAPER_ACKNOWLEDGEMENT,
    )
    context = ExecutionGate.arm_paper(
        requested_mode=ExecutionMode.PAPER,
        record=record,
        expected_namespace="qtpro-paper",
        expected_fingerprint=fingerprint,
        preflight=ready_preflight(),
        now=NOW + timedelta(minutes=1),
    )
    if expires_at is not None:
        return replace(context, expires_at=expires_at)
    return context


def approved_order() -> ApprovedBrokerOrder:
    intent = OrderIntent.create(
        correlation_id="paper-correlation",
        timestamp=NOW,
        symbol="SPY",
        side=Side.BUY,
        quantity=4,
        reference_price=Decimal("500"),
        rationale="paper fixture",
    )
    decision = RiskDecision(
        allowed=True,
        reason="within_limits",
        approved_quantity=4,
        intent_id=intent.intent_id,
        correlation_id=intent.correlation_id,
    )
    return ApprovedBrokerOrder.from_approved_intent(
        context=armed_context(),
        intent=intent,
        decision=decision,
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        limit_price=Decimal("499.50"),
    )


def order_payload(
    order: ApprovedBrokerOrder,
    *,
    status: str = "new",
    filled_qty: str = "0",
    filled_avg_price: str | None = None,
    client_order_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": "broker-order-1",
        "client_order_id": client_order_id or order.client_order_id,
        "status": status,
        "symbol": order.symbol,
        "side": order.side.value,
        "qty": str(order.quantity),
        "filled_qty": filled_qty,
        "filled_avg_price": filled_avg_price,
        "submitted_at": NOW.isoformat(),
        "updated_at": (NOW + timedelta(seconds=1)).isoformat(),
    }


def adapter(transport: ScriptedTransport, *, now: datetime | None = None) -> AlpacaPaperBroker:
    timestamp = now or NOW + timedelta(minutes=2)
    return AlpacaPaperBroker(
        transport=transport,
        expected_account_sha256=ACCOUNT_HASH,
        strategy_namespace="qtpro-paper",
        now=lambda: timestamp,
    )


def test_credentials_and_http_transport_are_paper_pinned_and_redacted() -> None:
    secret = "-".join(("fixture", "credential"))
    credentials = AlpacaPaperCredentials(key_id="fixture-key", secret_key=secret)
    assert "fixture-key" not in repr(credentials)
    assert secret not in repr(credentials)
    transport = AlpacaPaperHttpTransport(credentials=credentials, timeout_seconds=1)
    assert transport.base_url == PAPER_BASE_URL

    with pytest.raises(AlpacaPaperConfigurationError, match="unsupported HTTP method"):
        transport.request(method="PATCH", path="/v2/orders")
    with pytest.raises(AlpacaPaperConfigurationError, match="outside"):
        transport.request(method="GET", path="https://api.alpaca.markets/v2/account")
    with pytest.raises(AlpacaPaperConfigurationError, match="empty"):
        AlpacaPaperCredentials(key_id="", secret_key=secret)
    with pytest.raises(AlpacaPaperConfigurationError, match="timeout_seconds"):
        AlpacaPaperHttpTransport(credentials=credentials, timeout_seconds=0)


def test_adapter_rejects_live_origin_bad_identity_and_expired_context() -> None:
    wrong_origin = ScriptedTransport(expected=[], base_url="https://api.alpaca.markets")
    with pytest.raises(AlpacaPaperConfigurationError, match="exact Alpaca paper origin"):
        adapter(wrong_origin)
    with pytest.raises(AlpacaPaperConfigurationError, match="SHA-256"):
        AlpacaPaperBroker(
            transport=ScriptedTransport(expected=[]),
            expected_account_sha256="bad",
            strategy_namespace="qtpro-paper",
        )

    broker = adapter(ScriptedTransport(expected=[]))
    expired = armed_context(expires_at=NOW + timedelta(seconds=90))
    with pytest.raises(AlpacaPaperConfigurationError, match="not currently active"):
        broker.verify_context(expired)
    wrong_namespace = replace(armed_context(), strategy_namespace="other")
    with pytest.raises(AlpacaPaperConfigurationError, match="namespace"):
        broker.verify_context(wrong_namespace)


def test_account_clock_positions_and_foreign_open_orders_are_normalized() -> None:
    account = {
        "id": ACCOUNT_ID,
        "status": "ACTIVE",
        "trade_suspended_by_user": False,
        "trading_blocked": False,
        "account_blocked": False,
        "transfers_blocked": False,
        "cash": "10000.00",
        "equity": "11000.00",
        "buying_power": "10000.00",
    }
    clock = {
        "is_open": True,
        "timestamp": NOW.isoformat(),
        "next_open": (NOW + timedelta(days=1)).isoformat(),
        "next_close": (NOW + timedelta(hours=6)).isoformat(),
    }
    positions = [
        {
            "symbol": "SPY",
            "qty": "2.5",
            "avg_entry_price": "490",
            "current_price": "500",
            "market_value": "1250",
            "unrealized_pl": "25",
        }
    ]
    foreign_order = {
        "id": "foreign-order",
        "client_order_id": "manual:desk.order-1",
        "status": "accepted",
        "symbol": "QQQ",
        "side": "sell",
        "qty": "1",
        "filled_qty": "0",
        "filled_avg_price": None,
        "submitted_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    transport = ScriptedTransport(
        expected=[
            ExpectedCall("GET", "/v2/account", response(account)),
            ExpectedCall("GET", "/v2/clock", response(clock)),
            ExpectedCall("GET", "/v2/positions", response(positions)),
            ExpectedCall(
                "GET",
                "/v2/orders",
                response([foreign_order]),
                query={"status": "open", "limit": 500, "direction": "asc", "nested": False},
            ),
        ]
    )
    broker = adapter(transport)

    normalized_account = broker.get_account()
    assert normalized_account.environment is ExecutionMode.PAPER
    assert normalized_account.permits_new_exposure is True
    assert broker.get_clock().is_open is True
    assert broker.list_positions()[0].quantity == Decimal("2.5")
    normalized_order = broker.list_open_orders()[0]
    assert normalized_order.client_order_id == "manual:desk.order-1"
    assert normalized_order.side is Side.SELL
    assert transport.expected == []


def test_account_identity_mismatch_and_errors_are_redacted() -> None:
    mismatch = {
        "id": "different-account",
        "status": "ACTIVE",
        "cash": "1",
        "equity": "1",
        "buying_power": "1",
    }
    broker = adapter(
        ScriptedTransport(expected=[ExpectedCall("GET", "/v2/account", response(mismatch))])
    )
    with pytest.raises(AlpacaPaperResponseError, match="fingerprint"):
        broker.get_account()

    sensitive = "do-not-leak-response-value"
    failure = response({"message": sensitive}, status_code=403)
    broker = adapter(ScriptedTransport(expected=[ExpectedCall("GET", "/v2/account", failure)]))
    with pytest.raises(AlpacaPaperResponseError) as captured:
        broker.get_account()
    assert sensitive not in str(captured.value)
    assert "403" in str(captured.value)


def test_submit_once_returns_existing_order_without_posting() -> None:
    order = approved_order()
    existing = response(order_payload(order))
    transport = ScriptedTransport(
        expected=[
            ExpectedCall(
                "GET",
                "/v2/orders:by_client_order_id",
                existing,
                query={"client_order_id": order.client_order_id},
            )
        ]
    )
    snapshot = adapter(transport).submit_once(
        context=armed_context(),
        order=order,
        submission_journal_sequence=1,
    )
    assert snapshot.client_order_id == order.client_order_id
    assert [call[0] for call in transport.calls] == ["GET"]


def test_submit_once_posts_exactly_once_after_negative_lookup() -> None:
    order = approved_order()
    expected_body: dict[str, object] = {
        "symbol": "SPY",
        "qty": "4",
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "extended_hours": False,
        "client_order_id": order.client_order_id,
        "limit_price": "499.50",
    }
    transport = ScriptedTransport(
        expected=[
            ExpectedCall(
                "GET",
                "/v2/orders:by_client_order_id",
                response({"message": "not found"}, 404),
                query={"client_order_id": order.client_order_id},
            ),
            ExpectedCall(
                "POST",
                "/v2/orders",
                response(order_payload(order)),
                body=expected_body,
            ),
        ]
    )
    snapshot = adapter(transport).submit_once(
        context=armed_context(),
        order=order,
        submission_journal_sequence=7,
    )
    assert snapshot.status is BrokerOrderStatus.NEW
    assert [call[0] for call in transport.calls].count("POST") == 1


def test_ambiguous_submit_queries_by_client_id_and_never_blindly_retries() -> None:
    order = approved_order()
    transport = ScriptedTransport(
        expected=[
            ExpectedCall(
                "GET",
                "/v2/orders:by_client_order_id",
                response(None, 404),
                query={"client_order_id": order.client_order_id},
            ),
            ExpectedCall(
                "POST",
                "/v2/orders",
                AmbiguousPaperRequest("fixture timeout"),
                body={
                    "symbol": "SPY",
                    "qty": "4",
                    "side": "buy",
                    "type": "limit",
                    "time_in_force": "day",
                    "extended_hours": False,
                    "client_order_id": order.client_order_id,
                    "limit_price": "499.50",
                },
            ),
            ExpectedCall(
                "GET",
                "/v2/orders:by_client_order_id",
                response(order_payload(order)),
                query={"client_order_id": order.client_order_id},
            ),
        ]
    )
    snapshot = adapter(transport).submit_once(
        context=armed_context(),
        order=order,
        submission_journal_sequence=2,
    )
    assert snapshot.broker_order_id == "broker-order-1"
    assert [call[0] for call in transport.calls] == ["GET", "POST", "GET"]


def test_ambiguous_unresolved_submission_stays_halted() -> None:
    order = approved_order()
    transport = ScriptedTransport(
        expected=[
            ExpectedCall(
                "GET",
                "/v2/orders:by_client_order_id",
                response(None, 404),
                query={"client_order_id": order.client_order_id},
            ),
            ExpectedCall(
                "POST",
                "/v2/orders",
                AmbiguousPaperRequest("fixture timeout"),
                body={
                    "symbol": "SPY",
                    "qty": "4",
                    "side": "buy",
                    "type": "limit",
                    "time_in_force": "day",
                    "extended_hours": False,
                    "client_order_id": order.client_order_id,
                    "limit_price": "499.50",
                },
            ),
            ExpectedCall(
                "GET",
                "/v2/orders:by_client_order_id",
                response(None, 404),
                query={"client_order_id": order.client_order_id},
            ),
        ]
    )
    with pytest.raises(UnresolvedPaperSubmission, match="unresolved"):
        adapter(transport).submit_once(
            context=armed_context(),
            order=order,
            submission_journal_sequence=2,
        )
    assert [call[0] for call in transport.calls].count("POST") == 1


def test_submit_rejects_missing_journal_and_response_identity_drift() -> None:
    order = approved_order()
    broker = adapter(ScriptedTransport(expected=[]))
    with pytest.raises(AlpacaPaperConfigurationError, match="journal"):
        broker.submit_once(
            context=armed_context(),
            order=order,
            submission_journal_sequence=0,
        )

    transport = ScriptedTransport(
        expected=[
            ExpectedCall(
                "GET",
                "/v2/orders:by_client_order_id",
                response(order_payload(order, client_order_id="foreign-order")),
                query={"client_order_id": order.client_order_id},
            )
        ]
    )
    with pytest.raises(AlpacaPaperResponseError, match="client order ID"):
        adapter(transport).submit_once(
            context=armed_context(),
            order=order,
            submission_journal_sequence=1,
        )


def test_cancel_verifies_terminal_state_and_handles_ambiguous_delete() -> None:
    order = approved_order()
    canceled_payload = order_payload(order, status="canceled")
    transport = ScriptedTransport(
        expected=[
            ExpectedCall("DELETE", "/v2/orders/broker-order-1", response(None, 204)),
            ExpectedCall("GET", "/v2/orders/broker-order-1", response(canceled_payload)),
        ]
    )
    result = adapter(transport).cancel_order(
        context=armed_context(),
        broker_order_id="broker-order-1",
    )
    assert result.accepted is True
    assert result.verified_terminal is True

    filled_payload = order_payload(
        order,
        status="filled",
        filled_qty="4",
        filled_avg_price="500",
    )
    ambiguous = ScriptedTransport(
        expected=[
            ExpectedCall(
                "DELETE",
                "/v2/orders/broker-order-1",
                AmbiguousPaperRequest("fixture timeout"),
            ),
            ExpectedCall("GET", "/v2/orders/broker-order-1", response(filled_payload)),
        ]
    )
    result = adapter(ambiguous).cancel_order(
        context=armed_context(),
        broker_order_id="broker-order-1",
    )
    assert result.accepted is False
    assert result.observed_status is BrokerOrderStatus.FILLED


def test_fill_activity_pagination_preserves_broker_execution_identity() -> None:
    after = NOW - timedelta(days=1)
    page_cursor = "::".join(("prior", "cursor"))
    activities = [
        {
            "id": "20260812143000000::execution-1",
            "order_id": "broker-order-1",
            "symbol": "SPY",
            "side": "buy",
            "qty": "2",
            "price": "499.50",
            "transaction_time": NOW.isoformat(),
        },
        {
            "id": "20260812143100000::execution-2",
            "order_id": "broker-order-1",
            "symbol": "SPY",
            "side": "buy",
            "qty": "2",
            "price": "499.75",
            "transaction_time": (NOW + timedelta(minutes=1)).isoformat(),
        },
    ]
    transport = ScriptedTransport(
        expected=[
            ExpectedCall(
                "GET",
                "/v2/account/activities/FILL",
                response(activities),
                query={
                    "direction": "asc",
                    "page_size": 2,
                    "after": after.isoformat().replace("+00:00", "Z"),
                    "page_token": page_cursor,
                },
            )
        ]
    )
    page = adapter(transport).list_fill_activities(
        after=after,
        page_token=page_cursor,
        page_size=2,
    )
    assert page.next_page_token == activities[-1]["id"]
    assert page.activities[0].execution_id == activities[0]["id"]
    assert page.activities[0].client_order_id is None
    assert page.activities[0].fee is None

    with pytest.raises(AlpacaPaperConfigurationError, match="page_size"):
        adapter(ScriptedTransport(expected=[])).list_fill_activities(
            after=None,
            page_token=None,
            page_size=101,
        )


def test_malformed_broker_payloads_and_unknown_status_fail_safely() -> None:
    order = approved_order()
    malformed = ScriptedTransport(
        expected=[
            ExpectedCall(
                "GET",
                "/v2/orders:by_client_order_id",
                response(["not", "an", "object"]),
                query={"client_order_id": order.client_order_id},
            )
        ]
    )
    with pytest.raises(AlpacaPaperResponseError, match="object"):
        adapter(malformed).get_order_by_client_id(order.client_order_id)

    unknown = order_payload(order, status="provider_added_state")
    transport = ScriptedTransport(
        expected=[
            ExpectedCall(
                "GET",
                "/v2/orders:by_client_order_id",
                response(unknown),
                query={"client_order_id": order.client_order_id},
            )
        ]
    )
    snapshot = adapter(transport).get_order_by_client_id(order.client_order_id)
    assert snapshot is not None
    assert snapshot.status is BrokerOrderStatus.UNKNOWN

    with pytest.raises(AlpacaPaperConfigurationError, match="client_order_id"):
        adapter(ScriptedTransport(expected=[])).get_order_by_client_id("bad id")


def test_http_transport_encodes_requests_and_hashes_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_response = b'{"status":"ok"}'
    observed: dict[str, object] = {}

    class FakeResponse:
        status = 200

        @staticmethod
        def read() -> bytes:
            return raw_response

    class FakeConnection:
        def __init__(self, host: str, *, timeout: float) -> None:
            observed["host"] = host
            observed["timeout"] = timeout

        def request(
            self,
            method: str,
            target: str,
            *,
            body: bytes | None,
            headers: Mapping[str, str],
        ) -> None:
            observed["method"] = method
            observed["target"] = target
            observed["body"] = body
            observed["headers"] = dict(headers)

        @staticmethod
        def getresponse() -> FakeResponse:
            return FakeResponse()

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(
        "quantum_trader.adapters.alpaca_paper.http.client.HTTPSConnection",
        FakeConnection,
    )
    credentials = fixture_credentials()
    transport = AlpacaPaperHttpTransport(credentials=credentials, timeout_seconds=3)
    result = transport.request(
        method="POST",
        path="/v2/orders",
        query={"nested": False, "limit": 2},
        body={"symbol": "SPY", "qty": "1"},
    )

    assert observed["host"] == "paper-api.alpaca.markets"
    assert observed["target"] == "/v2/orders?nested=false&limit=2"
    assert observed["body"] == b'{"qty":"1","symbol":"SPY"}'
    headers = observed["headers"]
    assert isinstance(headers, dict)
    assert headers["APCA-API-KEY-ID"] == "fixture-key"
    assert headers["APCA-API-SECRET-KEY"] == "fixture-secret"
    assert result.payload == {"status": "ok"}
    assert result.raw_payload_sha256 == hashlib.sha256(raw_response).hexdigest()


def test_http_transport_rejects_invalid_json_and_classifies_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidJsonResponse:
        status = 200

        @staticmethod
        def read() -> bytes:
            return b"not-json"

    class InvalidJsonConnection:
        def __init__(self, host: str, *, timeout: float) -> None:
            del host, timeout

        @staticmethod
        def request(
            method: str,
            target: str,
            *,
            body: bytes | None,
            headers: Mapping[str, str],
        ) -> None:
            del method, target, body, headers

        @staticmethod
        def getresponse() -> InvalidJsonResponse:
            return InvalidJsonResponse()

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(
        "quantum_trader.adapters.alpaca_paper.http.client.HTTPSConnection",
        InvalidJsonConnection,
    )
    credentials = fixture_credentials()
    transport = AlpacaPaperHttpTransport(credentials=credentials)
    with pytest.raises(AlpacaPaperResponseError, match="valid JSON"):
        transport.request(method="GET", path="/v2/account")

    class TimeoutConnection(InvalidJsonConnection):
        @staticmethod
        def request(
            method: str,
            target: str,
            *,
            body: bytes | None,
            headers: Mapping[str, str],
        ) -> None:
            del method, target, body, headers
            raise TimeoutError

    monkeypatch.setattr(
        "quantum_trader.adapters.alpaca_paper.http.client.HTTPSConnection",
        TimeoutConnection,
    )
    with pytest.raises(AmbiguousPaperRequest, match="ambiguous"):
        transport.request(method="GET", path="/v2/account")
