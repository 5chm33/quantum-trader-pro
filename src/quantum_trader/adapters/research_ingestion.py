from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlencode

from quantum_trader.domain.rate_limits import (
    RequestBudgetExceeded,
    SlidingWindowRequestBudget,
)
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
    SecurityIdentity,
)

_SEC_HOST = "data.sec.gov"
_SEC_BASE_URL = "https://data.sec.gov"
_TREASURY_HOST = "home.treasury.gov"
_TREASURY_BASE_URL = "https://home.treasury.gov"
_ALLOWED_SEC_FORMS = frozenset(
    {
        "10-K",
        "10-K/A",
        "10-Q",
        "10-Q/A",
        "8-K",
        "8-K/A",
        "20-F",
        "20-F/A",
        "40-F",
        "40-F/A",
        "6-K",
        "6-K/A",
    }
)
_TREASURY_TENOR_DAYS = {
    "1 mo": 30,
    "1.5 month": 45,
    "2 mo": 60,
    "3 mo": 90,
    "4 mo": 120,
    "6 mo": 180,
    "1 yr": 365,
    "2 yr": 730,
    "3 yr": 1095,
    "5 yr": 1825,
    "7 yr": 2555,
    "10 yr": 3650,
    "20 yr": 7300,
    "30 yr": 10950,
}


class ResearchIngestionError(RuntimeError):
    """Raised for read-only research ingestion failures with redacted payloads."""


@dataclass(frozen=True, slots=True)
class RawResponse:
    status_code: int
    body: bytes
    source_uri: str
    captured_at: datetime

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be a valid HTTP status")
        if not self.source_uri.startswith("https://"):
            raise ValueError("source_uri must use HTTPS")
        captured_at = _utc(self.captured_at, "captured_at")
        object.__setattr__(self, "captured_at", captured_at)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


class ReadOnlyHttpsTransport(Protocol):
    @property
    def base_url(self) -> str:
        """Return the exact pinned HTTPS origin."""

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> RawResponse:
        """Return raw bytes from one read-only HTTPS request."""


class PinnedHttpsTransport:
    """Small standard-library GET-only transport for one approved origin."""

    def __init__(
        self,
        *,
        base_url: str,
        host: str,
        allowed_path_prefixes: frozenset[str],
        timeout_seconds: float = 10.0,
        request_budget: SlidingWindowRequestBudget | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not base_url.startswith("https://") or "/" in host or not host:
            raise ResearchIngestionError("research transport origin is invalid")
        if not allowed_path_prefixes or any(
            not prefix.startswith("/") for prefix in allowed_path_prefixes
        ):
            raise ResearchIngestionError(
                "research transport requires absolute allowed path prefixes"
            )
        if not 0 < timeout_seconds <= 30:
            raise ResearchIngestionError("timeout_seconds must be in (0, 30]")
        self._base_url = base_url.rstrip("/")
        self._host = host
        self._allowed_path_prefixes = allowed_path_prefixes
        self._timeout_seconds = timeout_seconds
        self._request_budget = request_budget or SlidingWindowRequestBudget()
        self._now = now or _system_now

    @property
    def base_url(self) -> str:
        return self._base_url

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> RawResponse:
        _validate_path(path, self._allowed_path_prefixes)
        try:
            self._request_budget.acquire(self._now())
        except (RequestBudgetExceeded, ValueError) as exc:
            raise ResearchIngestionError("local research request budget denied the call") from exc
        target = path
        if query:
            target = f"{path}?{urlencode(query)}"
        request_headers = {
            "Accept": "application/json, text/csv;q=0.9",
            "User-Agent": "quantum-trader-pro-research/0.2 (contact-required)",
        }
        if headers:
            request_headers.update(headers)
        connection = http.client.HTTPSConnection(self._host, timeout=self._timeout_seconds)
        try:
            connection.request("GET", target, headers=request_headers)
            response = connection.getresponse()
            body = response.read()
        except (TimeoutError, http.client.RemoteDisconnected, OSError) as exc:
            raise ResearchIngestionError("research source request failed or timed out") from exc
        finally:
            connection.close()
        captured_at = _utc(self._now(), "transport clock")
        return RawResponse(
            status_code=response.status,
            body=body,
            source_uri=f"{self._base_url}{target}",
            captured_at=captured_at,
        )


class SecEdgarTransport(PinnedHttpsTransport):
    """GET-only SEC transport pinned to submission and company-facts paths."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 10.0,
        request_budget: SlidingWindowRequestBudget | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not user_agent.strip() or "@" not in user_agent:
            raise ResearchIngestionError("SEC user_agent must include a contact email address")
        super().__init__(
            base_url=_SEC_BASE_URL,
            host=_SEC_HOST,
            allowed_path_prefixes=frozenset({"/submissions/", "/api/xbrl/companyfacts/"}),
            timeout_seconds=timeout_seconds,
            request_budget=request_budget,
            now=now,
        )
        self._user_agent = user_agent.strip()

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> RawResponse:
        request_headers = {"User-Agent": self._user_agent}
        if headers:
            request_headers.update(headers)
        return super().get(path=path, query=query, headers=request_headers)


class TreasuryArchiveTransport(PinnedHttpsTransport):
    """GET-only transport for Treasury daily-rate archive CSVs."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        request_budget: SlidingWindowRequestBudget | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            base_url=_TREASURY_BASE_URL,
            host=_TREASURY_HOST,
            allowed_path_prefixes=frozenset({"/resource-center/data-chart-center/interest-rates/"}),
            timeout_seconds=timeout_seconds,
            request_budget=request_budget,
            now=now,
        )


@dataclass(frozen=True, slots=True)
class SecFetchReceipt:
    company_facts: RawResponse
    submissions: RawResponse
    cik: str

    def __post_init__(self) -> None:
        cik = _cik(self.cik)
        if self.company_facts.status_code != 200 or self.submissions.status_code != 200:
            raise ResearchIngestionError(
                "SEC source did not return complete company facts and submissions"
            )
        object.__setattr__(self, "cik", cik)

    @property
    def combined_raw_sha256(self) -> str:
        return hashlib.sha256(self.company_facts.body + self.submissions.body).hexdigest()

    @property
    def captured_at(self) -> datetime:
        return max(self.company_facts.captured_at, self.submissions.captured_at)


class SecEdgarFundamentalIngestor:
    """Normalize SEC company facts only when an accession has retained acceptance time."""

    def __init__(
        self,
        *,
        transport: ReadOnlyHttpsTransport,
        transform_version: str = "sec_edgar_fundamentals_v1",
    ) -> None:
        if transport.base_url != _SEC_BASE_URL:
            raise ResearchIngestionError("SEC ingestion transport must use the fixed SEC origin")
        self._transport = transport
        self._transform_version = _identifier(transform_version, "transform_version")

    def fetch(self, *, cik: str) -> SecFetchReceipt:
        normalized_cik = _cik(cik)
        return SecFetchReceipt(
            company_facts=self._transport.get(
                path=f"/api/xbrl/companyfacts/CIK{normalized_cik}.json"
            ),
            submissions=self._transport.get(path=f"/submissions/CIK{normalized_cik}.json"),
            cik=normalized_cik,
        )

    def normalize_concept(
        self,
        *,
        receipt: SecFetchReceipt,
        security: SecurityIdentity,
        taxonomy: str,
        concept: str,
        unit: str,
    ) -> tuple[FundamentalFactRecord, ...]:
        if security.cik != receipt.cik:
            raise ResearchIngestionError("SEC security identity CIK does not match fetched payload")
        company_facts = _json_mapping(receipt.company_facts.body, "SEC company facts")
        submissions = _json_mapping(receipt.submissions.body, "SEC submissions")
        facts = _mapping(company_facts.get("facts"), "SEC facts")
        taxonomy_payload = _mapping(facts.get(taxonomy), "SEC taxonomy")
        concept_payload = _mapping(taxonomy_payload.get(concept), "SEC concept")
        units = _mapping(concept_payload.get("units"), "SEC concept units")
        raw_entries = _list(units.get(unit), "SEC concept unit entries")
        acceptance_by_accession = _acceptance_by_accession(submissions)
        normalized: list[FundamentalFactRecord] = []
        query_sha256 = _query_hash(
            {
                "cik": receipt.cik,
                "taxonomy": taxonomy,
                "concept": concept,
                "unit": unit,
                "company_facts_uri": receipt.company_facts.source_uri,
                "submissions_uri": receipt.submissions.source_uri,
            }
        )
        provenance = DataProvenance(
            provider="sec",
            dataset="edgar_companyfacts_and_submissions",
            provider_schema_version="data.sec.gov-json",
            source_uri=receipt.company_facts.source_uri,
            license_class="open",
            redistribution_allowed=True,
            raw_sha256=receipt.combined_raw_sha256,
            query_sha256=query_sha256,
            transform_version=self._transform_version,
        )
        for index, raw_entry in enumerate(raw_entries):
            entry = _mapping(raw_entry, "SEC concept entry")
            form = _required_string(entry, "form")
            accession = _required_string(entry, "accn")
            if form not in _ALLOWED_SEC_FORMS:
                continue
            accepted_at = acceptance_by_accession.get(accession)
            if accepted_at is None:
                continue
            report_end = _date(_required_string(entry, "end"), "SEC report period end")
            event_at = datetime.combine(report_end, time.min, UTC)
            value = _decimal(entry.get("val"), "SEC fact value")
            fiscal_year = _optional_int(entry.get("fy"), "fy")
            fiscal_period = _optional_string(entry.get("fp")) or "OTHER"
            decimals = _optional_int(entry.get("decimals"), "decimals")
            record_id = _identifier(
                f"sec.fact:{receipt.cik}:{taxonomy}:{concept}:{unit}:{accession}:{index}",
                "record_id",
            ).lower()
            normalized.append(
                FundamentalFactRecord(
                    identity=RecordIdentity(record_id=record_id),
                    security=security,
                    availability=DataAvailability(
                        event_at=event_at,
                        published_at=accepted_at,
                        available_at=accepted_at,
                        captured_at=receipt.captured_at,
                    ),
                    provenance=provenance,
                    accession_number=accession,
                    form=form,
                    filing_accepted_at=accepted_at,
                    report_period_end=report_end,
                    taxonomy=taxonomy,
                    concept=concept,
                    unit=unit,
                    value=value,
                    amendment=form.endswith("/A"),
                    report_period_start=(
                        _date(_required_string(entry, "start"), "SEC report period start")
                        if entry.get("start") is not None
                        else None
                    ),
                    fiscal_year=fiscal_year,
                    fiscal_period=fiscal_period,
                    decimals=decimals,
                    original_accession_number=None,
                )
            )
        if not normalized:
            raise ResearchIngestionError(
                "SEC concept had no normalized facts with retained acceptance timestamps"
            )
        return tuple(
            sorted(normalized, key=lambda item: (item.filing_accepted_at, item.identity.record_id))
        )


class TreasuryParCurveIngestor:
    """Normalize official Treasury archive rows only with caller-supplied availability policy."""

    def __init__(
        self,
        *,
        transport: ReadOnlyHttpsTransport,
        transform_version: str = "treasury_par_curve_v1",
    ) -> None:
        if transport.base_url != _TREASURY_BASE_URL:
            raise ResearchIngestionError(
                "Treasury ingestion transport must use the fixed Treasury origin"
            )
        self._transport = transport
        self._transform_version = _identifier(transform_version, "transform_version")

    def fetch_archive(self, *, archive_path: str) -> RawResponse:
        response = self._transport.get(path=archive_path)
        if response.status_code != 200:
            raise ResearchIngestionError(
                f"Treasury archive returned HTTP {response.status_code}; response body redacted"
            )
        return response

    def normalize(
        self,
        *,
        response: RawResponse,
        availability_by_vintage: Mapping[date, datetime],
        requested_dates: Iterable[date] | None = None,
    ) -> tuple[RateCurveRecord, ...]:
        requested = frozenset(requested_dates) if requested_dates is not None else None
        try:
            text = response.body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ResearchIngestionError("Treasury archive must be UTF-8 text") from exc
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ResearchIngestionError("Treasury archive is missing header row")
        source_headers = {header.strip().lower(): header for header in reader.fieldnames}
        date_header = source_headers.get("date")
        if date_header is None:
            raise ResearchIngestionError("Treasury archive is missing date column")
        nodes_by_header = {
            source_headers[normalized_header]: tenor
            for normalized_header, tenor in _TREASURY_TENOR_DAYS.items()
            if normalized_header in source_headers
        }
        if not nodes_by_header:
            raise ResearchIngestionError("Treasury archive contains no recognized tenor columns")
        query_sha256 = _query_hash(
            {
                "source_uri": response.source_uri,
                "requested_dates": sorted(item.isoformat() for item in requested)
                if requested
                else None,
                "availability_dates": sorted(item.isoformat() for item in availability_by_vintage),
            }
        )
        provenance = DataProvenance(
            provider="us_treasury",
            dataset="daily_treasury_par_yield_curve",
            provider_schema_version="treasury_archive_csv",
            source_uri=response.source_uri,
            license_class="open",
            redistribution_allowed=True,
            raw_sha256=response.sha256,
            query_sha256=query_sha256,
            transform_version=self._transform_version,
        )
        curves: list[RateCurveRecord] = []
        for source_sequence, row in enumerate(reader):
            vintage = _treasury_date(_required_row_value(row, date_header), "Treasury vintage date")
            if requested is not None and vintage not in requested:
                continue
            available_at = availability_by_vintage.get(vintage)
            if available_at is None:
                raise ResearchIngestionError(
                    "Treasury curve availability must be supplied explicitly "
                    "for every selected vintage"
                )
            available_at = _utc(available_at, "Treasury availability")
            nodes: list[RateNode] = []
            for header, tenor_days in nodes_by_header.items():
                raw_rate = (row.get(header) or "").strip()
                if not raw_rate or raw_rate.upper() in {"N/A", "NA"}:
                    continue
                nodes.append(
                    RateNode(
                        tenor_days=tenor_days,
                        rate=_decimal(raw_rate, f"Treasury {header}") / Decimal("100"),
                        instrument_type=(
                            InstrumentType.TREASURY_BILL
                            if tenor_days <= 365
                            else InstrumentType.TREASURY_NOTE
                        ),
                        source_series_id=header,
                    )
                )
            if not nodes:
                raise ResearchIngestionError(
                    "Treasury curve selected row contains no valid rate nodes"
                )
            event_at = datetime.combine(vintage, time.min, UTC)
            curves.append(
                RateCurveRecord(
                    identity=RecordIdentity(
                        record_id=f"treasury.curve.usd.par:{vintage.isoformat()}",
                    ),
                    availability=DataAvailability(
                        event_at=event_at,
                        published_at=available_at,
                        available_at=available_at,
                        captured_at=response.captured_at,
                    ),
                    provenance=DataProvenance(
                        provider=provenance.provider,
                        dataset=provenance.dataset,
                        provider_schema_version=provenance.provider_schema_version,
                        source_uri=provenance.source_uri,
                        license_class=provenance.license_class,
                        redistribution_allowed=provenance.redistribution_allowed,
                        raw_sha256=provenance.raw_sha256,
                        query_sha256=provenance.query_sha256,
                        transform_version=provenance.transform_version,
                        source_sequence=source_sequence,
                    ),
                    curve_id="usd.treasury.par.daily",
                    currency="USD",
                    curve_type=CurveType.TREASURY_PAR,
                    day_count_convention=DayCountConvention.ACT_ACT,
                    compounding=Compounding.ANNUAL,
                    vintage_date=vintage,
                    nodes=tuple(sorted(nodes, key=lambda item: item.tenor_days)),
                    interpolation_method="none",
                )
            )
        if not curves:
            raise ResearchIngestionError("Treasury archive query returned no selected curve rows")
        return tuple(sorted(curves, key=lambda item: item.vintage_date))


class PointInTimeEquityCsvIngestor:
    """Read local equity bars only when causal availability and provenance columns exist."""

    _required_columns = frozenset(
        {
            "event_at",
            "available_at",
            "captured_at",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "finality",
        }
    )

    def __init__(
        self,
        *,
        path: str | Path,
        security: SecurityIdentity,
        provider: str,
        dataset: str,
        source_uri: str,
        license_class: str,
        redistribution_allowed: bool,
        transform_version: str = "point_in_time_equity_csv_v1",
    ) -> None:
        self._path = Path(path).expanduser().resolve()
        if not self._path.is_file():
            raise FileNotFoundError(self._path)
        if security.asset_class not in {AssetClass.EQUITY, AssetClass.ETF, AssetClass.INDEX}:
            raise ResearchIngestionError(
                "equity CSV ingest requires equity, ETF, or index identity"
            )
        self._security = security
        self._provider = provider.strip()
        self._dataset = dataset.strip()
        self._source_uri = source_uri.strip()
        self._license_class = license_class
        self._redistribution_allowed = redistribution_allowed
        self._transform_version = _identifier(transform_version, "transform_version")
        if not all((self._provider, self._dataset, self._source_uri)):
            raise ResearchIngestionError("equity CSV provenance fields must not be blank")
        self._raw_sha256 = _digest_file(self._path)

    def stream(self) -> Iterable[EquityBarRecord]:
        with self._path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = frozenset(reader.fieldnames or ())
            missing = sorted(self._required_columns - fieldnames)
            if missing:
                raise ResearchIngestionError(
                    f"point-in-time equity CSV is missing required columns: {', '.join(missing)}"
                )
            adjusted_seen: bool | None = None
            total_return_seen: bool | None = None
            previous_event_at: datetime | None = None
            for source_sequence, row in enumerate(reader):
                event_at = _timestamp(_required_row_value(row, "event_at"), "event_at")
                if previous_event_at is not None and event_at <= previous_event_at:
                    raise ResearchIngestionError(
                        "equity bars must have strictly increasing event_at values"
                    )
                previous_event_at = event_at
                adjusted_close = _optional_decimal(row.get("adjusted_close"), "adjusted_close")
                total_return_factor = _optional_decimal(
                    row.get("total_return_factor"), "total_return_factor"
                )
                adjusted_seen = _consistent_presence(
                    adjusted_seen, adjusted_close is not None, "adjusted_close"
                )
                total_return_seen = _consistent_presence(
                    total_return_seen, total_return_factor is not None, "total_return_factor"
                )
                query_sha256 = _query_hash(
                    {
                        "path_name": self._path.name,
                        "security": self._security.instrument_id,
                        "source_sequence": source_sequence,
                    }
                )
                yield EquityBarRecord(
                    identity=RecordIdentity(
                        record_id=(
                            f"equity.bar:{self._security.instrument_id.lower()}:"
                            f"{event_at.strftime('%Y%m%dT%H%M%SZ')}:1d"
                        )
                    ),
                    security=self._security,
                    availability=DataAvailability(
                        event_at=event_at,
                        published_at=_optional_timestamp(row.get("published_at"), "published_at"),
                        available_at=_timestamp(
                            _required_row_value(row, "available_at"), "available_at"
                        ),
                        captured_at=_timestamp(
                            _required_row_value(row, "captured_at"), "captured_at"
                        ),
                    ),
                    provenance=DataProvenance(
                        provider=self._provider,
                        dataset=self._dataset,
                        provider_schema_version=_required_row_value(
                            row, "provider_schema_version", default="unknown"
                        ),
                        source_uri=self._source_uri,
                        license_class=self._license_class,
                        redistribution_allowed=self._redistribution_allowed,
                        raw_sha256=self._raw_sha256,
                        query_sha256=query_sha256,
                        transform_version=self._transform_version,
                        source_sequence=source_sequence,
                    ),
                    interval=_required_row_value(row, "interval", default="1d"),
                    session=_required_row_value(row, "session", default="regular"),
                    open=_decimal(_required_row_value(row, "open"), "open"),
                    high=_decimal(_required_row_value(row, "high"), "high"),
                    low=_decimal(_required_row_value(row, "low"), "low"),
                    close=_decimal(_required_row_value(row, "close"), "close"),
                    volume=_decimal(_required_row_value(row, "volume"), "volume"),
                    finality=BarFinality(_required_row_value(row, "finality")),
                    adjusted_close=adjusted_close,
                    split_adjustment_factor=_optional_decimal(
                        row.get("split_adjustment_factor"), "split_adjustment_factor"
                    ),
                    total_return_factor=total_return_factor,
                )


class PointInTimeCorporateActionCsvIngestor:
    """Read local corporate actions only with explicit announcement/availability timing."""

    _required_columns = frozenset(
        {
            "event_at",
            "available_at",
            "captured_at",
            "effective_at",
            "action_type",
            "status",
        }
    )

    def __init__(
        self,
        *,
        path: str | Path,
        security: SecurityIdentity,
        provider: str,
        dataset: str,
        source_uri: str,
        license_class: str,
        redistribution_allowed: bool,
        transform_version: str = "point_in_time_corporate_actions_v1",
    ) -> None:
        self._path = Path(path).expanduser().resolve()
        if not self._path.is_file():
            raise FileNotFoundError(self._path)
        self._security = security
        self._provider = provider.strip()
        self._dataset = dataset.strip()
        self._source_uri = source_uri.strip()
        self._license_class = license_class
        self._redistribution_allowed = redistribution_allowed
        self._transform_version = _identifier(transform_version, "transform_version")
        if not all((self._provider, self._dataset, self._source_uri)):
            raise ResearchIngestionError("corporate-action provenance fields must not be blank")
        self._raw_sha256 = _digest_file(self._path)

    def stream(self) -> Iterable[CorporateActionRecord]:
        with self._path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = frozenset(reader.fieldnames or ())
            missing = sorted(self._required_columns - fieldnames)
            if missing:
                raise ResearchIngestionError(
                    "point-in-time corporate-action CSV is missing required columns: "
                    f"{', '.join(missing)}"
                )
            for source_sequence, row in enumerate(reader):
                event_at = _timestamp(_required_row_value(row, "event_at"), "event_at")
                action_type = CorporateActionType(_required_row_value(row, "action_type"))
                action_id = _required_row_value(
                    row,
                    "action_id",
                    default=f"action:{self._security.instrument_id.lower()}:{source_sequence:08d}",
                )
                query_sha256 = _query_hash(
                    {
                        "path_name": self._path.name,
                        "security": self._security.instrument_id,
                        "source_sequence": source_sequence,
                    }
                )
                yield CorporateActionRecord(
                    identity=RecordIdentity(record_id=action_id),
                    security=self._security,
                    availability=DataAvailability(
                        event_at=event_at,
                        published_at=_optional_timestamp(row.get("published_at"), "published_at"),
                        available_at=_timestamp(
                            _required_row_value(row, "available_at"), "available_at"
                        ),
                        captured_at=_timestamp(
                            _required_row_value(row, "captured_at"), "captured_at"
                        ),
                    ),
                    provenance=DataProvenance(
                        provider=self._provider,
                        dataset=self._dataset,
                        provider_schema_version=_required_row_value(
                            row, "provider_schema_version", default="unknown"
                        ),
                        source_uri=self._source_uri,
                        license_class=self._license_class,
                        redistribution_allowed=self._redistribution_allowed,
                        raw_sha256=self._raw_sha256,
                        query_sha256=query_sha256,
                        transform_version=self._transform_version,
                        source_sequence=source_sequence,
                    ),
                    action_type=action_type,
                    status=CorporateActionStatus(_required_row_value(row, "status")),
                    effective_at=_timestamp(
                        _required_row_value(row, "effective_at"), "effective_at"
                    ),
                    cash_amount=_optional_decimal(row.get("cash_amount"), "cash_amount"),
                    currency=_optional_string(row.get("currency")),
                    ratio_numerator=_optional_decimal(
                        row.get("ratio_numerator"), "ratio_numerator"
                    ),
                    ratio_denominator=_optional_decimal(
                        row.get("ratio_denominator"), "ratio_denominator"
                    ),
                    ex_date=_optional_date(row.get("ex_date"), "ex_date"),
                    record_date=_optional_date(row.get("record_date"), "record_date"),
                    payable_date=_optional_date(row.get("payable_date"), "payable_date"),
                )


def _system_now() -> datetime:
    return datetime.now(UTC)


def _validate_path(path: str, allowed_prefixes: frozenset[str]) -> None:
    if not path.startswith("/") or ".." in path or "?" in path or "://" in path:
        raise ResearchIngestionError("research request path is unsafe")
    if not any(path.startswith(prefix) for prefix in allowed_prefixes):
        raise ResearchIngestionError("research request path is outside the approved origin scope")


def _cik(value: str) -> str:
    normalized = value.strip()
    if len(normalized) != 10 or not normalized.isdigit():
        raise ResearchIngestionError("CIK must be a 10-digit identifier")
    return normalized


def _identifier(value: str, field_name: str) -> str:
    normalized = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if not 3 <= len(normalized) <= 200 or any(character not in allowed for character in normalized):
        raise ResearchIngestionError(f"{field_name} is invalid")
    return normalized


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ResearchIngestionError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _json_mapping(body: bytes, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResearchIngestionError(f"{label} response is not valid JSON") from exc
    return _mapping(value, label)


def _mapping(value: object | None, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ResearchIngestionError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _list(value: object | None, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ResearchIngestionError(f"{label} must be an array")
    return cast(list[object], value)


def _required_string(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ResearchIngestionError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_string(value: object | None) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ResearchIngestionError("optional string is invalid")
    return value.strip()


def _optional_int(value: object | None, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ResearchIngestionError(f"{field_name} must be integer when present")
    try:
        result = int(cast(str | int, value))
    except (TypeError, ValueError) as exc:
        raise ResearchIngestionError(f"{field_name} must be integer when present") from exc
    return result


def _decimal(value: object | None, field_name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ResearchIngestionError(f"{field_name} must be decimal-compatible")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ResearchIngestionError(f"{field_name} must be decimal-compatible") from exc
    if not result.is_finite():
        raise ResearchIngestionError(f"{field_name} must be finite")
    return result


def _optional_decimal(value: object | None, field_name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    return _decimal(value, field_name)


def _date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ResearchIngestionError(f"{field_name} must use ISO date format") from exc


def _treasury_date(value: str, field_name: str) -> date:
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise ResearchIngestionError(f"{field_name} must use an accepted archive date format")


def _optional_date(value: object | None, field_name: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ResearchIngestionError(f"{field_name} must use ISO date format")
    return _date(value, field_name)


def _timestamp(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchIngestionError(f"{field_name} must use ISO datetime format") from exc
    return _utc(parsed, field_name)


def _optional_timestamp(value: object | None, field_name: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ResearchIngestionError(f"{field_name} must use ISO datetime format")
    return _timestamp(value, field_name)


def _required_row_value(
    row: Mapping[str, str | None], field_name: str, *, default: str | None = None
) -> str:
    value = row.get(field_name)
    if value is None or not value.strip():
        if default is not None:
            return default
        raise ResearchIngestionError(f"CSV column {field_name} must be non-empty")
    return value.strip()


def _consistent_presence(previous: bool | None, current: bool, field_name: str) -> bool:
    if previous is not None and previous != current:
        raise ResearchIngestionError(f"{field_name} must be all-or-none across a CSV snapshot")
    return current


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _query_hash(value: Mapping[str, object]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _acceptance_by_accession(payload: Mapping[str, object]) -> dict[str, datetime]:
    filings = _mapping(payload.get("filings"), "SEC filings")
    recent = _mapping(filings.get("recent"), "SEC recent filings")
    accessions = _list(recent.get("accessionNumber"), "SEC accession numbers")
    accepted_values = _list(recent.get("acceptanceDateTime"), "SEC acceptance times")
    if len(accessions) != len(accepted_values):
        raise ResearchIngestionError("SEC filing accessions and acceptance times are misaligned")
    result: dict[str, datetime] = {}
    for accession, accepted_at in zip(accessions, accepted_values, strict=True):
        if not isinstance(accession, str) or not isinstance(accepted_at, str):
            raise ResearchIngestionError("SEC filing accession or acceptance time is invalid")
        result[accession] = _timestamp(accepted_at, "SEC acceptanceDateTime")
    return result
