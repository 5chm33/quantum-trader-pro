from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from quantum_trader.domain.research_data import (
    AssetClass,
    BarFinality,
    Compounding,
    CorporateActionRecord,
    CorporateActionStatus,
    CorporateActionType,
    CurveType,
    DataAvailability,
    DataProvenance,
    DayCountConvention,
    EquityBarRecord,
    FundamentalFactRecord,
    InstrumentType,
    RateCurveRecord,
    RateNode,
    RecordIdentity,
    ResearchDataError,
    SecurityIdentity,
)

_NOW = datetime(2024, 2, 2, 15, 0, tzinfo=UTC)
_SHA = "a" * 64


def _availability() -> DataAvailability:
    return DataAvailability(
        event_at=_NOW - timedelta(minutes=2),
        published_at=_NOW - timedelta(minutes=1),
        available_at=_NOW - timedelta(minutes=1),
        captured_at=_NOW,
    )


def _provenance(**overrides: object) -> DataProvenance:
    values: dict[str, object] = {
        "provider": "fixture_provider",
        "dataset": "fixture_dataset",
        "provider_schema_version": "v1",
        "source_uri": "https://example.test/data",
        "license_class": "synthetic",
        "redistribution_allowed": True,
        "raw_sha256": _SHA,
        "query_sha256": "b" * 64,
        "transform_version": "fixture_transform_v1",
    }
    values.update(overrides)
    return DataProvenance(**values)  # type: ignore[arg-type]


def _security(**overrides: object) -> SecurityIdentity:
    values: dict[str, object] = {
        "instrument_id": "US0378331005",
        "asset_class": AssetClass.EQUITY,
        "currency": "USD",
        "symbol": "AAPL",
        "cik": "0000320193",
    }
    values.update(overrides)
    return SecurityIdentity(**values)  # type: ignore[arg-type]


def _identity(**overrides: object) -> RecordIdentity:
    values: dict[str, object] = {"record_id": "fixture.record.0001"}
    values.update(overrides)
    return RecordIdentity(**values)  # type: ignore[arg-type]


def _bar(**overrides: object) -> EquityBarRecord:
    values: dict[str, object] = {
        "identity": _identity(),
        "security": _security(),
        "availability": _availability(),
        "provenance": _provenance(),
        "interval": "1d",
        "session": "regular",
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("99"),
        "close": Decimal("101"),
        "volume": Decimal("1000"),
        "finality": BarFinality.FINAL,
    }
    values.update(overrides)
    return EquityBarRecord(**values)  # type: ignore[arg-type]


def test_availability_rejects_naive_and_noncausal_timestamps() -> None:
    with pytest.raises(ResearchDataError, match="timezone-aware"):
        DataAvailability(event_at=datetime(2024, 1, 1), available_at=_NOW, captured_at=_NOW)
    with pytest.raises(ResearchDataError, match="published_at"):
        DataAvailability(
            event_at=_NOW - timedelta(minutes=2),
            published_at=_NOW,
            available_at=_NOW - timedelta(seconds=1),
            captured_at=_NOW,
        )
    with pytest.raises(ResearchDataError, match="captured_at"):
        DataAvailability(
            event_at=_NOW - timedelta(minutes=2),
            available_at=_NOW,
            captured_at=_NOW - timedelta(seconds=1),
        )


def test_provenance_rejects_invalid_license_digest_and_sequence() -> None:
    with pytest.raises(ResearchDataError, match="license_class"):
        _provenance(license_class="unknown")
    with pytest.raises(ResearchDataError, match="raw_sha256"):
        _provenance(raw_sha256="not-a-hash")
    with pytest.raises(ResearchDataError, match="source_sequence"):
        _provenance(source_sequence=-1)


def test_security_and_record_identity_reject_invalid_fields() -> None:
    with pytest.raises(ResearchDataError, match="currency"):
        _security(currency="US")
    with pytest.raises(ResearchDataError, match="symbol"):
        _security(symbol=" ")
    with pytest.raises(ResearchDataError, match="cik"):
        _security(cik="123")
    with pytest.raises(ResearchDataError, match="record_version"):
        _identity(record_version=0)
    with pytest.raises(ResearchDataError, match="supersedes"):
        _identity(supersedes_record_id="bad+")


def test_equity_bar_rejects_non_equity_bad_session_and_inconsistent_ohlc() -> None:
    with pytest.raises(ResearchDataError, match="equity bars"):
        _bar(security=_security(asset_class=AssetClass.OPTION))
    with pytest.raises(ResearchDataError, match="interval"):
        _bar(interval="tick")
    with pytest.raises(ResearchDataError, match="session"):
        _bar(session="overnight")
    with pytest.raises(ResearchDataError, match="internally inconsistent"):
        _bar(high=Decimal("100"), close=Decimal("101"))
    with pytest.raises(ResearchDataError, match="total_return_factor"):
        _bar(total_return_factor=Decimal("0"))


def test_corporate_action_rejects_invalid_effective_cash_and_ratio_inputs() -> None:
    values = {
        "identity": _identity(),
        "security": _security(),
        "availability": _availability(),
        "provenance": _provenance(),
        "action_type": CorporateActionType.CASH_DIVIDEND,
        "status": CorporateActionStatus.ANNOUNCED,
        "effective_at": _NOW + timedelta(days=1),
    }
    with pytest.raises(ResearchDataError, match="effective_at"):
        CorporateActionRecord(**{**values, "effective_at": _NOW - timedelta(days=1)})
    with pytest.raises(ResearchDataError, match="currency"):
        CorporateActionRecord(**{**values, "cash_amount": Decimal("0.25")})
    with pytest.raises(ResearchDataError, match="numerator and denominator"):
        CorporateActionRecord(**{**values, "ratio_numerator": Decimal("2")})


def test_fundamental_fact_rejects_missing_cik_acceptance_and_period_errors() -> None:
    values = {
        "identity": _identity(),
        "security": _security(),
        "availability": _availability(),
        "provenance": _provenance(),
        "accession_number": "0000320193-24-000123",
        "form": "10-K",
        "filing_accepted_at": _NOW - timedelta(minutes=1),
        "report_period_end": date(2024, 1, 1),
        "taxonomy": "us-gaap",
        "concept": "Revenue",
        "unit": "USD",
        "value": Decimal("100"),
        "amendment": False,
    }
    with pytest.raises(ResearchDataError, match="CIK"):
        FundamentalFactRecord(**{**values, "security": _security(cik=None)})
    with pytest.raises(ResearchDataError, match="accession_number"):
        FundamentalFactRecord(**{**values, "accession_number": "malformed"})
    with pytest.raises(ResearchDataError, match="report_period_start"):
        FundamentalFactRecord(**{**values, "report_period_start": date(2024, 2, 1)})


def test_rate_node_and_curve_reject_bad_tenors_and_invalid_curve_metadata() -> None:
    with pytest.raises(ResearchDataError, match="tenor_days"):
        RateNode(tenor_days=-1, rate=Decimal("0.01"), instrument_type=InstrumentType.CASH)
    node = RateNode(
        tenor_days=30, rate=Decimal("0.01"), instrument_type=InstrumentType.TREASURY_BILL
    )
    values = {
        "identity": _identity(),
        "availability": _availability(),
        "provenance": _provenance(),
        "curve_id": "usd.curve",
        "currency": "USD",
        "curve_type": CurveType.TREASURY_PAR,
        "day_count_convention": DayCountConvention.ACT_ACT,
        "compounding": Compounding.ANNUAL,
        "vintage_date": date(2024, 1, 1),
        "nodes": (node,),
    }
    with pytest.raises(ResearchDataError, match="curve currency"):
        RateCurveRecord(**{**values, "currency": "US"})
    with pytest.raises(ResearchDataError, match="unique increasing"):
        RateCurveRecord(**{**values, "nodes": (node, node)})
    with pytest.raises(ResearchDataError, match="interpolation_method"):
        RateCurveRecord(**{**values, "interpolation_method": "spline"})
