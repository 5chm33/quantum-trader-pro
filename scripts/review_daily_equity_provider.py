"""Print a fail-closed provider-admission receipt from an inspected-field JSON document.

This command deliberately has no provider client, market-data fetch, snapshot writer, experiment
ledger, candidate registration, lockbox, broker, paper, or live-execution dependency.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from quantum_trader.domain.provider_admission import (
    CoverageReceipt,
    DailyEquityProviderInspection,
    ProviderFieldReceipt,
    review_daily_equity_provider,
)


class ProviderAdmissionCliError(ValueError):
    """Raised when an admission inspection document cannot be decoded safely."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspection", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    inspection = _decode_inspection(_load_json(arguments.inspection.expanduser().resolve()))
    receipt = review_daily_equity_provider(inspection)
    rendered = receipt.canonical_json()
    if arguments.output is not None:
        output = arguments.output.expanduser().resolve()
        if output.exists():
            raise ProviderAdmissionCliError("output receipt must not already exist")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        output.chmod(0o600)
    print(rendered, end="")
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ProviderAdmissionCliError(f"inspection cannot be read: {error}") from error
    except json.JSONDecodeError as error:
        raise ProviderAdmissionCliError(f"inspection is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ProviderAdmissionCliError("inspection JSON must be an object")
    return value


def _decode_inspection(value: dict[str, Any]) -> DailyEquityProviderInspection:
    fields = value.get("fields")
    coverage = value.get("coverage")
    if not isinstance(fields, list) or not isinstance(coverage, list):
        raise ProviderAdmissionCliError("inspection fields and coverage must be arrays")
    try:
        return DailyEquityProviderInspection(
            inspection_id=_text(value, "inspection_id"),
            inspected_at=_time(value, "inspected_at"),
            provider=_text(value, "provider"),
            dataset=_text(value, "dataset"),
            query_sha256=_text(value, "query_sha256"),
            provider_schema_version=_text(value, "provider_schema_version"),
            adjustment_convention=_optional_text(value, "adjustment_convention"),
            fixed_universe_rule=_optional_text(value, "fixed_universe_rule"),
            missing_bar_policy=_optional_text(value, "missing_bar_policy"),
            retention_and_rerun_permitted=_bool(value, "retention_and_rerun_permitted"),
            cost_model_requires_quote_or_proxy=_bool(
                value,
                "cost_model_requires_quote_or_proxy",
            ),
            lockbox_query_executed=_bool(value, "lockbox_query_executed"),
            candidate_registered=_bool(value, "candidate_registered"),
            snapshot_created=_bool(value, "snapshot_created"),
            fields=tuple(_decode_field(item) for item in fields),
            coverage=tuple(_decode_coverage(item) for item in coverage),
        )
    except (TypeError, ValueError) as error:
        raise ProviderAdmissionCliError(f"inspection is invalid: {error}") from error


def _decode_field(value: object) -> ProviderFieldReceipt:
    if not isinstance(value, dict):
        raise ProviderAdmissionCliError("each field receipt must be an object")
    return ProviderFieldReceipt(
        component=_text(value, "component"),
        field_name=_text(value, "field_name"),
        source_path=_text(value, "source_path"),
        time_semantics=_text(value, "time_semantics"),
        present=_bool(value, "present"),
    )


def _decode_coverage(value: object) -> CoverageReceipt:
    if not isinstance(value, dict):
        raise ProviderAdmissionCliError("each coverage receipt must be an object")
    row_count = value.get("observed_row_count")
    missing_count = value.get("missing_row_count")
    if not isinstance(row_count, int) or isinstance(row_count, bool):
        raise ProviderAdmissionCliError("observed_row_count must be an integer")
    if not isinstance(missing_count, int) or isinstance(missing_count, bool):
        raise ProviderAdmissionCliError("missing_row_count must be an integer")
    return CoverageReceipt(
        component=_text(value, "component"),
        coverage_start_at=_time(value, "coverage_start_at"),
        coverage_end_at=_time(value, "coverage_end_at"),
        observed_row_count=row_count,
        missing_row_count=missing_count,
        correction_policy=_text(value, "correction_policy"),
    )


def _text(value: dict[str, Any], field_name: str) -> str:
    field = value.get(field_name)
    if not isinstance(field, str):
        raise ProviderAdmissionCliError(f"{field_name} must be a string")
    return field


def _optional_text(value: dict[str, Any], field_name: str) -> str | None:
    field = value.get(field_name)
    if field is None or isinstance(field, str):
        return field
    raise ProviderAdmissionCliError(f"{field_name} must be a string or null")


def _bool(value: dict[str, Any], field_name: str) -> bool:
    field = value.get(field_name)
    if not isinstance(field, bool):
        raise ProviderAdmissionCliError(f"{field_name} must be a boolean")
    return field


def _time(value: dict[str, Any], field_name: str) -> datetime:
    field = _text(value, field_name)
    try:
        return datetime.fromisoformat(field.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProviderAdmissionCliError(f"{field_name} must be ISO 8601") from error


if __name__ == "__main__":
    raise SystemExit(main())
