from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, cast

from quantum_trader.domain.research_data import DataProvenance


class ResearchSnapshotError(ValueError):
    """Raised when a normalized research snapshot cannot be sealed safely."""


class ResearchSnapshotWriter:
    """Write immutable canonical JSONL sources and a checksummed snapshot manifest."""

    def __init__(
        self,
        *,
        output_directory: str | Path,
        snapshot_id: str,
        created_at: datetime,
        decision_cutoff_at: datetime,
        code_commit: str,
        environment_lock_sha256: str,
        schema_manifest_sha256: str,
        holdout_boundaries: tuple[dict[str, object], ...],
        protocol_id: str | None = None,
        experiment_ledger_head_sha256: str | None = None,
    ) -> None:
        self._output_directory = Path(output_directory).expanduser().resolve()
        self._snapshot_id = _identifier(snapshot_id, "snapshot_id")
        self._created_at = _timestamp(created_at, "created_at")
        self._decision_cutoff_at = _timestamp(decision_cutoff_at, "decision_cutoff_at")
        if self._decision_cutoff_at > self._created_at:
            raise ResearchSnapshotError("decision_cutoff_at cannot follow created_at")
        self._code_commit = _sha256_like_commit(code_commit)
        self._environment_lock_sha256 = _sha256(environment_lock_sha256, "environment_lock_sha256")
        self._schema_manifest_sha256 = _sha256(schema_manifest_sha256, "schema_manifest_sha256")
        self._holdout_boundaries = tuple(_normalize_holdout(item) for item in holdout_boundaries)
        self._protocol_id = protocol_id.strip() if protocol_id else None
        self._ledger_head = (
            _sha256(experiment_ledger_head_sha256, "experiment_ledger_head_sha256")
            if experiment_ledger_head_sha256
            else None
        )
        self._sources: list[dict[str, object]] = []
        self._written_source_ids: set[str] = set()
        self._initialized_output_directory = False

    def add_records(
        self,
        *,
        source_id: str,
        contract_schema_id: str,
        provenance: DataProvenance,
        records: tuple[object, ...],
    ) -> Path:
        normalized_source_id = _identifier(source_id, "source_id")
        if normalized_source_id in self._written_source_ids:
            raise ResearchSnapshotError("source_id may be written only once")
        if not contract_schema_id.startswith("qtpro.") or not contract_schema_id.endswith(".v1"):
            raise ResearchSnapshotError("contract_schema_id is not recognized")
        if not records:
            raise ResearchSnapshotError(
                "snapshot sources must retain at least one normalized record"
            )
        self._prepare_output_directory()
        normalized_records = [
            _record_to_mapping(record, contract_schema_id=contract_schema_id) for record in records
        ]
        event_times = [
            _nested_timestamp(record, "availability", "event_at") for record in normalized_records
        ]
        available_times = [
            _nested_timestamp(record, "availability", "available_at")
            for record in normalized_records
        ]
        if max(available_times) > self._decision_cutoff_at:
            raise ResearchSnapshotError(
                "snapshot source contains records unavailable at the declared decision cutoff"
            )
        source_path = self._output_directory / "sources" / f"{normalized_source_id}.jsonl"
        source_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if source_path.exists():
            raise ResearchSnapshotError("snapshot source path already exists")
        payload = "".join(
            f"{_canonical_json(record)}\n"
            for record in sorted(normalized_records, key=_record_sort_key)
        )
        source_path.write_text(payload, encoding="utf-8", newline="\n")
        source_path.chmod(0o600)
        normalized_sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self._sources.append(
            {
                "source_id": normalized_source_id,
                "provider": provenance.provider,
                "dataset": provenance.dataset,
                "provider_schema_version": provenance.provider_schema_version,
                "source_uri": provenance.source_uri,
                "license_class": provenance.license_class,
                "redistribution_allowed": provenance.redistribution_allowed,
                "query_sha256": provenance.query_sha256,
                "raw_sha256": provenance.raw_sha256,
                "normalized_sha256": normalized_sha256,
                "record_count": len(normalized_records),
                "contract_schema_id": contract_schema_id,
                "minimum_event_at": _timestamp_text(min(event_times)),
                "maximum_event_at": _timestamp_text(max(event_times)),
                "maximum_available_at": _timestamp_text(max(available_times)),
                "normalization_version": provenance.transform_version,
                "missingness_summary_sha256": None,
            }
        )
        self._written_source_ids.add(normalized_source_id)
        return source_path

    def seal(self) -> Path:
        if not self._sources:
            raise ResearchSnapshotError("cannot seal a snapshot without sources")
        self._prepare_output_directory()
        manifest_path = self._output_directory / "manifest.json"
        if manifest_path.exists():
            raise ResearchSnapshotError("snapshot manifest already exists")
        manifest: dict[str, object] = {
            "schema_id": "qtpro.data_snapshot_manifest.v1",
            "snapshot_id": self._snapshot_id,
            "created_at": _timestamp_text(self._created_at),
            "decision_cutoff_at": _timestamp_text(self._decision_cutoff_at),
            "protocol_id": self._protocol_id,
            "experiment_ledger_head_sha256": self._ledger_head,
            "code_commit": self._code_commit,
            "environment_lock_sha256": self._environment_lock_sha256,
            "schema_manifest_sha256": self._schema_manifest_sha256,
            "sources": sorted(self._sources, key=lambda item: str(item["source_id"])),
            "holdout_boundaries": list(self._holdout_boundaries),
        }
        manifest["manifest_sha256"] = hashlib.sha256(
            _canonical_json(manifest).encode("utf-8")
        ).hexdigest()
        manifest_path.write_text(f"{_canonical_json(manifest)}\n", encoding="utf-8", newline="\n")
        manifest_path.chmod(0o600)
        return manifest_path

    def _prepare_output_directory(self) -> None:
        if self._output_directory.exists():
            if not self._output_directory.is_dir():
                raise ResearchSnapshotError("snapshot output_directory must be a directory")
            if not self._initialized_output_directory and any(self._output_directory.iterdir()):
                raise ResearchSnapshotError(
                    "snapshot output_directory must be a new empty directory"
                )
        else:
            self._output_directory.mkdir(mode=0o700, parents=True)
        self._initialized_output_directory = True


def _record_to_mapping(record: object, *, contract_schema_id: str) -> dict[str, object]:
    if is_dataclass(record) and not isinstance(record, type):
        value = asdict(cast(Any, record))
    elif isinstance(record, dict):
        value = record
    else:
        raise ResearchSnapshotError("snapshot records must be dataclasses or mappings")
    normalized = _normalize_value(value)
    if not isinstance(normalized, dict):
        raise ResearchSnapshotError("normalized record must be an object")
    normalized["schema_id"] = contract_schema_id
    return normalized


def _normalize_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return _timestamp_text(_timestamp(value, "record timestamp"))
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise ResearchSnapshotError(f"cannot canonicalize record value {type(value).__name__}")


def _nested_timestamp(record: dict[str, object], parent: str, field_name: str) -> datetime:
    parent_value = record.get(parent)
    if not isinstance(parent_value, dict):
        raise ResearchSnapshotError(f"record {parent} must be an object")
    value = parent_value.get(field_name)
    if not isinstance(value, str):
        raise ResearchSnapshotError(f"record {parent}.{field_name} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchSnapshotError(f"record {parent}.{field_name} is invalid") from exc
    return _timestamp(parsed, f"record {parent}.{field_name}")


def _record_sort_key(record: dict[str, object]) -> tuple[str, str]:
    identity = record.get("identity")
    if not isinstance(identity, dict):
        raise ResearchSnapshotError("record identity must be an object")
    record_id = identity.get("record_id")
    if not isinstance(record_id, str):
        raise ResearchSnapshotError("record identity.record_id must be a string")
    return (_nested_timestamp(record, "availability", "event_at").isoformat(), record_id)


def _normalize_holdout(value: dict[str, object]) -> dict[str, object]:
    required = {"holdout_id", "start_at", "end_at", "status"}
    if set(value) - {"holdout_id", "start_at", "end_at", "status", "receipt_sha256"}:
        raise ResearchSnapshotError("holdout boundary contains unknown fields")
    if not required.issubset(value):
        raise ResearchSnapshotError("holdout boundary is incomplete")
    holdout_id = value["holdout_id"]
    if not isinstance(holdout_id, str):
        raise ResearchSnapshotError("holdout_id must be a string")
    start_at = _timestamp_from_object(value["start_at"], "holdout start_at")
    end_at = _timestamp_from_object(value["end_at"], "holdout end_at")
    if start_at >= end_at:
        raise ResearchSnapshotError("holdout start_at must precede end_at")
    status = value["status"]
    if status not in {"sealed", "opened_once", "retired"}:
        raise ResearchSnapshotError("holdout status is invalid")
    receipt = value.get("receipt_sha256")
    if receipt is not None and not isinstance(receipt, str):
        raise ResearchSnapshotError("holdout receipt_sha256 must be string or null")
    return {
        "holdout_id": _identifier(holdout_id, "holdout_id"),
        "start_at": _timestamp_text(start_at),
        "end_at": _timestamp_text(end_at),
        "status": status,
        "receipt_sha256": _sha256(receipt, "receipt_sha256") if receipt else None,
    }


def _timestamp_from_object(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return _timestamp(value, field_name)
    if isinstance(value, str):
        try:
            return _timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")), field_name)
        except ValueError as exc:
            raise ResearchSnapshotError(f"{field_name} is invalid") from exc
    raise ResearchSnapshotError(f"{field_name} must be a timestamp")


def _timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ResearchSnapshotError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _identifier(value: str, field_name: str) -> str:
    normalized = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
    if not 8 <= len(normalized) <= 127 or any(character not in allowed for character in normalized):
        raise ResearchSnapshotError(f"{field_name} is invalid")
    return normalized


def _sha256(value: str, field_name: str) -> str:
    normalized = value.strip()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ResearchSnapshotError(f"{field_name} must be a lowercase SHA-256")
    return normalized


def _sha256_like_commit(value: str) -> str:
    normalized = value.strip()
    if len(normalized) != 40 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ResearchSnapshotError("code_commit must be a lowercase 40-character commit hash")
    return normalized


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ResearchSnapshotError("snapshot value cannot be canonicalized") from exc
