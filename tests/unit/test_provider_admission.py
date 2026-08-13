from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from quantum_trader.domain.provider_admission import (
    AdmissionFailureCode,
    AdmissionStatus,
    CoverageReceipt,
    DailyEquityProviderInspection,
    ProviderAdmissionError,
    ProviderAdmissionReceipt,
    ProviderFieldReceipt,
    review_daily_equity_provider,
)

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_START = datetime(2010, 1, 1, tzinfo=UTC)
_END = datetime(2018, 12, 31, 23, 59, tzinfo=UTC)
_DIGEST = "a" * 64


def _field(component: str, field_name: str, *, present: bool = True) -> ProviderFieldReceipt:
    return ProviderFieldReceipt(
        component=component,
        field_name=field_name,
        source_path=f"{component}.{field_name}",
        time_semantics="point_in_time_available",
        present=present,
    )


def _coverage(component: str, *, missing_rows: int = 0) -> CoverageReceipt:
    return CoverageReceipt(
        component=component,
        coverage_start_at=_START,
        coverage_end_at=_END,
        observed_row_count=2_000,
        missing_row_count=missing_rows,
        correction_policy="retain_final_and_correction_status",
    )


def _inspection(**overrides: object) -> DailyEquityProviderInspection:
    daily = (
        "close",
        "adjusted_close",
        "unadjusted_close",
        "event_at",
        "available_at",
        "instrument_id",
        "session_id",
        "volume",
        "liquidity_proxy",
    )
    actions = ("action_type", "effective_at", "available_at")
    cash = ("available_at", "rate", "rate_date")
    values: dict[str, object] = {
        "inspection_id": "daily-etf-provider-admission-v1",
        "inspected_at": _NOW,
        "provider": "fixture_provider",
        "dataset": "daily_etf_research",
        "query_sha256": _DIGEST,
        "provider_schema_version": "v2026.08",
        "adjustment_convention": "vendor_total_return_adjusted_close_with_unadjusted_close",
        "fixed_universe_rule": "Six named ETFs frozen before campaign registration.",
        "missing_bar_policy": "Do not trade an asset with a missing regular-session daily bar.",
        "retention_and_rerun_permitted": True,
        "cost_model_requires_quote_or_proxy": True,
        "lockbox_query_executed": False,
        "candidate_registered": False,
        "snapshot_created": False,
        "fields": tuple(
            _field(component, name)
            for component, names in (
                ("daily_price", daily),
                ("corporate_action", actions),
                ("cash_rate", cash),
            )
            for name in names
        ),
        "coverage": tuple(
            _coverage(component)
            for component in ("daily_price", "corporate_action", "cash_rate", "universe")
        ),
    }
    values.update(overrides)
    return DailyEquityProviderInspection(**values)  # type: ignore[arg-type]


def test_complete_provider_inspection_is_admitted_and_canonical() -> None:
    receipt = review_daily_equity_provider(_inspection())

    assert receipt.status is AdmissionStatus.ADMITTED
    assert receipt.failure_codes == ()
    assert receipt.lockbox_query_executed is False
    assert receipt.candidate_registered is False
    assert receipt.snapshot_created is False
    assert receipt.receipt_sha256 == review_daily_equity_provider(_inspection()).receipt_sha256
    serialized = json.loads(receipt.canonical_json())
    assert serialized["fields"][0]["component"] == "cash_rate"
    assert serialized["coverage"][0]["component"] == "cash_rate"
    assert serialized["missing_bar_policy"].startswith("Do not trade")


@pytest.mark.parametrize(
    ("field_name", "failure_code"),
    (
        ("adjusted_close", AdmissionFailureCode.ADJUSTED_PRICE_MISSING),
        ("unadjusted_close", AdmissionFailureCode.UNADJUSTED_PRICE_MISSING),
        ("available_at", AdmissionFailureCode.AVAILABILITY_TIME_MISSING),
        ("session_id", AdmissionFailureCode.CALENDAR_FIELD_MISSING),
        ("volume", AdmissionFailureCode.VOLUME_FIELD_MISSING),
    ),
)
def test_daily_price_required_fields_fail_closed(
    field_name: str,
    failure_code: AdmissionFailureCode,
) -> None:
    inspection = _inspection(
        fields=tuple(
            field
            for field in _inspection().fields
            if not (field.component == "daily_price" and field.field_name == field_name)
        )
    )

    receipt = review_daily_equity_provider(inspection)

    assert receipt.status is AdmissionStatus.REJECTED
    assert failure_code in receipt.failure_codes
    assert AdmissionFailureCode.FIELD_NOT_PRESENT in receipt.failure_codes


@pytest.mark.parametrize(
    ("field_name", "failure_code"),
    (
        ("action_type", AdmissionFailureCode.CORPORATE_ACTION_RECORDS_MISSING),
        ("effective_at", AdmissionFailureCode.CORPORATE_ACTION_EFFECTIVE_TIME_MISSING),
        ("available_at", AdmissionFailureCode.CORPORATE_ACTION_AVAILABLE_TIME_MISSING),
    ),
)
def test_corporate_action_required_fields_fail_closed(
    field_name: str,
    failure_code: AdmissionFailureCode,
) -> None:
    inspection = _inspection(
        fields=tuple(
            field
            for field in _inspection().fields
            if not (field.component == "corporate_action" and field.field_name == field_name)
        )
    )

    receipt = review_daily_equity_provider(inspection)

    assert receipt.status is AdmissionStatus.REJECTED
    assert failure_code in receipt.failure_codes


def test_cash_universe_missing_policy_retention_and_quote_requirements_fail_closed() -> None:
    fields = tuple(
        field
        for field in _inspection().fields
        if field.component not in {"cash_rate", "daily_price"}
    ) + tuple(
        field
        for field in _inspection().fields
        if field.component == "daily_price" and field.field_name != "liquidity_proxy"
    )
    receipt = review_daily_equity_provider(
        _inspection(
            fields=fields,
            fixed_universe_rule=None,
            missing_bar_policy=None,
            retention_and_rerun_permitted=False,
        )
    )

    assert receipt.status is AdmissionStatus.REJECTED
    assert {
        AdmissionFailureCode.CASH_ALIGNMENT_MISSING,
        AdmissionFailureCode.UNIVERSE_RULE_MISSING,
        AdmissionFailureCode.MISSING_BAR_POLICY_MISSING,
        AdmissionFailureCode.RETENTION_RIGHT_MISSING,
        AdmissionFailureCode.QUOTE_OR_PROXY_MISSING,
    }.issubset(receipt.failure_codes)


def test_coverage_gap_and_missing_adjustment_convention_fail_closed() -> None:
    receipt = review_daily_equity_provider(
        _inspection(
            coverage=(_coverage("daily_price"),),
            adjustment_convention=None,
        )
    )

    assert receipt.status is AdmissionStatus.REJECTED
    assert AdmissionFailureCode.COVERAGE_INVALID in receipt.failure_codes
    assert AdmissionFailureCode.ADJUSTMENT_CONVENTION_MISSING in receipt.failure_codes


def test_lockbox_or_campaign_state_makes_inspection_rejected_without_state_transition() -> None:
    receipt = review_daily_equity_provider(
        _inspection(
            lockbox_query_executed=True,
            candidate_registered=True,
            snapshot_created=True,
        )
    )

    assert receipt.status is AdmissionStatus.REJECTED
    assert AdmissionFailureCode.LOCKBOX_ACTION_ATTEMPTED in receipt.failure_codes
    assert AdmissionFailureCode.CAMPAIGN_STATE_ATTEMPTED in receipt.failure_codes
    assert receipt.lockbox_query_executed is True
    assert receipt.candidate_registered is True
    assert receipt.snapshot_created is True


def test_admitted_receipt_rejects_campaign_state_and_rejected_receipt_requires_failure() -> None:
    inspection = _inspection()
    with pytest.raises(ProviderAdmissionError, match="admitted receipt cannot record"):
        ProviderAdmissionReceipt(
            inspection_id=inspection.inspection_id,
            status=AdmissionStatus.ADMITTED,
            inspected_at=inspection.inspected_at,
            provider=inspection.provider,
            dataset=inspection.dataset,
            query_sha256=inspection.query_sha256,
            failure_codes=(),
            fields=inspection.fields,
            coverage=inspection.coverage,
            adjustment_convention=inspection.adjustment_convention,
            fixed_universe_rule=inspection.fixed_universe_rule,
            missing_bar_policy=inspection.missing_bar_policy,
            retention_and_rerun_permitted=True,
            lockbox_query_executed=True,
            candidate_registered=False,
            snapshot_created=False,
        )
    with pytest.raises(ProviderAdmissionError, match="rejected receipt must retain"):
        ProviderAdmissionReceipt(
            inspection_id=inspection.inspection_id,
            status=AdmissionStatus.REJECTED,
            inspected_at=inspection.inspected_at,
            provider=inspection.provider,
            dataset=inspection.dataset,
            query_sha256=inspection.query_sha256,
            failure_codes=(),
            fields=inspection.fields,
            coverage=inspection.coverage,
            adjustment_convention=inspection.adjustment_convention,
            fixed_universe_rule=inspection.fixed_universe_rule,
            missing_bar_policy=inspection.missing_bar_policy,
            retention_and_rerun_permitted=True,
            lockbox_query_executed=False,
            candidate_registered=False,
            snapshot_created=False,
        )


def test_contract_rejects_naive_times_duplicate_receipts_and_invalid_coverage() -> None:
    with pytest.raises(ProviderAdmissionError, match="timezone-aware"):
        CoverageReceipt(
            component="daily_price",
            coverage_start_at=datetime(2010, 1, 1),
            coverage_end_at=_END,
            observed_row_count=1,
            missing_row_count=0,
            correction_policy="final",
        )
    with pytest.raises(ProviderAdmissionError, match="unique by component"):
        _inspection(fields=(_field("daily_price", "close"), _field("daily_price", "close")))
    with pytest.raises(ProviderAdmissionError, match="coverage_start_at must precede"):
        CoverageReceipt(
            component="daily_price",
            coverage_start_at=_END,
            coverage_end_at=_START,
            observed_row_count=1,
            missing_row_count=0,
            correction_policy="final",
        )


def test_receipt_rejects_noncanonical_failures_and_bad_hashes() -> None:
    inspection = _inspection()
    with pytest.raises(ProviderAdmissionError, match="canonically ordered"):
        ProviderAdmissionReceipt(
            inspection_id=inspection.inspection_id,
            status=AdmissionStatus.REJECTED,
            inspected_at=inspection.inspected_at,
            provider=inspection.provider,
            dataset=inspection.dataset,
            query_sha256=inspection.query_sha256,
            failure_codes=(
                AdmissionFailureCode.VOLUME_FIELD_MISSING,
                AdmissionFailureCode.ADJUSTED_PRICE_MISSING,
            ),
            fields=inspection.fields,
            coverage=inspection.coverage,
            adjustment_convention=inspection.adjustment_convention,
            fixed_universe_rule=inspection.fixed_universe_rule,
            missing_bar_policy=inspection.missing_bar_policy,
            retention_and_rerun_permitted=True,
            lockbox_query_executed=False,
            candidate_registered=False,
            snapshot_created=False,
        )
    with pytest.raises(ProviderAdmissionError, match="SHA-256"):
        _inspection(query_sha256="bad")
