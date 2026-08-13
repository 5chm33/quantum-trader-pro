from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantum_trader.adapters.research_snapshot import (
    ResearchSnapshotError,
    ResearchSnapshotWriter,
)
from quantum_trader.domain.research_data import (
    AssetClass,
    BarFinality,
    DataAvailability,
    DataProvenance,
    EquityBarRecord,
    RecordIdentity,
    SecurityIdentity,
)

_DIGEST = "a" * 64
_COMMIT = "b" * 40


def _bar(*, available_at: datetime) -> EquityBarRecord:
    event_at = available_at - timedelta(minutes=1)
    return EquityBarRecord(
        identity=RecordIdentity(record_id="equity.bar:us0378331005:20240102t210000z:1d"),
        security=SecurityIdentity(
            instrument_id="US0378331005",
            asset_class=AssetClass.EQUITY,
            currency="USD",
            symbol="AAPL",
            cik="0000320193",
        ),
        availability=DataAvailability(
            event_at=event_at,
            available_at=available_at,
            captured_at=available_at + timedelta(minutes=1),
        ),
        provenance=DataProvenance(
            provider="fixture_provider",
            dataset="equity_bars",
            provider_schema_version="v1",
            source_uri="https://example.test/aapl.csv",
            license_class="synthetic",
            redistribution_allowed=True,
            raw_sha256=_DIGEST,
            query_sha256="c" * 64,
            transform_version="fixture_equity_v1",
        ),
        interval="1d",
        session="regular",
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("1000"),
        finality=BarFinality.FINAL,
        adjusted_close=Decimal("101"),
        total_return_factor=Decimal("1.01"),
    )


def _writer(path: Path, *, cutoff: datetime) -> ResearchSnapshotWriter:
    return ResearchSnapshotWriter(
        output_directory=path,
        snapshot_id="snapshot-v1",
        created_at=cutoff + timedelta(minutes=1),
        decision_cutoff_at=cutoff,
        code_commit=_COMMIT,
        environment_lock_sha256="d" * 64,
        schema_manifest_sha256="e" * 64,
        holdout_boundaries=(
            {
                "holdout_id": "future-holdout",
                "start_at": "2025-01-01T00:00:00Z",
                "end_at": "2025-12-31T23:59:59Z",
                "status": "sealed",
                "receipt_sha256": None,
            },
        ),
        protocol_id="strategy-research-v1",
        experiment_ledger_head_sha256="f" * 64,
    )


def test_snapshot_writer_seals_canonical_source_and_manifest(tmp_path: Path) -> None:
    cutoff = datetime(2024, 1, 2, 22, 0, tzinfo=UTC)
    writer = _writer(tmp_path / "snapshot", cutoff=cutoff)
    bar = _bar(available_at=datetime(2024, 1, 2, 21, 1, tzinfo=UTC))

    source_path = writer.add_records(
        source_id="equity-aapl",
        contract_schema_id="qtpro.equity_bar.v1",
        provenance=bar.provenance,
        records=(bar,),
    )
    manifest_path = writer.seal()

    source_record = json.loads(source_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_without_digest = {
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }
    expected_digest = hashlib.sha256(
        json.dumps(
            manifest_without_digest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    assert source_record["schema_id"] == "qtpro.equity_bar.v1"
    assert source_record["close"] == "101"
    assert manifest["manifest_sha256"] == expected_digest
    assert manifest["sources"][0]["record_count"] == 1
    assert manifest["sources"][0]["maximum_available_at"] == "2024-01-02T21:01:00Z"
    assert source_path.stat().st_mode & 0o077 == 0
    assert manifest_path.stat().st_mode & 0o077 == 0


def test_snapshot_writer_rejects_future_evidence_and_duplicate_source(tmp_path: Path) -> None:
    cutoff = datetime(2024, 1, 2, 22, 0, tzinfo=UTC)
    writer = _writer(tmp_path / "snapshot", cutoff=cutoff)
    future_bar = _bar(available_at=cutoff + timedelta(seconds=1))

    with pytest.raises(ResearchSnapshotError, match="unavailable"):
        writer.add_records(
            source_id="equity-aapl",
            contract_schema_id="qtpro.equity_bar.v1",
            provenance=future_bar.provenance,
            records=(future_bar,),
        )

    valid_bar = _bar(available_at=cutoff - timedelta(minutes=1))
    writer.add_records(
        source_id="equity-aapl",
        contract_schema_id="qtpro.equity_bar.v1",
        provenance=valid_bar.provenance,
        records=(valid_bar,),
    )
    with pytest.raises(ResearchSnapshotError, match="only once"):
        writer.add_records(
            source_id="equity-aapl",
            contract_schema_id="qtpro.equity_bar.v1",
            provenance=valid_bar.provenance,
            records=(valid_bar,),
        )


def test_snapshot_writer_rejects_reuse_and_invalid_holdout_boundary(tmp_path: Path) -> None:
    cutoff = datetime(2024, 1, 2, 22, 0, tzinfo=UTC)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "unexpected.txt").write_text("x", encoding="utf-8")
    writer = _writer(occupied, cutoff=cutoff)
    bar = _bar(available_at=cutoff - timedelta(minutes=1))

    with pytest.raises(ResearchSnapshotError, match="new empty directory"):
        writer.add_records(
            source_id="equity-aapl",
            contract_schema_id="qtpro.equity_bar.v1",
            provenance=bar.provenance,
            records=(bar,),
        )

    with pytest.raises(ResearchSnapshotError, match="start_at must precede"):
        ResearchSnapshotWriter(
            output_directory=tmp_path / "bad",
            snapshot_id="snapshot-v1",
            created_at=cutoff,
            decision_cutoff_at=cutoff,
            code_commit=_COMMIT,
            environment_lock_sha256="d" * 64,
            schema_manifest_sha256="e" * 64,
            holdout_boundaries=(
                {
                    "holdout_id": "future-holdout",
                    "start_at": "2025-12-31T00:00:00Z",
                    "end_at": "2025-01-01T00:00:00Z",
                    "status": "sealed",
                },
            ),
        )


def test_snapshot_writer_rejects_empty_invalid_and_malformed_sources(tmp_path: Path) -> None:
    cutoff = datetime(2024, 1, 2, 22, 0, tzinfo=UTC)
    writer = _writer(tmp_path / "snapshot", cutoff=cutoff)
    bar = _bar(available_at=cutoff - timedelta(minutes=1))

    with pytest.raises(ResearchSnapshotError, match="without sources"):
        writer.seal()
    with pytest.raises(ResearchSnapshotError, match="contract_schema_id"):
        writer.add_records(
            source_id="equity-aapl",
            contract_schema_id="invalid-contract",
            provenance=bar.provenance,
            records=(bar,),
        )
    with pytest.raises(ResearchSnapshotError, match="at least one"):
        writer.add_records(
            source_id="equity-aapl",
            contract_schema_id="qtpro.equity_bar.v1",
            provenance=bar.provenance,
            records=(),
        )

    writer.add_records(
        source_id="equity-aapl",
        contract_schema_id="qtpro.equity_bar.v1",
        provenance=bar.provenance,
        records=(bar,),
    )
    writer.seal()
    with pytest.raises(ResearchSnapshotError, match="already exists"):
        writer.seal()


def test_snapshot_writer_rejects_malformed_record_and_bad_constructor_values(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2024, 1, 2, 22, 0, tzinfo=UTC)
    bar = _bar(available_at=cutoff - timedelta(minutes=1))
    with pytest.raises(ResearchSnapshotError, match="decision_cutoff"):
        ResearchSnapshotWriter(
            output_directory=tmp_path / "bad-cutoff",
            snapshot_id="snapshot-v1",
            created_at=cutoff,
            decision_cutoff_at=cutoff + timedelta(seconds=1),
            code_commit=_COMMIT,
            environment_lock_sha256="d" * 64,
            schema_manifest_sha256="e" * 64,
            holdout_boundaries=(),
        )
    with pytest.raises(ResearchSnapshotError, match="code_commit"):
        ResearchSnapshotWriter(
            output_directory=tmp_path / "bad-commit",
            snapshot_id="snapshot-v1",
            created_at=cutoff,
            decision_cutoff_at=cutoff,
            code_commit="bad",
            environment_lock_sha256="d" * 64,
            schema_manifest_sha256="e" * 64,
            holdout_boundaries=(),
        )

    writer = _writer(tmp_path / "bad-record", cutoff=cutoff)
    with pytest.raises(ResearchSnapshotError, match="dataclasses or mappings"):
        writer.add_records(
            source_id="equity-aapl",
            contract_schema_id="qtpro.equity_bar.v1",
            provenance=bar.provenance,
            records=(object(),),
        )
    with pytest.raises(ResearchSnapshotError, match="identity"):
        writer.add_records(
            source_id="equity-aapl",
            contract_schema_id="qtpro.equity_bar.v1",
            provenance=bar.provenance,
            records=(
                {
                    "availability": {
                        "event_at": "2024-01-01T00:00:00Z",
                        "available_at": "2024-01-01T00:01:00Z",
                    }
                },
            ),
        )
