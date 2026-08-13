"""Fetch a bounded public ETF history for Phase 13 falsification research only.

This script intentionally retrieves only the pre-holdout interval.  It records the new
campaign lockbox query as a SHA-256 value in a manifest but does not issue that request.  The
provider's historical adjusted prices are *not* treated as licensed point-in-time vintage data;
any resulting campaign is ineligible for strategy promotion or holdout opening.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

_ASSETS = ("SPY", "QQQ", "IWM", "EFA", "TLT", "GLD")
_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_START = 1104537600  # 2005-01-01T00:00:00Z
_PRE_HOLDOUT_END = 1546300799  # 2018-12-31T23:59:59Z
_LOCKBOX_START = 1546300800  # 2019-01-01T00:00:00Z; deliberately never fetched.
_LOCKBOX_END = 1767225599  # 2025-12-31T23:59:59Z; deliberately never fetched.


class PublicDataFetchError(RuntimeError):
    """Raised when a bounded public-data response is incomplete or malformed."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    output_dir = arguments.output_dir.expanduser().resolve()
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    retrieved_at = datetime.now(UTC)
    assets: list[dict[str, Any]] = []
    for symbol in _ASSETS:
        response, source_uri = _fetch(symbol=symbol, period1=_START, period2=_PRE_HOLDOUT_END)
        raw_path = raw_dir / f"{symbol.lower()}_chart.json"
        raw_path.write_bytes(response)
        normalized = _normalize(symbol=symbol, response=response)
        normalized_path = output_dir / f"{symbol.lower()}_daily_public_falsification.json"
        normalized_path.write_text(json.dumps(normalized, sort_keys=True) + "\n", encoding="utf-8")
        assets.append(
            {
                "symbol": symbol,
                "source_uri": source_uri,
                "raw_filename": raw_path.name,
                "raw_sha256": _sha256(response),
                "normalized_filename": normalized_path.name,
                "normalized_sha256": _sha256(normalized_path.read_bytes()),
                "observation_count": len(normalized["observations"]),
                "first_event_at": normalized["observations"][0]["event_at"],
                "last_event_at": normalized["observations"][-1]["event_at"],
            }
        )

    lockbox_query = _source_uri(symbol="{symbol}", period1=_LOCKBOX_START, period2=_LOCKBOX_END)
    manifest = {
        "campaign_class": "public_nonvintage_falsification_only",
        "provider": "Yahoo Finance public chart endpoint",
        "retrieved_at": retrieved_at.isoformat(),
        "pre_holdout_request": {
            "period1": _START,
            "period2": _PRE_HOLDOUT_END,
            "interval": "1d",
            "includeAdjustedClose": True,
            "events": "div,splits",
        },
        "new_campaign_lockbox": {
            "query_template_sha256": _sha256(lockbox_query.encode("utf-8")),
            "period1": _LOCKBOX_START,
            "period2": _LOCKBOX_END,
            "bytes_retrieved": False,
            "explicit_user_approval_required": True,
        },
        "limitations": [
            (
                "Historical adjusted prices are retrieved contemporaneously and have no retained "
                "historical-vintage revision history."
            ),
            (
                "This data may support falsification only and cannot satisfy point-in-time data "
                "or strategy-promotion gates."
            ),
            "Raw responses are retained outside the public repository pending licensing review.",
        ],
        "assets": assets,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


def _fetch(*, symbol: str, period1: int, period2: int) -> tuple[bytes, str]:
    source_uri = _source_uri(symbol=symbol, period1=period1, period2=period2)
    target = f"/v8/finance/chart/{symbol}?{_query_string(period1=period1, period2=period2)}"
    connection = http.client.HTTPSConnection("query1.finance.yahoo.com", timeout=20)
    try:
        connection.request(
            "GET",
            target,
            headers={"Accept": "application/json", "User-Agent": "quantum-trader-pro-research/0.2"},
        )
        response = connection.getresponse()
        payload = response.read()
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise PublicDataFetchError(f"public data fetch failed for {symbol}") from exc
    finally:
        connection.close()
    if response.status != 200 or not payload:
        raise PublicDataFetchError(f"public data response was incomplete for {symbol}")
    return payload, source_uri


def _source_uri(*, symbol: str, period1: int, period2: int) -> str:
    return f"{_ENDPOINT.format(symbol=symbol)}?{_query_string(period1=period1, period2=period2)}"


def _query_string(*, period1: int, period2: int) -> str:
    return urlencode(
        {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "includeAdjustedClose": "true",
            "events": "div,splits",
        }
    )


def _normalize(*, symbol: str, response: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(response)
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        adjusted = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise PublicDataFetchError(f"public data payload was malformed for {symbol}") from exc
    observations: list[dict[str, object]] = []
    for timestamp, close, adjusted_close, volume in zip(
        timestamps, quote["close"], adjusted, quote["volume"], strict=True
    ):
        if close is None or adjusted_close is None or volume is None:
            continue
        event_at = datetime.fromtimestamp(int(timestamp), UTC)
        observations.append(
            {
                "event_at": event_at.isoformat(),
                "available_at_assumption": "next_calendar_day_after_provider_daily_bar",
                "close": str(close),
                "adjusted_close": str(adjusted_close),
                "volume": int(volume),
            }
        )
    if len(observations) < 1000:
        raise PublicDataFetchError(f"public data history is insufficient for {symbol}")
    return {"symbol": symbol, "observations": observations}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
