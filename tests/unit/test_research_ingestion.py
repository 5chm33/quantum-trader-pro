from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pytest

from quantum_trader.adapters.research_ingestion import (
    PinnedHttpsTransport,
    PointInTimeCorporateActionCsvIngestor,
    PointInTimeEquityCsvIngestor,
    RawResponse,
    ResearchIngestionError,
    SecEdgarFundamentalIngestor,
    TreasuryParCurveIngestor,
)
from quantum_trader.domain.research_data import (
    AssetClass,
    CorporateActionType,
    DataAvailability,
    ResearchDataError,
    SecurityIdentity,
)

_CAPTURED_AT = datetime(2025, 1, 10, 18, 0, tzinfo=UTC)
_SEC_CIK = "0000320193"


class ScriptedTransport:
    def __init__(self, *, base_url: str, responses: dict[str, RawResponse]) -> None:
        self._base_url = base_url
        self._responses = responses
        self.calls: list[str] = []

    @property
    def base_url(self) -> str:
        return self._base_url

    def get(
        self,
        *,
        path: str,
        query: dict[str, str | int] | None = None,
        headers: dict[str, str] | None = None,
    ) -> RawResponse:
        del query, headers
        self.calls.append(path)
        try:
            return self._responses[path]
        except KeyError as exc:
            raise AssertionError(f"unexpected request path: {path}") from exc


def _response(*, status_code: int = 200, body: bytes, source_uri: str) -> RawResponse:
    return RawResponse(
        status_code=status_code,
        body=body,
        source_uri=source_uri,
        captured_at=_CAPTURED_AT,
    )


def _security() -> SecurityIdentity:
    return SecurityIdentity(
        instrument_id="US0378331005",
        asset_class=AssetClass.EQUITY,
        currency="USD",
        symbol="AAPL",
        exchange="NASDAQ",
        cik=_SEC_CIK,
    )


def _sec_transport(*, include_acceptance: bool = True) -> ScriptedTransport:
    accession = "0000320193-24-000123"
    company_facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "form": "10-K",
                                "accn": accession,
                                "start": "2023-10-01",
                                "end": "2024-09-30",
                                "fy": 2024,
                                "fp": "FY",
                                "val": 391035000000,
                                "decimals": "-6",
                            },
                            {
                                "form": "8-K",
                                "accn": "0000320193-24-999999",
                                "end": "2024-09-30",
                                "val": 1,
                            },
                        ]
                    }
                }
            }
        }
    }
    recent: dict[str, object] = {"accessionNumber": [accession]}
    if include_acceptance:
        recent["acceptanceDateTime"] = ["2024-11-01T20:00:01Z"]
    else:
        recent["acceptanceDateTime"] = ["2024-11-01T20:00:01Z"]
        recent["accessionNumber"] = ["0000320193-24-000124"]
    submissions = {"filings": {"recent": recent}}
    return ScriptedTransport(
        base_url="https://data.sec.gov",
        responses={
            f"/api/xbrl/companyfacts/CIK{_SEC_CIK}.json": _response(
                body=json.dumps(company_facts).encode("utf-8"),
                source_uri=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_SEC_CIK}.json",
            ),
            f"/submissions/CIK{_SEC_CIK}.json": _response(
                body=json.dumps(submissions).encode("utf-8"),
                source_uri=f"https://data.sec.gov/submissions/CIK{_SEC_CIK}.json",
            ),
        },
    )


def test_sec_ingestion_normalizes_only_accession_timestamped_facts() -> None:
    transport = _sec_transport()
    ingestor = SecEdgarFundamentalIngestor(transport=transport)

    receipt = ingestor.fetch(cik=_SEC_CIK)
    facts = ingestor.normalize_concept(
        receipt=receipt,
        security=_security(),
        taxonomy="us-gaap",
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        unit="USD",
    )

    assert len(facts) == 1
    fact = facts[0]
    assert fact.value == Decimal("391035000000")
    assert fact.filing_accepted_at == datetime(2024, 11, 1, 20, 0, 1, tzinfo=UTC)
    assert fact.availability.available_at == fact.filing_accepted_at
    assert fact.availability.event_at == datetime(2024, 9, 30, tzinfo=UTC)
    assert fact.provenance.raw_sha256 == receipt.combined_raw_sha256
    assert transport.calls == [
        f"/api/xbrl/companyfacts/CIK{_SEC_CIK}.json",
        f"/submissions/CIK{_SEC_CIK}.json",
    ]


def test_sec_ingestion_rejects_facts_without_matching_acceptance_timestamp() -> None:
    ingestor = SecEdgarFundamentalIngestor(transport=_sec_transport(include_acceptance=False))
    receipt = ingestor.fetch(cik=_SEC_CIK)

    with pytest.raises(ResearchIngestionError, match="no normalized facts"):
        ingestor.normalize_concept(
            receipt=receipt,
            security=_security(),
            taxonomy="us-gaap",
            concept="RevenueFromContractWithCustomerExcludingAssessedTax",
            unit="USD",
        )


def test_sec_ingestion_rejects_cik_identity_mismatch() -> None:
    ingestor = SecEdgarFundamentalIngestor(transport=_sec_transport())
    receipt = ingestor.fetch(cik=_SEC_CIK)
    wrong_security = SecurityIdentity(
        instrument_id="US0378331005",
        asset_class=AssetClass.EQUITY,
        currency="USD",
        cik="0000789019",
    )

    with pytest.raises(ResearchIngestionError, match="does not match"):
        ingestor.normalize_concept(
            receipt=receipt,
            security=wrong_security,
            taxonomy="us-gaap",
            concept="RevenueFromContractWithCustomerExcludingAssessedTax",
            unit="USD",
        )


def test_treasury_archive_requires_explicit_availability_and_normalizes_nodes() -> None:
    archive = b"Date,1 Mo,2 Yr,10 Yr\n2024-01-02,5.50,4.20,4.00\n"
    response = _response(
        body=archive,
        source_uri=(
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            "daily-treasury-rate-archives/par-yield-curve-rates-2020-2023.csv"
        ),
    )
    transport = ScriptedTransport(
        base_url="https://home.treasury.gov",
        responses={"/resource-center/data-chart-center/interest-rates/archive.csv": response},
    )
    ingestor = TreasuryParCurveIngestor(transport=transport)
    fetched = ingestor.fetch_archive(
        archive_path="/resource-center/data-chart-center/interest-rates/archive.csv"
    )
    available_at = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)

    curves = ingestor.normalize(
        response=fetched,
        availability_by_vintage={date(2024, 1, 2): available_at},
    )

    assert len(curves) == 1
    curve = curves[0]
    assert curve.nodes[0].tenor_days == 30
    assert curve.nodes[-1].tenor_days == 3650
    assert curve.nodes[0].rate == Decimal("0.055")
    assert curve.availability.available_at == available_at
    assert curve.provenance.raw_sha256 == hashlib.sha256(archive).hexdigest()


def test_treasury_archive_refuses_selected_row_without_availability() -> None:
    transport = ScriptedTransport(base_url="https://home.treasury.gov", responses={})
    ingestor = TreasuryParCurveIngestor(transport=transport)
    response = _response(
        body=b"Date,1 Mo\n2024-01-02,5.50\n",
        source_uri="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/a.csv",
    )

    with pytest.raises(ResearchIngestionError, match="availability must be supplied"):
        ingestor.normalize(response=response, availability_by_vintage={})


def _write_equity_csv(path: Path, *, adjusted_rows: tuple[str, str] = ("101", "102")) -> None:
    path.write_text(
        "event_at,available_at,captured_at,open,high,low,close,volume,finality,adjusted_close,"
        "total_return_factor,provider_schema_version\n"
        "2024-01-02T21:00:00Z,2024-01-02T21:01:00Z,2024-01-02T21:02:00Z,"
        f"100,102,99,101,1000,final,{adjusted_rows[0]},1.01,v1\n"
        "2024-01-03T21:00:00Z,2024-01-03T21:01:00Z,2024-01-03T21:02:00Z,"
        f"101,103,100,102,1100,final,{adjusted_rows[1]},1.02,v1\n",
        encoding="utf-8",
    )


def test_equity_csv_requires_causal_timestamps_and_consistent_adjustments(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    _write_equity_csv(path)
    ingestor = PointInTimeEquityCsvIngestor(
        path=path,
        security=_security(),
        provider="fixture_provider",
        dataset="point_in_time_equity",
        source_uri="https://example.test/bars.csv",
        license_class="synthetic",
        redistribution_allowed=True,
    )

    bars = tuple(ingestor.stream())

    assert [bar.close for bar in bars] == [Decimal("101"), Decimal("102")]
    assert bars[0].adjusted_close == Decimal("101")
    assert bars[1].total_return_factor == Decimal("1.02")
    assert bars[0].availability.available_at > bars[0].availability.event_at


def test_equity_csv_rejects_mixed_adjustment_presence(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    _write_equity_csv(path, adjusted_rows=("101", ""))
    ingestor = PointInTimeEquityCsvIngestor(
        path=path,
        security=_security(),
        provider="fixture_provider",
        dataset="point_in_time_equity",
        source_uri="https://example.test/bars.csv",
        license_class="synthetic",
        redistribution_allowed=True,
    )

    with pytest.raises(ResearchIngestionError, match="all-or-none"):
        tuple(ingestor.stream())


def test_corporate_action_csv_requires_explicit_availability_and_ratio_pair(tmp_path: Path) -> None:
    path = tmp_path / "actions.csv"
    path.write_text(
        "event_at,available_at,captured_at,effective_at,action_type,status,cash_amount,currency,"
        "ratio_numerator,ratio_denominator,provider_schema_version\n"
        "2024-01-02T15:00:00Z,2024-01-02T15:01:00Z,2024-01-02T15:02:00Z,"
        "2024-02-01T00:00:00Z,cash_dividend,announced,0.24,USD,,,v1\n",
        encoding="utf-8",
    )
    ingestor = PointInTimeCorporateActionCsvIngestor(
        path=path,
        security=_security(),
        provider="fixture_provider",
        dataset="corporate_actions",
        source_uri="https://example.test/actions.csv",
        license_class="synthetic",
        redistribution_allowed=True,
    )

    actions = tuple(ingestor.stream())

    assert len(actions) == 1
    assert actions[0].action_type is CorporateActionType.CASH_DIVIDEND
    assert actions[0].cash_amount == Decimal("0.24")


def test_data_availability_rejects_lookahead_and_pinned_transport_rejects_unsafe_path() -> None:
    with pytest.raises(ResearchDataError, match="cannot precede"):
        DataAvailability(
            event_at=datetime(2024, 1, 2, 12, 0, tzinfo=UTC),
            available_at=datetime(2024, 1, 2, 11, 59, tzinfo=UTC),
            captured_at=datetime(2024, 1, 2, 12, 1, tzinfo=UTC),
        )
    transport = PinnedHttpsTransport(
        base_url="https://example.test",
        host="example.test",
        allowed_path_prefixes=frozenset({"/allowed/"}),
    )
    with pytest.raises(ResearchIngestionError, match="outside"):
        transport.get(path="/outside/data")


def test_corporate_action_rejects_unpaired_split_ratio(tmp_path: Path) -> None:
    path = tmp_path / "actions.csv"
    path.write_text(
        "event_at,available_at,captured_at,effective_at,action_type,status,ratio_numerator,"
        "ratio_denominator,provider_schema_version\n"
        "2024-01-02T15:00:00Z,2024-01-02T15:01:00Z,2024-01-02T15:02:00Z,"
        "2024-02-01T00:00:00Z,split,announced,2,,v1\n",
        encoding="utf-8",
    )
    ingestor = PointInTimeCorporateActionCsvIngestor(
        path=path,
        security=_security(),
        provider="fixture_provider",
        dataset="corporate_actions",
        source_uri="https://example.test/actions.csv",
        license_class="synthetic",
        redistribution_allowed=True,
    )

    with pytest.raises(ResearchDataError, match="numerator and denominator"):
        tuple(ingestor.stream())


def test_raw_response_and_pinned_transport_reject_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="HTTP status"):
        RawResponse(
            status_code=99,
            body=b"",
            source_uri="https://example.test/a",
            captured_at=_CAPTURED_AT,
        )
    with pytest.raises(ValueError, match="HTTPS"):
        RawResponse(
            status_code=200,
            body=b"",
            source_uri="http://example.test/a",
            captured_at=_CAPTURED_AT,
        )
    with pytest.raises(ResearchIngestionError, match="origin"):
        PinnedHttpsTransport(
            base_url="http://example.test",
            host="example.test",
            allowed_path_prefixes=frozenset({"/allowed/"}),
        )
    with pytest.raises(ResearchIngestionError, match="prefixes"):
        PinnedHttpsTransport(
            base_url="https://example.test",
            host="example.test",
            allowed_path_prefixes=frozenset(),
        )


def test_sec_and_treasury_ingestors_reject_wrong_origins_and_malformed_payloads() -> None:
    wrong_transport = ScriptedTransport(base_url="https://example.test", responses={})
    with pytest.raises(ResearchIngestionError, match="fixed SEC origin"):
        SecEdgarFundamentalIngestor(transport=wrong_transport)
    with pytest.raises(ResearchIngestionError, match="fixed Treasury origin"):
        TreasuryParCurveIngestor(transport=wrong_transport)

    malformed_transport = ScriptedTransport(
        base_url="https://data.sec.gov",
        responses={
            f"/api/xbrl/companyfacts/CIK{_SEC_CIK}.json": _response(
                body=b"not-json",
                source_uri=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_SEC_CIK}.json",
            ),
            f"/submissions/CIK{_SEC_CIK}.json": _response(
                body=b"{}",
                source_uri=f"https://data.sec.gov/submissions/CIK{_SEC_CIK}.json",
            ),
        },
    )
    ingestor = SecEdgarFundamentalIngestor(transport=malformed_transport)
    receipt = ingestor.fetch(cik=_SEC_CIK)
    with pytest.raises(ResearchIngestionError, match="not valid JSON"):
        ingestor.normalize_concept(
            receipt=receipt,
            security=_security(),
            taxonomy="us-gaap",
            concept="Revenue",
            unit="USD",
        )
    with pytest.raises(ResearchIngestionError, match="CIK"):
        ingestor.fetch(cik="not-a-cik")


def test_treasury_ingestion_rejects_unknown_layout_encoding_and_dates() -> None:
    ingestor = TreasuryParCurveIngestor(
        transport=ScriptedTransport(base_url="https://home.treasury.gov", responses={})
    )
    response = _response(
        body=b"Date,Other\n2024-01-01,1\n",
        source_uri="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/a.csv",
    )
    with pytest.raises(ResearchIngestionError, match="recognized tenor"):
        ingestor.normalize(
            response=response,
            availability_by_vintage={date(2024, 1, 1): _CAPTURED_AT},
        )
    bad_date = _response(
        body=b"date,1 mo\nnot-a-date,5.1\n",
        source_uri="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/a.csv",
    )
    with pytest.raises(ResearchIngestionError, match="accepted archive date"):
        ingestor.normalize(
            response=bad_date,
            availability_by_vintage={date(2024, 1, 1): _CAPTURED_AT},
        )
    malformed_utf8 = _response(
        body=b"\xff\xfe",
        source_uri="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/a.csv",
    )
    with pytest.raises(ResearchIngestionError, match="UTF-8"):
        ingestor.normalize(response=malformed_utf8, availability_by_vintage={})


def test_equity_and_corporate_action_csv_reject_missing_columns_and_bad_order(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.csv"
    missing.write_text("event_at,close\n2024-01-01T00:00:00Z,1\n", encoding="utf-8")
    ingestor = PointInTimeEquityCsvIngestor(
        path=missing,
        security=_security(),
        provider="fixture_provider",
        dataset="bars",
        source_uri="https://example.test/bars.csv",
        license_class="synthetic",
        redistribution_allowed=True,
    )
    with pytest.raises(ResearchIngestionError, match="missing required"):
        tuple(ingestor.stream())

    descending = tmp_path / "descending.csv"
    _write_equity_csv(descending)
    text = descending.read_text(encoding="utf-8")
    first, second, third = text.splitlines()
    descending.write_text("\n".join((first, third, second, "")), encoding="utf-8")
    descending_ingestor = PointInTimeEquityCsvIngestor(
        path=descending,
        security=_security(),
        provider="fixture_provider",
        dataset="bars",
        source_uri="https://example.test/bars.csv",
        license_class="synthetic",
        redistribution_allowed=True,
    )
    with pytest.raises(ResearchIngestionError, match="strictly increasing"):
        tuple(descending_ingestor.stream())

    action_missing = tmp_path / "action_missing.csv"
    action_missing.write_text("event_at,status\n2024-01-01T00:00:00Z,announced\n", encoding="utf-8")
    action_ingestor = PointInTimeCorporateActionCsvIngestor(
        path=action_missing,
        security=_security(),
        provider="fixture_provider",
        dataset="actions",
        source_uri="https://example.test/actions.csv",
        license_class="synthetic",
        redistribution_allowed=True,
    )
    with pytest.raises(ResearchIngestionError, match="missing required"):
        tuple(action_ingestor.stream())


def test_pinned_transport_rejects_path_traversal_before_network_access() -> None:
    transport = PinnedHttpsTransport(
        base_url="https://example.test",
        host="example.test",
        allowed_path_prefixes=frozenset({"/allowed/"}),
    )
    for unsafe_path in ("/allowed/../secret", "/allowed/data?x=1", "relative"):
        with pytest.raises(ResearchIngestionError, match="unsafe"):
            transport.get(path=unsafe_path)


class _FakeHttpResponse:
    def __init__(self, *, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeHttpsConnection:
    requests: ClassVar[list[tuple[str, str, dict[str, str]]]] = []
    response: ClassVar[_FakeHttpResponse] = _FakeHttpResponse(status=200, body=b"{}")
    failure: ClassVar[BaseException | None] = None
    closed: ClassVar[bool] = False

    def __init__(self, host: str, timeout: float) -> None:
        assert host == "example.test"
        assert timeout == 5.0

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        if self.failure is not None:
            raise self.failure
        self.requests.append((method, target, headers))

    def getresponse(self) -> _FakeHttpResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def test_pinned_https_transport_canonicalizes_queries_and_redacts_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quantum_trader.adapters import research_ingestion as module

    _FakeHttpsConnection.requests.clear()
    _FakeHttpsConnection.failure = None
    monkeypatch.setattr(module.http.client, "HTTPSConnection", _FakeHttpsConnection)
    transport = PinnedHttpsTransport(
        base_url="https://example.test",
        host="example.test",
        allowed_path_prefixes=frozenset({"/allowed/"}),
        timeout_seconds=5.0,
        now=lambda: _CAPTURED_AT,
    )

    response = transport.get(
        path="/allowed/data", query={"b": 2, "a": "one"}, headers={"X-Test": "yes"}
    )

    assert response.status_code == 200
    assert response.source_uri == "https://example.test/allowed/data?b=2&a=one"
    assert _FakeHttpsConnection.requests == [
        (
            "GET",
            "/allowed/data?b=2&a=one",
            {
                "Accept": "application/json, text/csv;q=0.9",
                "User-Agent": "quantum-trader-pro-research/0.2 (contact-required)",
                "X-Test": "yes",
            },
        )
    ]

    _FakeHttpsConnection.failure = TimeoutError("source detail must not leak")
    with pytest.raises(ResearchIngestionError, match="failed or timed out"):
        transport.get(path="/allowed/data")
    _FakeHttpsConnection.failure = None


def test_sec_transport_receipt_and_treasury_fetch_reject_non_successful_responses() -> None:
    from quantum_trader.adapters.research_ingestion import SecFetchReceipt

    failed = _response(
        status_code=503,
        body=b"redacted",
        source_uri="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    )
    successful = _response(
        body=b"{}",
        source_uri="https://data.sec.gov/submissions/CIK0000320193.json",
    )
    with pytest.raises(ResearchIngestionError, match="complete company facts"):
        SecFetchReceipt(company_facts=failed, submissions=successful, cik=_SEC_CIK)

    treasury_transport = ScriptedTransport(
        base_url="https://home.treasury.gov",
        responses={
            "/resource-center/data-chart-center/interest-rates/a.csv": _response(
                status_code=404,
                body=b"not retained",
                source_uri="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/a.csv",
            )
        },
    )
    treasury = TreasuryParCurveIngestor(transport=treasury_transport)
    with pytest.raises(ResearchIngestionError, match="HTTP 404"):
        treasury.fetch_archive(
            archive_path="/resource-center/data-chart-center/interest-rates/a.csv"
        )


def test_sec_transport_requires_contact_and_helper_parsers_fail_closed() -> None:
    from quantum_trader.adapters import research_ingestion as module

    with pytest.raises(ResearchIngestionError, match="contact email"):
        module.SecEdgarTransport(user_agent="no-contact")
    with pytest.raises(ResearchIngestionError, match="invalid"):
        module._identifier("bad+", "identifier")
    with pytest.raises(ResearchIngestionError, match="timezone-aware"):
        module._utc(datetime(2024, 1, 1), "clock")
    with pytest.raises(ResearchIngestionError, match="object"):
        module._mapping([], "payload")
    with pytest.raises(ResearchIngestionError, match="array"):
        module._list({}, "payload")
    with pytest.raises(ResearchIngestionError, match="non-empty"):
        module._required_string({}, "form")
    with pytest.raises(ResearchIngestionError, match="optional string"):
        module._optional_string(1)
    with pytest.raises(ResearchIngestionError, match="integer"):
        module._optional_int(True, "fy")
    with pytest.raises(ResearchIngestionError, match="decimal-compatible"):
        module._decimal(True, "price")
    with pytest.raises(ResearchIngestionError, match="finite"):
        module._decimal("NaN", "price")
    with pytest.raises(ResearchIngestionError, match="ISO date"):
        module._optional_date("not-a-date", "ex_date")
    with pytest.raises(ResearchIngestionError, match="ISO datetime"):
        module._optional_timestamp("not-a-time", "published_at")
    with pytest.raises(ResearchIngestionError, match="must be non-empty"):
        module._required_row_value({}, "close")
    with pytest.raises(ResearchIngestionError, match="all-or-none"):
        module._consistent_presence(True, False, "adjusted_close")


def test_sec_normalizer_skips_disallowed_forms_and_rejects_misaligned_acceptance_arrays() -> None:
    transport = _sec_transport()
    ingestor = SecEdgarFundamentalIngestor(transport=transport)
    receipt = ingestor.fetch(cik=_SEC_CIK)
    facts = ingestor.normalize_concept(
        receipt=receipt,
        security=_security(),
        taxonomy="us-gaap",
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        unit="USD",
    )
    assert all(fact.form != "8-K" for fact in facts)

    with pytest.raises(ResearchIngestionError, match="misaligned"):
        from quantum_trader.adapters import research_ingestion as module

        module._acceptance_by_accession(
            {
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000320193-24-000123"],
                        "acceptanceDateTime": [],
                    }
                }
            }
        )


def test_csv_ingestors_reject_missing_paths_blank_provenance_and_invalid_values(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        PointInTimeEquityCsvIngestor(
            path=tmp_path / "missing.csv",
            security=_security(),
            provider="fixture",
            dataset="bars",
            source_uri="https://example.test/bars.csv",
            license_class="synthetic",
            redistribution_allowed=True,
        )
    path = tmp_path / "bars.csv"
    _write_equity_csv(path)
    with pytest.raises(ResearchIngestionError, match="provenance"):
        PointInTimeEquityCsvIngestor(
            path=path,
            security=_security(),
            provider=" ",
            dataset="bars",
            source_uri="https://example.test/bars.csv",
            license_class="synthetic",
            redistribution_allowed=True,
        )
    invalid = tmp_path / "invalid.csv"
    invalid.write_text(
        "event_at,available_at,captured_at,open,high,low,close,volume,finality\n"
        "2024-01-02T21:00:00Z,2024-01-02T21:01:00Z,2024-01-02T21:02:00Z,"
        "100,102,99,101,1000,not-a-finality\n",
        encoding="utf-8",
    )
    invalid_ingestor = PointInTimeEquityCsvIngestor(
        path=invalid,
        security=_security(),
        provider="fixture",
        dataset="bars",
        source_uri="https://example.test/bars.csv",
        license_class="synthetic",
        redistribution_allowed=True,
    )
    with pytest.raises(ValueError, match="not-a-finality"):
        tuple(invalid_ingestor.stream())
