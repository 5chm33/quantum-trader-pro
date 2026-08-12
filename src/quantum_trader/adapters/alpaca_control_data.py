"""Read-only Alpaca asset, calendar, and quote adapters for pre-trade controls."""

from __future__ import annotations

import hashlib
import http.client
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quantum_trader.adapters.alpaca_paper import (
    PAPER_BASE_URL,
    AlpacaPaperCredentials,
    AlpacaPaperTransport,
)
from quantum_trader.domain.market_controls import (
    AssetTradingSnapshot,
    LatestQuoteSnapshot,
    MarketCalendarDay,
)
from quantum_trader.domain.rate_limits import (
    RequestBudgetExceeded,
    SlidingWindowRequestBudget,
)

MARKET_DATA_BASE_URL = "https://data.alpaca.markets"
MARKET_DATA_HOST = "data.alpaca.markets"
_ALLOWED_QUOTE_FEEDS = frozenset({"iex", "sip"})


class AlpacaControlDataError(RuntimeError):
    """Control-data error that never contains credential or response-body values."""


@dataclass(frozen=True, slots=True)
class ControlDataResponse:
    status_code: int
    payload: object | None
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be a valid HTTP status")
        _sha256(self.raw_payload_sha256, "raw_payload_sha256")


class AlpacaMarketDataTransport(Protocol):
    @property
    def base_url(self) -> str:
        """Return the exact configured market-data origin."""

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str | int | bool] | None = None,
    ) -> ControlDataResponse:
        """Perform one read-only market-data request."""


class AlpacaMarketDataHttpTransport:
    """Standard-library HTTPS transport pinned to Alpaca market data."""

    def __init__(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        timeout_seconds: float = 5.0,
        request_budget: SlidingWindowRequestBudget | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0 < timeout_seconds <= 30:
            raise AlpacaControlDataError("timeout_seconds must be in (0, 30]")
        self._credentials = credentials
        self._timeout_seconds = timeout_seconds
        self._request_budget = request_budget or SlidingWindowRequestBudget()
        self._now = now or _system_now

    @property
    def base_url(self) -> str:
        return MARKET_DATA_BASE_URL

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str | int | bool] | None = None,
    ) -> ControlDataResponse:
        if not path.startswith("/v2/stocks/") or ".." in path or "?" in path or "://" in path:
            raise AlpacaControlDataError("market-data path is outside the allowed stock API root")
        target = path
        if query:
            normalized_query = {
                key: (str(value).lower() if isinstance(value, bool) else value)
                for key, value in query.items()
            }
            target = f"{path}?{urlencode(normalized_query)}"
        try:
            self._request_budget.acquire(self._now())
        except (RequestBudgetExceeded, ValueError) as exc:
            raise AlpacaControlDataError(
                "local market-data request budget denied the call"
            ) from exc
        headers = {
            "APCA-API-KEY-ID": self._credentials.key_id,
            "APCA-API-SECRET-KEY": self._credentials.secret_key,
            "Accept": "application/json",
            "User-Agent": "quantum-trader-pro-control-data/0.1",
        }
        connection = http.client.HTTPSConnection(
            MARKET_DATA_HOST,
            timeout=self._timeout_seconds,
        )
        try:
            connection.request("GET", target, headers=headers)
            response = connection.getresponse()
            raw_payload = response.read()
        except (TimeoutError, http.client.RemoteDisconnected, OSError) as exc:
            raise AlpacaControlDataError("market-data request failed or timed out") from exc
        finally:
            connection.close()
        payload: object | None = None
        if raw_payload:
            try:
                payload = json.loads(raw_payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AlpacaControlDataError("market-data response was not valid JSON") from exc
        return ControlDataResponse(
            status_code=response.status,
            payload=payload,
            raw_payload_sha256=hashlib.sha256(raw_payload).hexdigest(),
        )


class AlpacaPaperControlData:
    """Compose paper Trading API reads with production real-time market data reads."""

    def __init__(
        self,
        *,
        trading_transport: AlpacaPaperTransport,
        market_data_transport: AlpacaMarketDataTransport,
        quote_feed: str = "iex",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if trading_transport.base_url != PAPER_BASE_URL:
            raise AlpacaControlDataError("trading transport must use the paper origin")
        if market_data_transport.base_url != MARKET_DATA_BASE_URL:
            raise AlpacaControlDataError(
                "market-data transport must use the production data origin"
            )
        if quote_feed not in _ALLOWED_QUOTE_FEEDS:
            raise AlpacaControlDataError("quote_feed must be real-time iex or sip")
        try:
            eastern = ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError as exc:
            raise AlpacaControlDataError(
                "America/New_York timezone data is required for paper controls"
            ) from exc
        self._trading_transport = trading_transport
        self._market_data_transport = market_data_transport
        self._quote_feed = quote_feed
        self._now = now or _system_now
        self._eastern = eastern

    def get_asset(self, symbol: str) -> AssetTradingSnapshot:
        normalized_symbol = _symbol(symbol)
        response = self._trading_transport.request(
            method="GET",
            path=f"/v2/assets/{quote(normalized_symbol, safe='')}",
        )
        _require_status(response.status_code, 200, "asset")
        payload = _mapping(response.payload, "asset")
        response_symbol = _required_string(payload, "symbol").upper()
        if response_symbol != normalized_symbol:
            raise AlpacaControlDataError("asset response symbol does not match request")
        captured_at = self._timestamp()
        return AssetTradingSnapshot(
            symbol=response_symbol,
            asset_class=_required_string(payload, "class"),
            status=_required_string(payload, "status").lower(),
            tradable=_required_bool(payload, "tradable"),
            fractionable=_required_bool(payload, "fractionable"),
            marginable=_required_bool(payload, "marginable"),
            shortable=_required_bool(payload, "shortable"),
            borrow_status=_optional_string(payload, "borrow_status"),
            captured_at=captured_at,
            raw_payload_sha256=_payload_hash(payload),
        )

    def get_calendar_day(self, trade_date: date) -> MarketCalendarDay | None:
        date_text = trade_date.isoformat()
        response = self._trading_transport.request(
            method="GET",
            path="/v2/calendar",
            query={"start": date_text, "end": date_text},
        )
        _require_status(response.status_code, 200, "calendar")
        values = _list(response.payload, "calendar")
        if not values:
            return None
        if len(values) != 1:
            raise AlpacaControlDataError("bounded calendar response must contain one row")
        payload = _mapping(values[0], "calendar day")
        returned_date = _date(_required_string(payload, "date"))
        if returned_date != trade_date:
            raise AlpacaControlDataError("calendar response date does not match request")
        regular_open = _local_datetime(
            returned_date,
            _required_string(payload, "open"),
            self._eastern,
        )
        regular_close = _local_datetime(
            returned_date,
            _required_string(payload, "close"),
            self._eastern,
        )
        session_open = _local_datetime(
            returned_date,
            _required_string(payload, "session_open"),
            self._eastern,
        )
        session_close = _local_datetime(
            returned_date,
            _required_string(payload, "session_close"),
            self._eastern,
        )
        return MarketCalendarDay(
            trade_date=returned_date,
            regular_open=regular_open,
            regular_close=regular_close,
            session_open=session_open,
            session_close=session_close,
            raw_payload_sha256=_payload_hash(payload),
        )

    def get_latest_quote(self, symbol: str) -> LatestQuoteSnapshot:
        normalized_symbol = _symbol(symbol)
        response = self._market_data_transport.get(
            path=f"/v2/stocks/{quote(normalized_symbol, safe='')}/quotes/latest",
            query={"feed": self._quote_feed},
        )
        _require_status(response.status_code, 200, "latest quote")
        payload = _mapping(response.payload, "latest quote")
        response_symbol = _required_string(payload, "symbol").upper()
        if response_symbol != normalized_symbol:
            raise AlpacaControlDataError("quote response symbol does not match request")
        quote_payload = _mapping(payload.get("quote"), "quote")
        return LatestQuoteSnapshot(
            symbol=response_symbol,
            bid_price=_required_decimal(quote_payload, "bp"),
            bid_size=_required_int(quote_payload, "bs"),
            ask_price=_required_decimal(quote_payload, "ap"),
            ask_size=_required_int(quote_payload, "as"),
            timestamp=_datetime(_required_string(quote_payload, "t")),
            feed=self._quote_feed,
            raw_payload_sha256=_payload_hash(quote_payload),
        )

    def _timestamp(self) -> datetime:
        timestamp = self._now()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise AlpacaControlDataError("control-data clock must be timezone-aware")
        return timestamp


def _system_now() -> datetime:
    return datetime.now(UTC)


def _require_status(status_code: int, expected: int, label: str) -> None:
    if status_code != expected:
        raise AlpacaControlDataError(
            f"{label} endpoint returned HTTP {status_code}; response body redacted"
        )


def _symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized or not normalized.replace(".", "").replace("-", "").isalnum():
        raise AlpacaControlDataError("symbol has invalid characters")
    return normalized


def _mapping(value: object | None, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AlpacaControlDataError(f"{label} payload must be an object")
    return cast(Mapping[str, object], value)


def _list(value: object | None, label: str) -> list[object]:
    if not isinstance(value, list):
        raise AlpacaControlDataError(f"{label} payload must be an array")
    return cast(list[object], value)


def _required_string(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise AlpacaControlDataError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(payload: Mapping[str, object], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AlpacaControlDataError(f"{field_name} must be a non-empty string when present")
    return value


def _required_bool(payload: Mapping[str, object], field_name: str) -> bool:
    value = payload.get(field_name)
    if not isinstance(value, bool):
        raise AlpacaControlDataError(f"{field_name} must be boolean")
    return value


def _required_decimal(payload: Mapping[str, object], field_name: str) -> Decimal:
    value = payload.get(field_name)
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise AlpacaControlDataError(f"{field_name} must be decimal-compatible")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise AlpacaControlDataError(f"{field_name} is not a decimal") from exc
    if not result.is_finite():
        raise AlpacaControlDataError(f"{field_name} must be finite")
    return result


def _required_int(payload: Mapping[str, object], field_name: str) -> int:
    value = payload.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise AlpacaControlDataError(f"{field_name} must be an integer")
    return value


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AlpacaControlDataError("calendar date is invalid") from exc


def _datetime(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AlpacaControlDataError("quote timestamp is invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise AlpacaControlDataError("quote timestamp must be timezone-aware")
    return result


def _local_datetime(trade_date: date, value: str, timezone: ZoneInfo) -> datetime:
    compact = value.replace(":", "")
    if len(compact) != 4 or not compact.isdigit():
        raise AlpacaControlDataError("calendar time must be HH:MM or HHMM")
    hour = int(compact[:2])
    minute = int(compact[2:])
    try:
        parsed_time = time(hour, minute)
    except ValueError as exc:
        raise AlpacaControlDataError("calendar time is invalid") from exc
    return datetime.combine(trade_date, parsed_time, tzinfo=timezone)


def _payload_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
