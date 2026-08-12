#!/usr/bin/env python3
"""Fetch the preregistered Yahoo daily panel for local research evaluation.

Users are responsible for complying with the provider's terms. The repository
publishes checksums and evaluation ledgers, not the provider's market-data rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_symbol(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    output_dir: Path,
) -> dict[str, Any]:
    query = urlencode(
        {
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": "1d",
            "includeAdjustedClose": "true",
            "events": "div,splits",
        }
    )
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
    request = Request(url, headers={"User-Agent": "QuantumTraderPro-Research/1.0"})
    with urlopen(request, timeout=60) as handle:  # noqa: S310 - fixed HTTPS provider
        raw_bytes = handle.read()
    response: dict[str, Any] = json.loads(raw_bytes)
    raw_path = output_dir / f"{symbol.lower()}_yahoo_raw.json"
    raw_path.write_bytes(raw_bytes)

    chart = response.get("chart", {})
    errors = chart.get("error")
    results = chart.get("result")
    if errors or not results:
        raise RuntimeError(f"{symbol}: market-data endpoint returned no result: {errors}")
    result = results[0]
    timestamps = result.get("timestamp", [])
    quote = result.get("indicators", {}).get("quote", [{}])[0]
    adjusted = result.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose", [])
    columns = {name: quote.get(name, []) for name in ("open", "high", "low", "close", "volume")}
    if not timestamps or any(len(values) != len(timestamps) for values in columns.values()):
        raise RuntimeError(f"{symbol}: inconsistent OHLCV arrays")
    if len(adjusted) != len(timestamps):
        raise RuntimeError(f"{symbol}: inconsistent adjusted-close array")

    csv_path = output_dir / f"{symbol.lower()}_daily.csv"
    retained: list[datetime] = []
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["datetime", "open", "high", "low", "close", "adjusted_close", "volume"])
        for index, raw_timestamp in enumerate(timestamps):
            instant = datetime.fromtimestamp(int(raw_timestamp), tz=UTC)
            if instant < start or instant > end:
                continue
            values = [
                columns["open"][index],
                columns["high"][index],
                columns["low"][index],
                columns["close"][index],
                adjusted[index],
                columns["volume"][index],
            ]
            if any(value is None for value in values):
                continue
            writer.writerow([instant.isoformat(), *values])
            retained.append(instant)
    if not retained:
        raise RuntimeError(f"{symbol}: no valid observations remained after date filtering")
    return {
        "symbol": symbol,
        "provider": "Yahoo Finance public chart endpoint",
        "requested_period1": int(start.timestamp()),
        "requested_period2": int(end.timestamp()),
        "interval": "1d",
        "observations": len(retained),
        "start": retained[0].isoformat(),
        "end": retained[-1].isoformat(),
        "csv_sha256": digest(csv_path),
        "raw_sha256": digest(raw_path),
        "normalized_csv": csv_path.name,
        "raw_response": raw_path.name,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    protocol_path = arguments.protocol.expanduser().resolve()
    output_dir = arguments.output.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError("output directory must be empty")

    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    start = datetime.fromisoformat(protocol["data"]["start_date"] + "T00:00:00+00:00")
    end = datetime.fromisoformat(protocol["data"]["end_date"] + "T23:59:59+00:00")
    assets = [
        fetch_symbol(
            symbol=item["symbol"],
            start=start,
            end=end,
            output_dir=output_dir,
        )
        for item in protocol["data"]["assets"]
    ]
    manifest = {
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": digest(protocol_path),
        "retrieved_at": datetime.now(UTC).isoformat(),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "assets": assets,
    }
    (output_dir / "source_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
