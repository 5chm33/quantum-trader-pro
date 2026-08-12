from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pytest

from quantum_trader.adapters.alpaca_control_data import (
    MARKET_DATA_BASE_URL,
    AlpacaControlDataError,
    AlpacaMarketDataHttpTransport,
    AlpacaPaperControlData,
    ControlDataResponse,
)
from quantum_trader.adapters.alpaca_paper import (
    PAPER_BASE_URL,
    AlpacaPaperCredentials,
    PaperTransportResponse,
)

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
RAW = "a" * 64


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(
        key_id="fixture-key",
        secret_key="-".join(("fixture", "secret")),
    )


def paper_response(payload: object | None, status: int = 200) -> PaperTransportResponse:
    encoded = (
        b""
        if payload is None
        else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    return PaperTransportResponse(
        status_code=status,
        payload=payload,
        raw_payload_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def data_response(payload: object | None, status: int = 200) -> ControlDataResponse:
    encoded = (
        b""
        if payload is None
        else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    return ControlDataResponse(
        status_code=status,
        payload=payload,
        raw_payload_sha256=hashlib.sha256(encoded).hexdigest(),
    )


@dataclass(slots=True)
class PaperCall:
    method: str
    path: str
    response: PaperTransportResponse
    query: Mapping[str, str | int | bool] | None = None


@dataclass(slots=True)
class ScriptedPaperTransport:
    expected: list[PaperCall]
    base_url: str = PAPER_BASE_URL

    def request(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str | int | bool] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> PaperTransportResponse:
        assert body is None
        if not self.expected:
            raise AssertionError(f"unexpected paper request: {method} {path}")
        expected = self.expected.pop(0)
        assert method == expected.method
        assert path == expected.path
        assert query == expected.query
        return expected.response


@dataclass(slots=True)
class MarketCall:
    path: str
    response: ControlDataResponse
    query: Mapping[str, str | int | bool] | None = None


@dataclass(slots=True)
class ScriptedMarketTransport:
    expected: list[MarketCall]
    base_url: str = MARKET_DATA_BASE_URL
    calls: list[tuple[str, Mapping[str, str | int | bool] | None]] = field(default_factory=list)

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str | int | bool] | None = None,
    ) -> ControlDataResponse:
        self.calls.append((path, query))
        if not self.expected:
            raise AssertionError(f"unexpected market-data request: {path}")
        expected = self.expected.pop(0)
        assert path == expected.path
        assert query == expected.query
        return expected.response


def adapter(
    paper: ScriptedPaperTransport,
    market: ScriptedMarketTransport,
    *,
    feed: str = "iex",
) -> AlpacaPaperControlData:
    return AlpacaPaperControlData(
        trading_transport=paper,
        market_data_transport=market,
        quote_feed=feed,
        now=lambda: NOW,
    )


def test_asset_calendar_and_latest_quote_are_normalized_from_fixed_origins() -> None:
    asset_payload = {
        "id": "asset-id",
        "class": "us_equity",
        "exchange": "ARCA",
        "symbol": "SPY",
        "name": "SPDR S&P 500 ETF Trust",
        "status": "active",
        "tradable": True,
        "marginable": True,
        "shortable": True,
        "fractionable": True,
        "borrow_status": "easy_to_borrow",
    }
    calendar_payload = [
        {
            "date": "2026-08-12",
            "open": "09:30",
            "close": "13:00",
            "session_open": "0400",
            "session_close": "2000",
            "settlement_date": "2026-08-13",
        }
    ]
    quote_payload = {
        "symbol": "SPY",
        "quote": {
            "t": "2026-08-12T14:29:59.123456789Z",
            "bp": 500.01,
            "bs": 10,
            "ap": 500.03,
            "as": 12,
            "bx": "N",
            "ax": "P",
            "c": ["R"],
            "z": "C",
        },
    }
    paper = ScriptedPaperTransport(
        expected=[
            PaperCall("GET", "/v2/assets/SPY", paper_response(asset_payload)),
            PaperCall(
                "GET",
                "/v2/calendar",
                paper_response(calendar_payload),
                query={"start": "2026-08-12", "end": "2026-08-12"},
            ),
        ]
    )
    market = ScriptedMarketTransport(
        expected=[
            MarketCall(
                "/v2/stocks/SPY/quotes/latest",
                data_response(quote_payload),
                query={"feed": "iex"},
            )
        ]
    )
    controls = adapter(paper, market)

    normalized_asset = controls.get_asset("spy")
    assert normalized_asset.permits_long_equity_order is True
    assert normalized_asset.captured_at == NOW
    calendar = controls.get_calendar_day(date(2026, 8, 12))
    assert calendar is not None
    assert calendar.regular_close.hour == 13
    assert calendar.regular_close.utcoffset() is not None
    latest = controls.get_latest_quote("SPY")
    assert str(latest.bid_price) == "500.01"
    assert str(latest.ask_price) == "500.03"
    assert latest.timestamp.microsecond == 123456
    assert paper.expected == []
    assert market.expected == []


def test_calendar_holiday_returns_none_and_bad_identity_fails_closed() -> None:
    paper = ScriptedPaperTransport(
        expected=[
            PaperCall(
                "GET",
                "/v2/calendar",
                paper_response([]),
                query={"start": "2026-12-25", "end": "2026-12-25"},
            ),
            PaperCall(
                "GET",
                "/v2/assets/SPY",
                paper_response(
                    {
                        "class": "us_equity",
                        "symbol": "QQQ",
                        "status": "active",
                        "tradable": True,
                        "fractionable": True,
                        "marginable": True,
                        "shortable": True,
                    }
                ),
            ),
        ]
    )
    controls = adapter(paper, ScriptedMarketTransport(expected=[]))
    assert controls.get_calendar_day(date(2026, 12, 25)) is None
    with pytest.raises(AlpacaControlDataError, match="does not match"):
        controls.get_asset("SPY")


def test_control_data_rejects_wrong_origins_feed_and_redacts_errors() -> None:
    with pytest.raises(AlpacaControlDataError, match="paper origin"):
        adapter(
            ScriptedPaperTransport(expected=[], base_url="https://api.alpaca.markets"),
            ScriptedMarketTransport(expected=[]),
        )
    with pytest.raises(AlpacaControlDataError, match="production data origin"):
        adapter(
            ScriptedPaperTransport(expected=[]),
            ScriptedMarketTransport(
                expected=[],
                base_url="https://data.sandbox.alpaca.markets",
            ),
        )
    with pytest.raises(AlpacaControlDataError, match="iex or sip"):
        adapter(
            ScriptedPaperTransport(expected=[]),
            ScriptedMarketTransport(expected=[]),
            feed="delayed_sip",
        )

    sensitive = "private-broker-message"
    controls = adapter(
        ScriptedPaperTransport(
            expected=[
                PaperCall(
                    "GET",
                    "/v2/assets/SPY",
                    paper_response({"message": sensitive}, status=403),
                )
            ]
        ),
        ScriptedMarketTransport(expected=[]),
    )
    with pytest.raises(AlpacaControlDataError) as captured:
        controls.get_asset("SPY")
    assert "403" in str(captured.value)
    assert sensitive not in str(captured.value)


def test_malformed_calendar_and_quote_payloads_are_rejected() -> None:
    paper = ScriptedPaperTransport(
        expected=[
            PaperCall(
                "GET",
                "/v2/calendar",
                paper_response(
                    [
                        {
                            "date": "2026-08-12",
                            "open": "bad",
                            "close": "16:00",
                            "session_open": "0400",
                            "session_close": "2000",
                        }
                    ]
                ),
                query={"start": "2026-08-12", "end": "2026-08-12"},
            )
        ]
    )
    controls = adapter(paper, ScriptedMarketTransport(expected=[]))
    with pytest.raises(AlpacaControlDataError, match="HH:MM"):
        controls.get_calendar_day(date(2026, 8, 12))

    market = ScriptedMarketTransport(
        expected=[
            MarketCall(
                "/v2/stocks/SPY/quotes/latest",
                data_response(
                    {
                        "symbol": "SPY",
                        "quote": {
                            "t": "2026-08-12T14:30:00Z",
                            "bp": 501,
                            "bs": 1,
                            "ap": 500,
                            "as": 1,
                        },
                    }
                ),
                query={"feed": "iex"},
            )
        ]
    )
    controls = adapter(ScriptedPaperTransport(expected=[]), market)
    with pytest.raises(ValueError, match="crossed"):
        controls.get_latest_quote("SPY")


def test_market_data_http_transport_pins_host_encodes_query_and_hashes_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_response = b'{"symbol":"SPY","quote":{}}'
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
            headers: Mapping[str, str],
        ) -> None:
            observed["method"] = method
            observed["target"] = target
            observed["headers"] = dict(headers)

        @staticmethod
        def getresponse() -> FakeResponse:
            return FakeResponse()

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(
        "quantum_trader.adapters.alpaca_control_data.http.client.HTTPSConnection",
        FakeConnection,
    )
    transport = AlpacaMarketDataHttpTransport(
        credentials=credentials(),
        timeout_seconds=3,
    )
    result = transport.get(
        path="/v2/stocks/SPY/quotes/latest",
        query={"feed": "iex", "test": False},
    )
    assert observed["host"] == "data.alpaca.markets"
    assert observed["target"] == "/v2/stocks/SPY/quotes/latest?feed=iex&test=false"
    headers = observed["headers"]
    assert isinstance(headers, dict)
    assert headers["APCA-API-KEY-ID"] == "fixture-key"
    assert headers["APCA-API-SECRET-KEY"] == "fixture-secret"
    assert result.raw_payload_sha256 == hashlib.sha256(raw_response).hexdigest()

    with pytest.raises(AlpacaControlDataError, match="outside"):
        transport.get(path="https://evil.invalid/v2/stocks/SPY")
    with pytest.raises(AlpacaControlDataError, match="timeout_seconds"):
        AlpacaMarketDataHttpTransport(credentials=credentials(), timeout_seconds=0)


def test_market_data_http_transport_rejects_invalid_json_and_timeouts(
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
            headers: Mapping[str, str],
        ) -> None:
            del method, target, headers

        @staticmethod
        def getresponse() -> InvalidJsonResponse:
            return InvalidJsonResponse()

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(
        "quantum_trader.adapters.alpaca_control_data.http.client.HTTPSConnection",
        InvalidJsonConnection,
    )
    transport = AlpacaMarketDataHttpTransport(credentials=credentials())
    with pytest.raises(AlpacaControlDataError, match="valid JSON"):
        transport.get(path="/v2/stocks/SPY/quotes/latest")

    class TimeoutConnection(InvalidJsonConnection):
        @staticmethod
        def request(
            method: str,
            target: str,
            *,
            headers: Mapping[str, str],
        ) -> None:
            del method, target, headers
            raise TimeoutError

    monkeypatch.setattr(
        "quantum_trader.adapters.alpaca_control_data.http.client.HTTPSConnection",
        TimeoutConnection,
    )
    with pytest.raises(AlpacaControlDataError, match="failed or timed out"):
        transport.get(path="/v2/stocks/SPY/quotes/latest")


def test_control_data_response_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="status"):
        ControlDataResponse(status_code=99, payload=None, raw_payload_sha256=RAW)
    with pytest.raises(ValueError, match="SHA-256"):
        ControlDataResponse(status_code=200, payload=None, raw_payload_sha256="bad")
