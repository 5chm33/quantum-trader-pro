from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum


class ResearchDataError(ValueError):
    """Raised when point-in-time research evidence is incomplete or invalid."""


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    OPTION = "option"
    CASH = "cash"
    RATE = "rate"


class CorporateActionType(StrEnum):
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SPLIT = "split"
    DISTRIBUTION = "distribution"
    RIGHTS = "rights"
    MERGER = "merger"
    SPIN_OFF = "spin_off"
    NEW_LISTING = "new_listing"
    DELISTING = "delisting"
    SUSPENSION = "suspension"
    SYMBOL_CHANGE = "symbol_change"
    OPTION_CONTRACT_ADJUSTMENT = "option_contract_adjustment"


class CorporateActionStatus(StrEnum):
    ANNOUNCED = "announced"
    CONFIRMED = "confirmed"
    EFFECTIVE = "effective"
    CANCELED = "canceled"
    CORRECTED = "corrected"


class CurveType(StrEnum):
    RISK_FREE_PROXY = "risk_free_proxy"
    TREASURY_PAR = "treasury_par"
    OVERNIGHT_INDEX = "overnight_index"
    FUNDING = "funding"
    BORROW = "borrow"


class DayCountConvention(StrEnum):
    ACT_365F = "ACT_365F"
    ACT_360 = "ACT_360"
    ACT_ACT = "ACT_ACT"
    THIRTY_360 = "THIRTY_360"
    OTHER = "OTHER"


class Compounding(StrEnum):
    SIMPLE = "simple"
    ANNUAL = "annual"
    CONTINUOUS = "continuous"


class InstrumentType(StrEnum):
    CASH = "cash"
    TREASURY_BILL = "treasury_bill"
    TREASURY_NOTE = "treasury_note"
    TREASURY_BOND = "treasury_bond"
    OVERNIGHT_INDEX = "overnight_index"
    SWAP = "swap"
    SYNTHETIC = "synthetic"


class BarFinality(StrEnum):
    PRELIMINARY = "preliminary"
    FINAL = "final"
    CORRECTED = "corrected"


def _require_identifier(value: str, field_name: str, *, minimum: int = 3) -> str:
    normalized = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if not minimum <= len(normalized) <= 200 or any(
        character not in allowed for character in normalized
    ):
        raise ResearchDataError(f"{field_name} has an invalid identifier")
    return normalized


def _require_sha256(value: str, field_name: str) -> str:
    normalized = value.strip()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ResearchDataError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ResearchDataError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(
    value: Decimal, field_name: str, *, positive: bool = False, nonnegative: bool = False
) -> Decimal:
    if not value.is_finite():
        raise ResearchDataError(f"{field_name} must be finite")
    if positive and value <= 0:
        raise ResearchDataError(f"{field_name} must be positive")
    if nonnegative and value < 0:
        raise ResearchDataError(f"{field_name} must be nonnegative")
    return value


@dataclass(frozen=True, slots=True)
class DataAvailability:
    """Retained causal timestamps for one research record."""

    event_at: datetime
    available_at: datetime
    captured_at: datetime
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        event_at = _utc(self.event_at, "event_at")
        available_at = _utc(self.available_at, "available_at")
        captured_at = _utc(self.captured_at, "captured_at")
        published_at = _utc(self.published_at, "published_at") if self.published_at else None
        if available_at < event_at:
            raise ResearchDataError("available_at cannot precede event_at")
        if published_at is not None and available_at < published_at:
            raise ResearchDataError("available_at cannot precede published_at")
        if captured_at < available_at:
            raise ResearchDataError("captured_at cannot precede available_at")
        object.__setattr__(self, "event_at", event_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "published_at", published_at)


@dataclass(frozen=True, slots=True)
class DataProvenance:
    """Content, query, license, and transformation receipt for a source payload."""

    provider: str
    dataset: str
    provider_schema_version: str
    source_uri: str
    license_class: str
    redistribution_allowed: bool
    raw_sha256: str
    query_sha256: str
    transform_version: str
    source_sequence: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "provider",
            "dataset",
            "provider_schema_version",
            "source_uri",
            "transform_version",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ResearchDataError(f"{field_name} must not be empty")
        if self.license_class not in {
            "open",
            "licensed_nonredistributable",
            "private",
            "synthetic",
        }:
            raise ResearchDataError("license_class is not recognized")
        if self.source_sequence is not None and self.source_sequence < 0:
            raise ResearchDataError("source_sequence must be nonnegative")
        object.__setattr__(self, "raw_sha256", _require_sha256(self.raw_sha256, "raw_sha256"))
        object.__setattr__(self, "query_sha256", _require_sha256(self.query_sha256, "query_sha256"))


@dataclass(frozen=True, slots=True)
class SecurityIdentity:
    instrument_id: str
    asset_class: AssetClass
    currency: str
    symbol: str | None = None
    exchange: str | None = None
    cik: str | None = None

    def __post_init__(self) -> None:
        instrument_id = _require_identifier(self.instrument_id, "instrument_id", minimum=2).upper()
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ResearchDataError("currency must be a three-letter code")
        if self.symbol is not None and not self.symbol.strip():
            raise ResearchDataError("symbol must not be blank when present")
        if self.cik is not None and (len(self.cik) != 10 or not self.cik.isdigit()):
            raise ResearchDataError("cik must be a 10-digit SEC identifier when present")
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "symbol", self.symbol.strip().upper() if self.symbol else None)
        object.__setattr__(
            self, "exchange", self.exchange.strip().upper() if self.exchange else None
        )


@dataclass(frozen=True, slots=True)
class RecordIdentity:
    record_id: str
    record_version: int = 1
    revision: int = 0
    is_correction: bool = False
    supersedes_record_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "record_id", _require_identifier(self.record_id, "record_id", minimum=8)
        )
        if self.record_version < 1 or self.revision < 0:
            raise ResearchDataError("record_version and revision are invalid")
        if self.supersedes_record_id is not None:
            object.__setattr__(
                self,
                "supersedes_record_id",
                _require_identifier(self.supersedes_record_id, "supersedes_record_id", minimum=8),
            )


@dataclass(frozen=True, slots=True)
class EquityBarRecord:
    identity: RecordIdentity
    security: SecurityIdentity
    availability: DataAvailability
    provenance: DataProvenance
    interval: str
    session: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    finality: BarFinality
    adjusted_close: Decimal | None = None
    split_adjustment_factor: Decimal | None = None
    total_return_factor: Decimal | None = None

    def __post_init__(self) -> None:
        if self.security.asset_class not in {AssetClass.EQUITY, AssetClass.ETF, AssetClass.INDEX}:
            raise ResearchDataError("equity bars require equity, ETF, or index security identity")
        if self.interval not in {"1m", "5m", "15m", "1h", "1d"}:
            raise ResearchDataError("interval is unsupported")
        if self.session not in {"regular", "extended", "auction", "unknown"}:
            raise ResearchDataError("session is unsupported")
        open_price = _finite(self.open, "open", positive=True)
        high = _finite(self.high, "high", positive=True)
        low = _finite(self.low, "low", positive=True)
        close = _finite(self.close, "close", positive=True)
        _finite(self.volume, "volume", nonnegative=True)
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise ResearchDataError("OHLC prices are internally inconsistent")
        for field_name in ("adjusted_close", "split_adjustment_factor", "total_return_factor"):
            value = getattr(self, field_name)
            if value is not None:
                _finite(value, field_name, positive=True)


@dataclass(frozen=True, slots=True)
class CorporateActionRecord:
    identity: RecordIdentity
    security: SecurityIdentity
    availability: DataAvailability
    provenance: DataProvenance
    action_type: CorporateActionType
    status: CorporateActionStatus
    effective_at: datetime
    cash_amount: Decimal | None = None
    currency: str | None = None
    ratio_numerator: Decimal | None = None
    ratio_denominator: Decimal | None = None
    ex_date: date | None = None
    record_date: date | None = None
    payable_date: date | None = None

    def __post_init__(self) -> None:
        effective_at = _utc(self.effective_at, "effective_at")
        if effective_at < self.availability.event_at:
            raise ResearchDataError("effective_at cannot precede action event_at")
        object.__setattr__(self, "effective_at", effective_at)
        if self.cash_amount is not None:
            _finite(self.cash_amount, "cash_amount", nonnegative=True)
            if self.currency is None:
                raise ResearchDataError("cash corporate actions require a currency")
        if (self.ratio_numerator is None) != (self.ratio_denominator is None):
            raise ResearchDataError("corporate action ratios require numerator and denominator")
        if self.ratio_numerator is not None and self.ratio_denominator is not None:
            _finite(self.ratio_numerator, "ratio_numerator", positive=True)
            _finite(self.ratio_denominator, "ratio_denominator", positive=True)


@dataclass(frozen=True, slots=True)
class FundamentalFactRecord:
    identity: RecordIdentity
    security: SecurityIdentity
    availability: DataAvailability
    provenance: DataProvenance
    accession_number: str
    form: str
    filing_accepted_at: datetime
    report_period_end: date
    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    amendment: bool
    report_period_start: date | None = None
    fiscal_year: int | None = None
    fiscal_period: str = "OTHER"
    decimals: int | None = None
    original_accession_number: str | None = None

    def __post_init__(self) -> None:
        accession = self.accession_number.strip()
        if len(accession) != 20 or accession[10] != "-" or accession[13] != "-":
            raise ResearchDataError("accession_number must use SEC accession syntax")
        accepted_at = _utc(self.filing_accepted_at, "filing_accepted_at")
        if self.availability.available_at < accepted_at:
            raise ResearchDataError("fundamental availability cannot precede filing acceptance")
        if self.security.cik is None:
            raise ResearchDataError("SEC fundamental facts require a security CIK")
        if not self.taxonomy.strip() or not self.concept.strip() or not self.unit.strip():
            raise ResearchDataError("taxonomy, concept, and unit must not be empty")
        if self.report_period_start and self.report_period_start > self.report_period_end:
            raise ResearchDataError("report_period_start cannot follow report_period_end")
        if self.fiscal_year is not None and not 1900 <= self.fiscal_year <= 2200:
            raise ResearchDataError("fiscal_year is out of range")
        if self.fiscal_period not in {"FY", "Q1", "Q2", "Q3", "Q4", "H1", "H2", "TTM", "OTHER"}:
            raise ResearchDataError("fiscal_period is unsupported")
        _finite(self.value, "value")
        object.__setattr__(self, "accession_number", accession)
        object.__setattr__(self, "filing_accepted_at", accepted_at)


@dataclass(frozen=True, slots=True)
class RateNode:
    tenor_days: int
    rate: Decimal
    instrument_type: InstrumentType
    maturity_date: date | None = None
    source_series_id: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.tenor_days <= 36500:
            raise ResearchDataError("tenor_days is out of range")
        _finite(self.rate, "rate")
        if self.source_series_id is not None and not self.source_series_id.strip():
            raise ResearchDataError("source_series_id must not be blank when present")


@dataclass(frozen=True, slots=True)
class RateCurveRecord:
    identity: RecordIdentity
    availability: DataAvailability
    provenance: DataProvenance
    curve_id: str
    currency: str
    curve_type: CurveType
    day_count_convention: DayCountConvention
    compounding: Compounding
    vintage_date: date
    nodes: tuple[RateNode, ...]
    collateral_basis: str | None = None
    interpolation_method: str = "none"

    def __post_init__(self) -> None:
        curve_id = _require_identifier(self.curve_id, "curve_id", minimum=3).lower()
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ResearchDataError("curve currency must be a three-letter code")
        if not self.nodes:
            raise ResearchDataError("rate curve must contain at least one node")
        tenors = tuple(node.tenor_days for node in self.nodes)
        if tuple(sorted(tenors)) != tenors or len(set(tenors)) != len(tenors):
            raise ResearchDataError("rate curve nodes must have unique increasing tenors")
        if self.interpolation_method not in {
            "none",
            "linear_zero",
            "linear_discount",
            "log_discount",
            "other",
        }:
            raise ResearchDataError("interpolation_method is unsupported")
        object.__setattr__(self, "curve_id", curve_id)
        object.__setattr__(self, "currency", currency)
