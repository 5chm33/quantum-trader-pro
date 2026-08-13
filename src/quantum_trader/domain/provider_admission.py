"""Fail-closed provider-admission contracts for daily-equity research data.

Admission inspects declared provider fields and coverage receipts only. It does not fetch
market data, create a snapshot, register a candidate, access a lockbox, or authorize any
execution path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum


class ProviderAdmissionError(ValueError):
    """Raised when a provider-admission receipt is malformed or unsafe."""


class AdmissionStatus(StrEnum):
    ADMITTED = "admitted"
    REJECTED = "rejected"


class AdmissionFailureCode(StrEnum):
    ADJUSTED_PRICE_MISSING = "adjusted_price_missing"
    ADJUSTMENT_CONVENTION_MISSING = "adjustment_convention_missing"
    AVAILABILITY_TIME_MISSING = "availability_time_missing"
    CALENDAR_FIELD_MISSING = "calendar_field_missing"
    CAMPAIGN_STATE_ATTEMPTED = "campaign_state_attempted"
    CASH_ALIGNMENT_MISSING = "cash_alignment_missing"
    CORPORATE_ACTION_EFFECTIVE_TIME_MISSING = "corporate_action_effective_time_missing"
    CORPORATE_ACTION_RECORDS_MISSING = "corporate_action_records_missing"
    CORPORATE_ACTION_AVAILABLE_TIME_MISSING = "corporate_action_available_time_missing"
    COVERAGE_INVALID = "coverage_invalid"
    FIELD_NOT_PRESENT = "field_not_present"
    LOCKBOX_ACTION_ATTEMPTED = "lockbox_action_attempted"
    MISSING_BAR_POLICY_MISSING = "missing_bar_policy_missing"
    QUOTE_OR_PROXY_MISSING = "quote_or_proxy_missing"
    RETENTION_RIGHT_MISSING = "retention_right_missing"
    UNADJUSTED_PRICE_MISSING = "unadjusted_price_missing"
    UNIVERSE_RULE_MISSING = "universe_rule_missing"
    VOLUME_FIELD_MISSING = "volume_field_missing"


_REQUIRED_PRICE_FIELDS = frozenset(
    {
        "close",
        "adjusted_close",
        "unadjusted_close",
        "event_at",
        "available_at",
        "instrument_id",
        "session_id",
        "volume",
    }
)
_REQUIRED_ACTION_FIELDS = frozenset({"action_type", "effective_at", "available_at"})
_REQUIRED_CASH_FIELDS = frozenset({"available_at", "rate", "rate_date"})


def _identifier(value: str, field_name: str) -> str:
    normalized = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/")
    if not 3 <= len(normalized) <= 200 or any(character not in allowed for character in normalized):
        raise ProviderAdmissionError(f"{field_name} has an invalid identifier")
    return normalized


def _sha256(value: str, field_name: str) -> str:
    normalized = value.strip()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ProviderAdmissionError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProviderAdmissionError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ProviderFieldReceipt:
    """One machine-readable inspected field and its stated time semantics."""

    component: str
    field_name: str
    source_path: str
    time_semantics: str
    present: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "component", _identifier(self.component, "component"))
        object.__setattr__(self, "field_name", _identifier(self.field_name, "field_name"))
        if not self.source_path.strip() or not self.time_semantics.strip():
            raise ProviderAdmissionError(
                "field receipt source_path and time_semantics are required"
            )


@dataclass(frozen=True, slots=True)
class CoverageReceipt:
    """A declared historical coverage interval and missing-record count."""

    component: str
    coverage_start_at: datetime
    coverage_end_at: datetime
    observed_row_count: int
    missing_row_count: int
    correction_policy: str

    def __post_init__(self) -> None:
        start = _utc(self.coverage_start_at, "coverage_start_at")
        end = _utc(self.coverage_end_at, "coverage_end_at")
        if start >= end:
            raise ProviderAdmissionError("coverage_start_at must precede coverage_end_at")
        if self.observed_row_count <= 0 or self.missing_row_count < 0:
            raise ProviderAdmissionError("coverage row counts are invalid")
        if not self.correction_policy.strip():
            raise ProviderAdmissionError("correction_policy is required")
        object.__setattr__(self, "component", _identifier(self.component, "component"))
        object.__setattr__(self, "coverage_start_at", start)
        object.__setattr__(self, "coverage_end_at", end)


@dataclass(frozen=True, slots=True)
class DailyEquityProviderInspection:
    """A read-only field and coverage inspection before any campaign is registered."""

    inspection_id: str
    inspected_at: datetime
    provider: str
    dataset: str
    query_sha256: str
    provider_schema_version: str
    adjustment_convention: str | None
    fixed_universe_rule: str | None
    missing_bar_policy: str | None
    retention_and_rerun_permitted: bool
    cost_model_requires_quote_or_proxy: bool
    lockbox_query_executed: bool
    candidate_registered: bool
    snapshot_created: bool
    fields: tuple[ProviderFieldReceipt, ...]
    coverage: tuple[CoverageReceipt, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "inspection_id", _identifier(self.inspection_id, "inspection_id"))
        object.__setattr__(self, "inspected_at", _utc(self.inspected_at, "inspected_at"))
        object.__setattr__(self, "provider", _identifier(self.provider, "provider"))
        object.__setattr__(self, "dataset", _identifier(self.dataset, "dataset"))
        object.__setattr__(self, "query_sha256", _sha256(self.query_sha256, "query_sha256"))
        if not self.provider_schema_version.strip():
            raise ProviderAdmissionError("provider_schema_version is required")
        if not self.fields or not self.coverage:
            raise ProviderAdmissionError("inspection requires fields and coverage")
        field_keys = tuple((field.component, field.field_name) for field in self.fields)
        if len(field_keys) != len(set(field_keys)):
            raise ProviderAdmissionError("inspection fields must be unique by component and name")
        components = tuple(receipt.component for receipt in self.coverage)
        if len(components) != len(set(components)):
            raise ProviderAdmissionError("coverage components must be unique")


@dataclass(frozen=True, slots=True)
class ProviderAdmissionReceipt:
    """Deterministic, read-only admission decision and data dictionary."""

    inspection_id: str
    status: AdmissionStatus
    inspected_at: datetime
    provider: str
    dataset: str
    query_sha256: str
    failure_codes: tuple[AdmissionFailureCode, ...]
    fields: tuple[ProviderFieldReceipt, ...]
    coverage: tuple[CoverageReceipt, ...]
    adjustment_convention: str | None
    fixed_universe_rule: str | None
    missing_bar_policy: str | None
    retention_and_rerun_permitted: bool
    lockbox_query_executed: bool
    candidate_registered: bool
    snapshot_created: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "inspection_id", _identifier(self.inspection_id, "inspection_id"))
        object.__setattr__(self, "inspected_at", _utc(self.inspected_at, "inspected_at"))
        object.__setattr__(self, "provider", _identifier(self.provider, "provider"))
        object.__setattr__(self, "dataset", _identifier(self.dataset, "dataset"))
        object.__setattr__(self, "query_sha256", _sha256(self.query_sha256, "query_sha256"))
        if tuple(sorted(set(self.failure_codes), key=str)) != self.failure_codes:
            raise ProviderAdmissionError("failure_codes must be unique and canonically ordered")
        if self.status is AdmissionStatus.ADMITTED and self.failure_codes:
            raise ProviderAdmissionError("admitted receipt cannot retain failures")
        if self.status is AdmissionStatus.REJECTED and not self.failure_codes:
            raise ProviderAdmissionError("rejected receipt must retain failure codes")
        if self.status is AdmissionStatus.ADMITTED and (
            self.lockbox_query_executed or self.candidate_registered or self.snapshot_created
        ):
            raise ProviderAdmissionError("admitted receipt cannot record campaign state")

    def canonical_json(self) -> str:
        """Return a stable machine-readable data dictionary and decision receipt."""
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["failure_codes"] = [code.value for code in self.failure_codes]
        payload["inspected_at"] = self.inspected_at.isoformat().replace("+00:00", "Z")
        for record in payload["coverage"]:
            record["coverage_start_at"] = (
                record["coverage_start_at"].isoformat().replace("+00:00", "Z")
            )
            record["coverage_end_at"] = record["coverage_end_at"].isoformat().replace("+00:00", "Z")
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    @property
    def receipt_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def review_daily_equity_provider(
    inspection: DailyEquityProviderInspection,
) -> ProviderAdmissionReceipt:
    """Validate field presence and boundaries without fetching or freezing research data."""
    present = {(field.component, field.field_name) for field in inspection.fields if field.present}
    coverage = {receipt.component: receipt for receipt in inspection.coverage}
    failures: set[AdmissionFailureCode] = set()

    _require_fields("daily_price", _REQUIRED_PRICE_FIELDS, present, failures)
    _require_fields("corporate_action", _REQUIRED_ACTION_FIELDS, present, failures)
    _require_fields("cash_rate", _REQUIRED_CASH_FIELDS, present, failures)
    _require_component_coverage(
        components=("daily_price", "corporate_action", "cash_rate", "universe"),
        coverage=coverage,
        failures=failures,
    )
    if not inspection.adjustment_convention or not inspection.adjustment_convention.strip():
        failures.add(AdmissionFailureCode.ADJUSTMENT_CONVENTION_MISSING)
    if not inspection.fixed_universe_rule or not inspection.fixed_universe_rule.strip():
        failures.add(AdmissionFailureCode.UNIVERSE_RULE_MISSING)
    if not inspection.missing_bar_policy or not inspection.missing_bar_policy.strip():
        failures.add(AdmissionFailureCode.MISSING_BAR_POLICY_MISSING)
    if not inspection.retention_and_rerun_permitted:
        failures.add(AdmissionFailureCode.RETENTION_RIGHT_MISSING)
    if inspection.cost_model_requires_quote_or_proxy and not _has_quote_or_proxy(present):
        failures.add(AdmissionFailureCode.QUOTE_OR_PROXY_MISSING)
    if inspection.lockbox_query_executed:
        failures.add(AdmissionFailureCode.LOCKBOX_ACTION_ATTEMPTED)
    if inspection.candidate_registered or inspection.snapshot_created:
        failures.add(AdmissionFailureCode.CAMPAIGN_STATE_ATTEMPTED)

    ordered_failures = tuple(sorted(failures, key=str))
    return ProviderAdmissionReceipt(
        inspection_id=inspection.inspection_id,
        status=AdmissionStatus.ADMITTED if not ordered_failures else AdmissionStatus.REJECTED,
        inspected_at=inspection.inspected_at,
        provider=inspection.provider,
        dataset=inspection.dataset,
        query_sha256=inspection.query_sha256,
        failure_codes=ordered_failures,
        fields=tuple(
            sorted(inspection.fields, key=lambda field: (field.component, field.field_name))
        ),
        coverage=tuple(sorted(inspection.coverage, key=lambda receipt: receipt.component)),
        adjustment_convention=inspection.adjustment_convention,
        fixed_universe_rule=inspection.fixed_universe_rule,
        missing_bar_policy=inspection.missing_bar_policy,
        retention_and_rerun_permitted=inspection.retention_and_rerun_permitted,
        lockbox_query_executed=inspection.lockbox_query_executed,
        candidate_registered=inspection.candidate_registered,
        snapshot_created=inspection.snapshot_created,
    )


def _require_fields(
    component: str,
    required: frozenset[str],
    present: set[tuple[str, str]],
    failures: set[AdmissionFailureCode],
) -> None:
    missing = required - {field_name for candidate, field_name in present if candidate == component}
    if not missing:
        return
    if component == "daily_price":
        if "adjusted_close" in missing:
            failures.add(AdmissionFailureCode.ADJUSTED_PRICE_MISSING)
        if "unadjusted_close" in missing:
            failures.add(AdmissionFailureCode.UNADJUSTED_PRICE_MISSING)
        if "volume" in missing:
            failures.add(AdmissionFailureCode.VOLUME_FIELD_MISSING)
        if "available_at" in missing:
            failures.add(AdmissionFailureCode.AVAILABILITY_TIME_MISSING)
        if "session_id" in missing:
            failures.add(AdmissionFailureCode.CALENDAR_FIELD_MISSING)
    elif component == "corporate_action":
        if "effective_at" in missing:
            failures.add(AdmissionFailureCode.CORPORATE_ACTION_EFFECTIVE_TIME_MISSING)
        if "available_at" in missing:
            failures.add(AdmissionFailureCode.CORPORATE_ACTION_AVAILABLE_TIME_MISSING)
        if "action_type" in missing:
            failures.add(AdmissionFailureCode.CORPORATE_ACTION_RECORDS_MISSING)
    elif component == "cash_rate":
        failures.add(AdmissionFailureCode.CASH_ALIGNMENT_MISSING)
    failures.add(AdmissionFailureCode.FIELD_NOT_PRESENT)


def _require_component_coverage(
    *,
    components: tuple[str, ...],
    coverage: dict[str, CoverageReceipt],
    failures: set[AdmissionFailureCode],
) -> None:
    if any(component not in coverage for component in components):
        failures.add(AdmissionFailureCode.COVERAGE_INVALID)


def _has_quote_or_proxy(present: set[tuple[str, str]]) -> bool:
    allowed = {"bid", "ask", "quoted_spread", "liquidity_proxy", "participation_capacity"}
    return bool(
        allowed & {field_name for component, field_name in present if component == "daily_price"}
    )
