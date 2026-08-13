"""Freeze a bounded daily-equity prototype snapshot without reading its excluded tail.

The resulting snapshot is deliberately classified as provisional and non-promotion.  It is
not point-in-time evidence, cannot unlock a holdout, and cannot authorize paper or live
execution.  Input files must be chronologically ascending; each parser stops before the
protocol's ``excluded_from`` instant instead of scanning later rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_REQUIRED_COLUMNS = (
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
)


class ProvisionalSnapshotError(ValueError):
    """Raised when an input cannot produce a bounded, reproducible prototype snapshot."""


@dataclass(frozen=True, slots=True)
class AssetReceipt:
    """Hash and coverage receipt for exactly the rows admitted before the exclusion boundary."""

    symbol: str
    filename: str
    row_count: int
    first_event_at: str
    last_event_at: str
    source_subset_sha256: str
    normalized_sha256: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    protocol_path = arguments.protocol.expanduser().resolve()
    source_data = arguments.source_data.expanduser().resolve()
    output_dir = arguments.output.expanduser().resolve()
    protocol = _load_protocol(protocol_path)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ProvisionalSnapshotError("output directory must be empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir()

    excluded_from = _parse_timestamp(str(protocol["data"]["excluded_from"]))
    symbols = tuple(str(value) for value in protocol["data"]["universe"])
    if symbols != tuple(sorted(symbols)):
        raise ProvisionalSnapshotError("protocol universe must be canonically sorted")
    receipts = tuple(
        _copy_before_boundary(
            symbol=symbol,
            source_path=source_data / f"{symbol.lower()}_daily.csv",
            destination_path=data_dir / f"{symbol.lower()}_daily.csv",
            excluded_from=excluded_from,
        )
        for symbol in symbols
    )
    manifest = _manifest(
        protocol=protocol,
        protocol_path=protocol_path,
        excluded_from=excluded_from,
        receipts=receipts,
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
    return 0


def _load_protocol(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvisionalSnapshotError("protocol could not be read as JSON") from exc
    if not isinstance(raw, dict):
        raise ProvisionalSnapshotError("protocol must be a JSON object")
    if raw.get("classification") != "provisional_nonpromotion_falsification_only":
        raise ProvisionalSnapshotError("protocol classification is not provisional non-promotion")
    data = raw.get("data")
    if not isinstance(data, dict):
        raise ProvisionalSnapshotError("protocol data section is missing")
    universe = data.get("universe")
    if not isinstance(universe, list) or not universe:
        raise ProvisionalSnapshotError("protocol universe is missing")
    if tuple(str(item) for item in universe) != tuple(sorted(str(item) for item in universe)):
        raise ProvisionalSnapshotError("protocol universe must be canonically sorted")
    if not isinstance(data.get("excluded_from"), str):
        raise ProvisionalSnapshotError("protocol excluded_from is missing")
    return raw


def _copy_before_boundary(
    *,
    symbol: str,
    source_path: Path,
    destination_path: Path,
    excluded_from: datetime,
) -> AssetReceipt:
    if not source_path.is_file():
        raise ProvisionalSnapshotError(f"{symbol}: source CSV is missing")
    hasher = hashlib.sha256()
    retained: list[list[str]] = []
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ProvisionalSnapshotError(f"{symbol}: source CSV is empty") from exc
        if tuple(header) != _REQUIRED_COLUMNS:
            raise ProvisionalSnapshotError(f"{symbol}: source CSV header is invalid")
        _update_line_digest(hasher, header)
        previous: datetime | None = None
        for row in reader:
            if len(row) != len(_REQUIRED_COLUMNS):
                raise ProvisionalSnapshotError(f"{symbol}: source row has an invalid column count")
            timestamp = _parse_timestamp(row[0])
            if previous is not None and timestamp <= previous:
                raise ProvisionalSnapshotError(
                    f"{symbol}: source timestamps are not strictly ascending"
                )
            if timestamp >= excluded_from:
                break
            _validate_row(symbol=symbol, row=row)
            _update_line_digest(hasher, row)
            retained.append(row)
            previous = timestamp
    if not retained:
        raise ProvisionalSnapshotError(f"{symbol}: no rows precede the exclusion boundary")
    with destination_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(_REQUIRED_COLUMNS)
        writer.writerows(retained)
    return AssetReceipt(
        symbol=symbol,
        filename=destination_path.name,
        row_count=len(retained),
        first_event_at=retained[0][0],
        last_event_at=retained[-1][0],
        source_subset_sha256=hasher.hexdigest(),
        normalized_sha256=_digest_file(destination_path),
    )


def _validate_row(*, symbol: str, row: list[str]) -> None:
    for label, value in zip(_REQUIRED_COLUMNS[1:], row[1:], strict=True):
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ProvisionalSnapshotError(f"{symbol}: {label} is not decimal") from exc
        if not number.is_finite() or number < 0:
            raise ProvisionalSnapshotError(f"{symbol}: {label} must be finite and nonnegative")
    prices = tuple(Decimal(value) for value in row[1:6])
    if any(value <= 0 for value in prices):
        raise ProvisionalSnapshotError(f"{symbol}: prices must be positive")
    open_price, high, low, close, _adjusted_close = prices
    if high < max(open_price, close) or low > min(open_price, close) or high < low:
        raise ProvisionalSnapshotError(f"{symbol}: OHLC values are inconsistent")


def _manifest(
    *,
    protocol: dict[str, Any],
    protocol_path: Path,
    excluded_from: datetime,
    receipts: tuple[AssetReceipt, ...],
) -> dict[str, Any]:
    content = {
        "snapshot_id": "qtpro-provisional-daily-equity-snapshot-v1",
        "classification": "provisional_nonpromotion_falsification_only",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": _digest_file(protocol_path),
        "source": {
            "provider": protocol["data"]["source_provider"],
            "source_class": protocol["data"]["source_class"],
            "historical_vintage_availability": protocol["data"]["source_availability_status"],
            "corporate_action_status": protocol["data"]["corporate_action_status"],
            "license_class": "private",
            "redistribution_allowed": False,
        },
        "admission_scope": {
            "included_start": protocol["data"]["included_start"],
            "excluded_from": excluded_from.isoformat(),
            "read_policy": "Parser stops at excluded_from and does not scan later source rows.",
            "not_a_holdout": True,
            "cannot_support": [
                "strategy_grade",
                "candidate_promotion",
                "new_holdout",
                "paper_trading",
                "live_trading",
                "options_validation",
                "historical_execution_or_capacity_claims",
            ],
        },
        "assets": [
            {
                "symbol": receipt.symbol,
                "normalized_filename": receipt.filename,
                "row_count": receipt.row_count,
                "first_event_at": receipt.first_event_at,
                "last_event_at": receipt.last_event_at,
                "source_subset_sha256": receipt.source_subset_sha256,
                "normalized_sha256": receipt.normalized_sha256,
            }
            for receipt in receipts
        ],
    }
    return {**content, "content_sha256": _sha256_json(content)}


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProvisionalSnapshotError("timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProvisionalSnapshotError("timestamp must be timezone-aware")
    return parsed


def _update_line_digest(hasher: Any, row: list[str] | tuple[str, ...]) -> None:
    hasher.update(",".join(row).encode("utf-8"))
    hasher.update(b"\n")


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _sha256_json(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
