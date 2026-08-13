"""Hardened append-only SQLite experiment ledger."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from quantum_trader.domain.experiments import (
    ArtifactRecord,
    AttemptRegistration,
    AttemptStage,
    AttemptStatus,
    CampaignRegistration,
    CandidateRegistration,
    HoldoutApproval,
    HoldoutSeal,
    HoldoutStatus,
    JsonValue,
    LedgerEvent,
    LedgerEventType,
    PreregistrationFreeze,
    ResearchState,
    canonical_json,
    ledger_event_hash,
    sha256_json,
    validate_state_transition,
)

_ZERO_HASH = "0" * 64
_SHA256_LENGTH = 64


class ExperimentLedgerError(RuntimeError):
    """The durable research ledger could not complete an operation."""


class ExperimentLedgerConflict(ExperimentLedgerError):
    """An immutable identity or state transition conflicted with retained evidence."""


class ExperimentLedgerIntegrityError(ExperimentLedgerError):
    """Retained research evidence failed an integrity check."""


class SQLiteExperimentLedger:
    """Full-sync append-only experiment evidence with projection tables."""

    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("experiment-ledger database path must be absolute")
        _prepare_secure_database_path(path)
        self._path = path
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            path,
            isolation_level=None,
        )
        connection = self._require_connection()
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        self._initialize()
        self.verify_integrity()

    def _initialize(self) -> None:
        connection = self._require_connection()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO metadata(key, value)
            VALUES ('schema_version', '1');

            CREATE TABLE IF NOT EXISTS ledger_events (
                sequence INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                campaign_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                previous_event_sha256 TEXT NOT NULL,
                event_sha256 TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS ledger_events_campaign_idx
            ON ledger_events(campaign_id, sequence);
            CREATE INDEX IF NOT EXISTS ledger_events_subject_idx
            ON ledger_events(subject_id, sequence);

            CREATE TABLE IF NOT EXISTS campaigns (
                campaign_id TEXT PRIMARY KEY,
                governance_policy_sha256 TEXT NOT NULL,
                hypothesis_catalog_sha256 TEXT NOT NULL,
                data_contract_manifest_sha256 TEXT NOT NULL,
                baseline_commit TEXT NOT NULL,
                registered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                family_id TEXT NOT NULL,
                candidate_index INTEGER NOT NULL,
                candidate_ceiling INTEGER NOT NULL,
                specification_sha256 TEXT NOT NULL,
                code_commit TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                current_state TEXT NOT NULL,
                state_updated_at TEXT NOT NULL,
                UNIQUE(campaign_id, family_id, candidate_index)
            );

            CREATE TABLE IF NOT EXISTS preregistrations (
                candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id),
                protocol_id TEXT NOT NULL UNIQUE,
                protocol_sha256 TEXT NOT NULL,
                data_snapshot_id TEXT NOT NULL,
                data_snapshot_manifest_sha256 TEXT NOT NULL,
                partition_plan_sha256 TEXT NOT NULL,
                benchmark_set_sha256 TEXT NOT NULL,
                cost_model_set_sha256 TEXT NOT NULL,
                candidate_budget_sha256 TEXT NOT NULL,
                frozen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
                campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                comparison_group_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                protocol_id TEXT NOT NULL,
                data_snapshot_id TEXT NOT NULL,
                partition_id TEXT NOT NULL,
                code_commit TEXT NOT NULL,
                configuration_sha256 TEXT NOT NULL,
                benchmark_set_sha256 TEXT NOT NULL,
                cost_model_sha256 TEXT NOT NULL,
                inference_plan_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                result_summary_sha256 TEXT,
                terminal_reason_code TEXT,
                terminal_reason_sha256 TEXT
            );
            CREATE INDEX IF NOT EXISTS attempts_comparison_idx
            ON attempts(comparison_group_id, attempt_id);

            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                name TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                byte_count INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                role TEXT NOT NULL,
                license_class TEXT NOT NULL,
                retained_at TEXT NOT NULL,
                UNIQUE(attempt_id, name)
            );

            CREATE TABLE IF NOT EXISTS comparison_groups (
                comparison_group_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                opened_at TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                terminal_attempt_manifest_sha256 TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS holdouts (
                holdout_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
                boundary_sha256 TEXT NOT NULL,
                provider_query_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                sealed_at TEXT NOT NULL,
                opened_at TEXT,
                completed_at TEXT,
                approval_id TEXT,
                data_snapshot_id TEXT,
                data_snapshot_manifest_sha256 TEXT,
                result_manifest_sha256 TEXT,
                passed INTEGER CHECK(passed IN (0, 1))
            );

            CREATE TABLE IF NOT EXISTS holdout_approvals (
                approval_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                holdout_id TEXT NOT NULL REFERENCES holdouts(holdout_id),
                acknowledgment_sha256 TEXT NOT NULL,
                conversation_receipt_sha256 TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT
            );
            """
        )
        expected_projection_sha256 = _projection_sha256(connection)
        retained_projection = connection.execute(
            "SELECT value FROM metadata WHERE key = 'projection_sha256'"
        ).fetchone()
        if retained_projection is None:
            projection_rows = sum(len(rows) for rows in _projection_rows(connection).values())
            if projection_rows != 0:
                raise ExperimentLedgerIntegrityError(
                    "existing experiment ledger lacks a projection digest"
                )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('projection_sha256', ?)",
                (expected_projection_sha256,),
            )
        elif str(retained_projection[0]) != expected_projection_sha256:
            raise ExperimentLedgerIntegrityError("experiment-ledger projection digest is invalid")

    def register_campaign(
        self,
        campaign: CampaignRegistration,
        *,
        actor: str,
    ) -> LedgerEvent:
        payload = _campaign_payload(campaign)
        with self._transaction() as connection:
            if _exists(
                connection, "SELECT 1 FROM campaigns WHERE campaign_id = ?", campaign.campaign_id
            ):
                raise ExperimentLedgerConflict("campaign identity is already registered")
            connection.execute(
                """
                INSERT INTO campaigns(
                    campaign_id, governance_policy_sha256, hypothesis_catalog_sha256,
                    data_contract_manifest_sha256, baseline_commit, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign.campaign_id,
                    campaign.governance_policy_sha256,
                    campaign.hypothesis_catalog_sha256,
                    campaign.data_contract_manifest_sha256,
                    campaign.baseline_commit,
                    _iso(campaign.registered_at),
                ),
            )
            event = self._append_event(
                connection,
                campaign_id=campaign.campaign_id,
                event_type=LedgerEventType.CAMPAIGN_REGISTERED,
                subject_id=campaign.campaign_id,
                occurred_at=campaign.registered_at,
                actor=actor,
                payload=payload,
            )
        return event

    def register_candidate(
        self,
        candidate: CandidateRegistration,
        *,
        actor: str,
    ) -> LedgerEvent:
        payload = _candidate_payload(candidate)
        with self._transaction() as connection:
            _require_campaign(connection, candidate.campaign_id)
            if _exists(
                connection,
                "SELECT 1 FROM candidates WHERE candidate_id = ?",
                candidate.candidate_id,
            ):
                raise ExperimentLedgerConflict("candidate identity is already registered")
            rows = connection.execute(
                """
                SELECT candidate_ceiling FROM candidates
                WHERE campaign_id = ? AND family_id = ?
                """,
                (candidate.campaign_id, candidate.family_id),
            ).fetchall()
            if rows and any(
                int(row["candidate_ceiling"]) != candidate.candidate_ceiling for row in rows
            ):
                raise ExperimentLedgerConflict(
                    "candidate family ceiling changed after registration"
                )
            if len(rows) >= candidate.candidate_ceiling:
                raise ExperimentLedgerConflict("candidate family budget is exhausted")
            try:
                connection.execute(
                    """
                    INSERT INTO candidates(
                        candidate_id, campaign_id, family_id, candidate_index,
                        candidate_ceiling, specification_sha256, code_commit,
                        registered_at, current_state, state_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.candidate_id,
                        candidate.campaign_id,
                        candidate.family_id,
                        candidate.candidate_index,
                        candidate.candidate_ceiling,
                        candidate.specification_sha256,
                        candidate.code_commit,
                        _iso(candidate.registered_at),
                        ResearchState.HYPOTHESIS.value,
                        _iso(candidate.registered_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ExperimentLedgerConflict(
                    "candidate index or immutable identity is already registered"
                ) from exc
            event = self._append_event(
                connection,
                campaign_id=candidate.campaign_id,
                event_type=LedgerEventType.CANDIDATE_REGISTERED,
                subject_id=candidate.candidate_id,
                occurred_at=candidate.registered_at,
                actor=actor,
                payload=payload,
            )
        return event

    def freeze_preregistration(
        self,
        freeze: PreregistrationFreeze,
        *,
        actor: str,
    ) -> LedgerEvent:
        payload = _freeze_payload(freeze)
        with self._transaction() as connection:
            candidate = _candidate_row(connection, freeze.candidate_id)
            if ResearchState(str(candidate["current_state"])) is not ResearchState.DEVELOPMENT:
                raise ExperimentLedgerConflict(
                    "preregistration can freeze only while a candidate is in development"
                )
            if _exists(
                connection,
                "SELECT 1 FROM preregistrations WHERE candidate_id = ?",
                freeze.candidate_id,
            ):
                raise ExperimentLedgerConflict("candidate preregistration is already frozen")
            connection.execute(
                """
                INSERT INTO preregistrations(
                    candidate_id, protocol_id, protocol_sha256, data_snapshot_id,
                    data_snapshot_manifest_sha256, partition_plan_sha256,
                    benchmark_set_sha256, cost_model_set_sha256,
                    candidate_budget_sha256, frozen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    freeze.candidate_id,
                    freeze.protocol_id,
                    freeze.protocol_sha256,
                    freeze.data_snapshot_id,
                    freeze.data_snapshot_manifest_sha256,
                    freeze.partition_plan_sha256,
                    freeze.benchmark_set_sha256,
                    freeze.cost_model_set_sha256,
                    freeze.candidate_budget_sha256,
                    _iso(freeze.frozen_at),
                ),
            )
            event = self._append_event(
                connection,
                campaign_id=str(candidate["campaign_id"]),
                event_type=LedgerEventType.PREREGISTRATION_FROZEN,
                subject_id=freeze.candidate_id,
                occurred_at=freeze.frozen_at,
                actor=actor,
                payload=payload,
            )
        return event

    def register_attempt(
        self,
        attempt: AttemptRegistration,
        *,
        actor: str,
    ) -> LedgerEvent:
        payload = _attempt_payload(attempt)
        with self._transaction() as connection:
            candidate = _candidate_row(connection, attempt.candidate_id)
            preregistration = connection.execute(
                "SELECT * FROM preregistrations WHERE candidate_id = ?",
                (attempt.candidate_id,),
            ).fetchone()
            if preregistration is None:
                raise ExperimentLedgerConflict("attempt requires a frozen preregistration")
            if attempt.protocol_id != str(preregistration["protocol_id"]):
                raise ExperimentLedgerConflict("attempt protocol differs from preregistration")
            if attempt.stage not in {
                AttemptStage.LOCKED_HOLDOUT,
                AttemptStage.SHADOW,
                AttemptStage.PAPER,
            } and attempt.data_snapshot_id != str(preregistration["data_snapshot_id"]):
                raise ExperimentLedgerConflict("attempt data snapshot differs from preregistration")
            state = ResearchState(str(candidate["current_state"]))
            _validate_attempt_stage(connection, attempt, state)
            if _exists(
                connection, "SELECT 1 FROM attempts WHERE attempt_id = ?", attempt.attempt_id
            ):
                raise ExperimentLedgerConflict("attempt identity is already registered")
            connection.execute(
                """
                INSERT INTO attempts(
                    attempt_id, candidate_id, campaign_id, comparison_group_id,
                    stage, protocol_id, data_snapshot_id, partition_id, code_commit,
                    configuration_sha256, benchmark_set_sha256, cost_model_sha256,
                    inference_plan_sha256, status, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.candidate_id,
                    candidate["campaign_id"],
                    attempt.comparison_group_id,
                    attempt.stage.value,
                    attempt.protocol_id,
                    attempt.data_snapshot_id,
                    attempt.partition_id,
                    attempt.code_commit,
                    attempt.configuration_sha256,
                    attempt.benchmark_set_sha256,
                    attempt.cost_model_sha256,
                    attempt.inference_plan_sha256,
                    AttemptStatus.REGISTERED.value,
                    _iso(attempt.registered_at),
                ),
            )
            event = self._append_event(
                connection,
                campaign_id=str(candidate["campaign_id"]),
                event_type=LedgerEventType.ATTEMPT_REGISTERED,
                subject_id=attempt.attempt_id,
                occurred_at=attempt.registered_at,
                actor=actor,
                payload=payload,
            )
        return event

    def start_attempt(
        self,
        attempt_id: str,
        *,
        actor: str,
        started_at: datetime,
    ) -> LedgerEvent:
        _aware(started_at, "started_at")
        with self._transaction() as connection:
            attempt = _attempt_row(connection, attempt_id)
            if AttemptStatus(str(attempt["status"])) is not AttemptStatus.REGISTERED:
                raise ExperimentLedgerConflict("only a registered attempt can start")
            if _parse_datetime(str(attempt["registered_at"])) > started_at.astimezone(UTC):
                raise ExperimentLedgerConflict("attempt cannot start before registration")
            connection.execute(
                "UPDATE attempts SET status = ?, started_at = ? WHERE attempt_id = ?",
                (AttemptStatus.STARTED.value, _iso(started_at), attempt_id),
            )
            event = self._append_event(
                connection,
                campaign_id=str(attempt["campaign_id"]),
                event_type=LedgerEventType.ATTEMPT_STARTED,
                subject_id=attempt_id,
                occurred_at=started_at,
                actor=actor,
                payload={"attempt_id": attempt_id},
            )
        return event

    def complete_attempt(
        self,
        attempt_id: str,
        *,
        actor: str,
        completed_at: datetime,
        result_summary_sha256: str,
        artifacts: Sequence[ArtifactRecord],
    ) -> LedgerEvent:
        _aware(completed_at, "completed_at")
        _require_sha256(result_summary_sha256, "result_summary_sha256")
        if not artifacts:
            raise ValueError("a completed attempt must retain at least one artifact")
        if any(artifact.attempt_id != attempt_id for artifact in artifacts):
            raise ValueError("every retained artifact must belong to the completed attempt")
        if len({artifact.artifact_id for artifact in artifacts}) != len(artifacts):
            raise ValueError("artifact IDs must be unique within a completion")
        if len({artifact.name for artifact in artifacts}) != len(artifacts):
            raise ValueError("artifact names must be unique within a completion")
        ordered_artifacts = tuple(
            sorted(artifacts, key=lambda artifact: (artifact.retained_at, artifact.artifact_id))
        )
        if any(artifact.retained_at > completed_at for artifact in ordered_artifacts):
            raise ValueError("artifact retention cannot occur after attempt completion")
        with self._transaction() as connection:
            attempt = _attempt_row(connection, attempt_id)
            if AttemptStatus(str(attempt["status"])) is not AttemptStatus.STARTED:
                raise ExperimentLedgerConflict("only a started attempt can complete")
            started_at = _parse_datetime(str(attempt["started_at"]))
            if completed_at.astimezone(UTC) < started_at:
                raise ExperimentLedgerConflict("attempt cannot complete before it starts")
            if any(
                artifact.retained_at.astimezone(UTC) < started_at for artifact in ordered_artifacts
            ):
                raise ExperimentLedgerConflict(
                    "artifact retention cannot occur before attempt start"
                )
            for artifact in ordered_artifacts:
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        artifact_id, attempt_id, name, sha256, byte_count,
                        media_type, role, license_class, retained_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact.artifact_id,
                        artifact.attempt_id,
                        artifact.name,
                        artifact.sha256,
                        artifact.byte_count,
                        artifact.media_type,
                        artifact.role,
                        artifact.license_class,
                        _iso(artifact.retained_at),
                    ),
                )
                self._append_event(
                    connection,
                    campaign_id=str(attempt["campaign_id"]),
                    event_type=LedgerEventType.ARTIFACT_RETAINED,
                    subject_id=artifact.artifact_id,
                    occurred_at=artifact.retained_at,
                    actor=actor,
                    payload=_artifact_payload(artifact),
                )
            connection.execute(
                """
                UPDATE attempts
                SET status = ?, completed_at = ?, result_summary_sha256 = ?
                WHERE attempt_id = ?
                """,
                (
                    AttemptStatus.COMPLETED.value,
                    _iso(completed_at),
                    result_summary_sha256,
                    attempt_id,
                ),
            )
            event = self._append_event(
                connection,
                campaign_id=str(attempt["campaign_id"]),
                event_type=LedgerEventType.ATTEMPT_COMPLETED,
                subject_id=attempt_id,
                occurred_at=completed_at,
                actor=actor,
                payload={
                    "attempt_id": attempt_id,
                    "result_summary_sha256": result_summary_sha256,
                    "artifact_ids": [artifact.artifact_id for artifact in ordered_artifacts],
                },
            )
        return event

    def fail_attempt(
        self,
        attempt_id: str,
        *,
        actor: str,
        failed_at: datetime,
        reason_code: str,
        reason_sha256: str,
    ) -> LedgerEvent:
        return self._terminate_attempt(
            attempt_id,
            actor=actor,
            timestamp=failed_at,
            reason_code=reason_code,
            reason_sha256=reason_sha256,
            target=AttemptStatus.FAILED,
            event_type=LedgerEventType.ATTEMPT_FAILED,
        )

    def abort_attempt(
        self,
        attempt_id: str,
        *,
        actor: str,
        aborted_at: datetime,
        reason_code: str,
        reason_sha256: str,
    ) -> LedgerEvent:
        return self._terminate_attempt(
            attempt_id,
            actor=actor,
            timestamp=aborted_at,
            reason_code=reason_code,
            reason_sha256=reason_sha256,
            target=AttemptStatus.ABORTED,
            event_type=LedgerEventType.ATTEMPT_ABORTED,
        )

    def _terminate_attempt(
        self,
        attempt_id: str,
        *,
        actor: str,
        timestamp: datetime,
        reason_code: str,
        reason_sha256: str,
        target: AttemptStatus,
        event_type: LedgerEventType,
    ) -> LedgerEvent:
        _aware(timestamp, "terminal timestamp")
        _require_sha256(reason_sha256, "reason_sha256")
        code = reason_code.strip()
        if not code or len(code) > 100:
            raise ValueError("reason_code must contain 1 to 100 characters")
        with self._transaction() as connection:
            attempt = _attempt_row(connection, attempt_id)
            current = AttemptStatus(str(attempt["status"]))
            if current not in {AttemptStatus.REGISTERED, AttemptStatus.STARTED}:
                raise ExperimentLedgerConflict("terminal attempt state cannot be replaced")
            earliest = _parse_datetime(str(attempt["registered_at"]))
            if timestamp.astimezone(UTC) < earliest:
                raise ExperimentLedgerConflict("attempt cannot terminate before registration")
            connection.execute(
                """
                UPDATE attempts
                SET status = ?, completed_at = ?, terminal_reason_code = ?,
                    terminal_reason_sha256 = ?
                WHERE attempt_id = ?
                """,
                (target.value, _iso(timestamp), code, reason_sha256, attempt_id),
            )
            event = self._append_event(
                connection,
                campaign_id=str(attempt["campaign_id"]),
                event_type=event_type,
                subject_id=attempt_id,
                occurred_at=timestamp,
                actor=actor,
                payload={
                    "attempt_id": attempt_id,
                    "reason_code": code,
                    "reason_sha256": reason_sha256,
                },
            )
        return event

    def open_comparison(
        self,
        comparison_group_id: str,
        *,
        actor: str,
        opened_at: datetime,
    ) -> LedgerEvent:
        _aware(opened_at, "opened_at")
        with self._transaction() as connection:
            if _exists(
                connection,
                "SELECT 1 FROM comparison_groups WHERE comparison_group_id = ?",
                comparison_group_id,
            ):
                raise ExperimentLedgerConflict("comparison group is already opened")
            rows = connection.execute(
                "SELECT attempt_id, candidate_id, campaign_id, stage, status, "
                "configuration_sha256, completed_at, result_summary_sha256 "
                "FROM attempts WHERE comparison_group_id = ? ORDER BY attempt_id",
                (comparison_group_id,),
            ).fetchall()
            if not rows:
                raise ExperimentLedgerConflict("comparison group has no registered attempts")
            campaign_ids = {str(row["campaign_id"]) for row in rows}
            if len(campaign_ids) != 1:
                raise ExperimentLedgerIntegrityError("comparison group spans multiple campaigns")
            if any(not AttemptStatus(str(row["status"])).terminal for row in rows):
                raise ExperimentLedgerConflict(
                    "candidate comparison is blocked until every assigned attempt is terminal"
                )
            if any(
                row["completed_at"] is None
                or _parse_datetime(str(row["completed_at"])) > opened_at.astimezone(UTC)
                for row in rows
            ):
                raise ExperimentLedgerConflict(
                    "comparison cannot open before every assigned attempt terminates"
                )
            manifest: list[JsonValue] = [
                {
                    "attempt_id": str(row["attempt_id"]),
                    "candidate_id": str(row["candidate_id"]),
                    "stage": str(row["stage"]),
                    "status": str(row["status"]),
                    "configuration_sha256": str(row["configuration_sha256"]),
                    "result_summary_sha256": (
                        str(row["result_summary_sha256"])
                        if row["result_summary_sha256"] is not None
                        else None
                    ),
                }
                for row in rows
            ]
            manifest_sha256 = sha256_json(manifest)
            campaign_id = next(iter(campaign_ids))
            connection.execute(
                """
                INSERT INTO comparison_groups(
                    comparison_group_id, campaign_id, opened_at, attempt_count,
                    terminal_attempt_manifest_sha256
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    comparison_group_id,
                    campaign_id,
                    _iso(opened_at),
                    len(rows),
                    manifest_sha256,
                ),
            )
            event = self._append_event(
                connection,
                campaign_id=campaign_id,
                event_type=LedgerEventType.COMPARISON_OPENED,
                subject_id=comparison_group_id,
                occurred_at=opened_at,
                actor=actor,
                payload={
                    "comparison_group_id": comparison_group_id,
                    "attempt_count": len(rows),
                    "terminal_attempt_manifest_sha256": manifest_sha256,
                },
            )
        return event

    def transition_candidate(
        self,
        candidate_id: str,
        *,
        actor: str,
        target: ResearchState,
        occurred_at: datetime,
        gate_evidence_sha256: str,
    ) -> LedgerEvent:
        _aware(occurred_at, "occurred_at")
        _require_sha256(gate_evidence_sha256, "gate_evidence_sha256")
        with self._transaction() as connection:
            candidate = _candidate_row(connection, candidate_id)
            current = ResearchState(str(candidate["current_state"]))
            validate_state_transition(current, target)
            self._validate_promotion_prerequisites(connection, candidate_id, target)
            connection.execute(
                """
                UPDATE candidates SET current_state = ?, state_updated_at = ?
                WHERE candidate_id = ?
                """,
                (target.value, _iso(occurred_at), candidate_id),
            )
            event = self._append_event(
                connection,
                campaign_id=str(candidate["campaign_id"]),
                event_type=LedgerEventType.CANDIDATE_STATE_CHANGED,
                subject_id=candidate_id,
                occurred_at=occurred_at,
                actor=actor,
                payload={
                    "candidate_id": candidate_id,
                    "from_state": current.value,
                    "to_state": target.value,
                    "gate_evidence_sha256": gate_evidence_sha256,
                },
            )
        return event

    def _validate_promotion_prerequisites(
        self,
        connection: sqlite3.Connection,
        candidate_id: str,
        target: ResearchState,
    ) -> None:
        if target is ResearchState.REJECTED or target is ResearchState.DEVELOPMENT:
            return
        if not _exists(
            connection,
            "SELECT 1 FROM preregistrations WHERE candidate_id = ?",
            candidate_id,
        ):
            raise ExperimentLedgerConflict("promotion requires frozen preregistration")
        rows = connection.execute(
            "SELECT stage, status FROM attempts WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchall()
        statuses = [AttemptStatus(str(row["status"])) for row in rows]
        if any(not status.terminal for status in statuses):
            raise ExperimentLedgerConflict("promotion is blocked by a nonterminal attempt")
        if target is ResearchState.TEST_ELIGIBLE:
            if not any(
                AttemptStage(str(row["stage"]))
                in {AttemptStage.DEVELOPMENT, AttemptStage.VALIDATION}
                and AttemptStatus(str(row["status"])) is AttemptStatus.COMPLETED
                for row in rows
            ):
                raise ExperimentLedgerConflict(
                    "test eligibility requires completed development or validation evidence"
                )
            _require_all_candidate_comparisons_opened(connection, candidate_id)
            return
        if target is ResearchState.HOLDOUT_ELIGIBLE:
            if not rows or any(status is not AttemptStatus.COMPLETED for status in statuses):
                raise ExperimentLedgerConflict(
                    "holdout eligibility requires every registered attempt to complete"
                )
            if not any(AttemptStage(str(row["stage"])) is AttemptStage.TEST for row in rows):
                raise ExperimentLedgerConflict(
                    "holdout eligibility requires completed test evidence"
                )
            _require_all_candidate_comparisons_opened(connection, candidate_id)
            return
        if target is ResearchState.SHADOW_ELIGIBLE:
            passed = connection.execute(
                """
                SELECT 1 FROM holdouts
                WHERE candidate_id = ? AND status = ? AND passed = 1
                LIMIT 1
                """,
                (candidate_id, HoldoutStatus.COMPLETED.value),
            ).fetchone()
            if passed is None:
                raise ExperimentLedgerConflict("shadow eligibility requires a passing holdout")
            return
        required_stage = (
            AttemptStage.SHADOW if target is ResearchState.PAPER_ELIGIBLE else AttemptStage.PAPER
        )
        if not any(
            AttemptStage(str(row["stage"])) is required_stage
            and AttemptStatus(str(row["status"])) is AttemptStatus.COMPLETED
            for row in rows
        ):
            raise ExperimentLedgerConflict(
                f"{target.value} requires a completed {required_stage.value} attempt"
            )

    def seal_holdout(self, seal: HoldoutSeal, *, actor: str) -> LedgerEvent:
        payload: dict[str, JsonValue] = {
            "holdout_id": seal.holdout_id,
            "campaign_id": seal.campaign_id,
            "candidate_id": seal.candidate_id,
            "boundary_sha256": seal.boundary_sha256,
            "provider_query_sha256": seal.provider_query_sha256,
            "bytes_retrieved": seal.bytes_retrieved,
            "sealed_at": _iso(seal.sealed_at),
        }
        with self._transaction() as connection:
            candidate = _candidate_row(connection, seal.candidate_id)
            if str(candidate["campaign_id"]) != seal.campaign_id:
                raise ExperimentLedgerConflict("holdout candidate belongs to another campaign")
            if ResearchState(str(candidate["current_state"])) is not ResearchState.HOLDOUT_ELIGIBLE:
                raise ExperimentLedgerConflict(
                    "holdout sealing requires a holdout-eligible candidate"
                )
            if _exists(connection, "SELECT 1 FROM holdouts WHERE holdout_id = ?", seal.holdout_id):
                raise ExperimentLedgerConflict("holdout identity is already sealed")
            if _exists(
                connection,
                "SELECT 1 FROM holdouts WHERE candidate_id = ?",
                seal.candidate_id,
            ):
                raise ExperimentLedgerConflict(
                    "a candidate can seal only one lockbox in a campaign"
                )
            connection.execute(
                """
                INSERT INTO holdouts(
                    holdout_id, campaign_id, candidate_id, boundary_sha256,
                    provider_query_sha256, status, sealed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seal.holdout_id,
                    seal.campaign_id,
                    seal.candidate_id,
                    seal.boundary_sha256,
                    seal.provider_query_sha256,
                    HoldoutStatus.SEALED.value,
                    _iso(seal.sealed_at),
                ),
            )
            event = self._append_event(
                connection,
                campaign_id=seal.campaign_id,
                event_type=LedgerEventType.HOLDOUT_SEALED,
                subject_id=seal.holdout_id,
                occurred_at=seal.sealed_at,
                actor=actor,
                payload=payload,
            )
        return event

    def approve_holdout(self, approval: HoldoutApproval, *, actor: str) -> LedgerEvent:
        approval.verify(
            campaign_id=approval.campaign_id,
            holdout_id=approval.holdout_id,
            now=approval.approved_at,
        )
        payload: dict[str, JsonValue] = {
            "approval_id": approval.approval_id,
            "campaign_id": approval.campaign_id,
            "holdout_id": approval.holdout_id,
            "acknowledgment_sha256": approval.acknowledgment_sha256,
            "conversation_receipt_sha256": approval.conversation_receipt_sha256,
            "approved_at": _iso(approval.approved_at),
            "expires_at": _iso(approval.expires_at),
        }
        with self._transaction() as connection:
            holdout = _holdout_row(connection, approval.holdout_id)
            if str(holdout["campaign_id"]) != approval.campaign_id:
                raise ExperimentLedgerConflict("holdout approval campaign does not match")
            if HoldoutStatus(str(holdout["status"])) is not HoldoutStatus.SEALED:
                raise ExperimentLedgerConflict("only a sealed holdout can be approved")
            if approval.approved_at.astimezone(UTC) < _parse_datetime(str(holdout["sealed_at"])):
                raise ExperimentLedgerConflict("holdout approval cannot predate sealing")
            try:
                connection.execute(
                    """
                    INSERT INTO holdout_approvals(
                        approval_id, campaign_id, holdout_id,
                        acknowledgment_sha256, conversation_receipt_sha256,
                        approved_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval.approval_id,
                        approval.campaign_id,
                        approval.holdout_id,
                        approval.acknowledgment_sha256,
                        approval.conversation_receipt_sha256,
                        _iso(approval.approved_at),
                        _iso(approval.expires_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ExperimentLedgerConflict("holdout approval was already retained") from exc
            event = self._append_event(
                connection,
                campaign_id=approval.campaign_id,
                event_type=LedgerEventType.HOLDOUT_OPEN_APPROVED,
                subject_id=approval.approval_id,
                occurred_at=approval.approved_at,
                actor=actor,
                payload=payload,
            )
        return event

    def open_holdout(
        self,
        holdout_id: str,
        *,
        actor: str,
        opened_at: datetime,
        approval_id: str,
        data_snapshot_id: str,
        data_snapshot_manifest_sha256: str,
    ) -> LedgerEvent:
        _aware(opened_at, "opened_at")
        _require_identifier(data_snapshot_id, "data_snapshot_id")
        _require_sha256(
            data_snapshot_manifest_sha256,
            "data_snapshot_manifest_sha256",
        )
        with self._transaction() as connection:
            holdout = _holdout_row(connection, holdout_id)
            if HoldoutStatus(str(holdout["status"])) is not HoldoutStatus.SEALED:
                raise ExperimentLedgerConflict("holdout can be opened exactly once")
            candidate = _candidate_row(connection, str(holdout["candidate_id"]))
            if ResearchState(str(candidate["current_state"])) is not ResearchState.HOLDOUT_ELIGIBLE:
                raise ExperimentLedgerConflict("candidate is no longer holdout eligible")
            approval = connection.execute(
                "SELECT * FROM holdout_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if approval is None:
                raise ExperimentLedgerConflict("explicit holdout approval is missing")
            if str(approval["holdout_id"]) != holdout_id:
                raise ExperimentLedgerConflict("approval is bound to another holdout")
            if approval["used_at"] is not None:
                raise ExperimentLedgerConflict("holdout approval was already consumed")
            if opened_at.astimezone(UTC) < _parse_datetime(str(approval["approved_at"])):
                raise ExperimentLedgerConflict("holdout cannot open before approval")
            if opened_at.astimezone(UTC) > _parse_datetime(str(approval["expires_at"])):
                raise ExperimentLedgerConflict("holdout approval expired before use")
            connection.execute(
                "UPDATE holdout_approvals SET used_at = ? WHERE approval_id = ?",
                (_iso(opened_at), approval_id),
            )
            connection.execute(
                """
                UPDATE holdouts
                SET status = ?, opened_at = ?, approval_id = ?,
                    data_snapshot_id = ?, data_snapshot_manifest_sha256 = ?
                WHERE holdout_id = ?
                """,
                (
                    HoldoutStatus.OPENED.value,
                    _iso(opened_at),
                    approval_id,
                    data_snapshot_id,
                    data_snapshot_manifest_sha256,
                    holdout_id,
                ),
            )
            event = self._append_event(
                connection,
                campaign_id=str(holdout["campaign_id"]),
                event_type=LedgerEventType.HOLDOUT_OPENED,
                subject_id=holdout_id,
                occurred_at=opened_at,
                actor=actor,
                payload={
                    "holdout_id": holdout_id,
                    "approval_id": approval_id,
                    "candidate_id": str(holdout["candidate_id"]),
                    "boundary_sha256": str(holdout["boundary_sha256"]),
                    "provider_query_sha256": str(holdout["provider_query_sha256"]),
                    "data_snapshot_id": data_snapshot_id,
                    "data_snapshot_manifest_sha256": data_snapshot_manifest_sha256,
                },
            )
        return event

    def complete_holdout(
        self,
        holdout_id: str,
        *,
        actor: str,
        completed_at: datetime,
        result_manifest_sha256: str,
        passed: bool,
    ) -> LedgerEvent:
        _aware(completed_at, "completed_at")
        _require_sha256(result_manifest_sha256, "result_manifest_sha256")
        with self._transaction() as connection:
            holdout = _holdout_row(connection, holdout_id)
            if HoldoutStatus(str(holdout["status"])) is not HoldoutStatus.OPENED:
                raise ExperimentLedgerConflict("only an opened holdout can complete")
            opened_at = _parse_datetime(str(holdout["opened_at"]))
            if completed_at.astimezone(UTC) < opened_at:
                raise ExperimentLedgerConflict("holdout cannot complete before opening")
            completed_attempt = connection.execute(
                """
                SELECT 1 FROM attempts
                WHERE candidate_id = ? AND stage = ? AND data_snapshot_id = ?
                    AND status = ?
                LIMIT 1
                """,
                (
                    holdout["candidate_id"],
                    AttemptStage.LOCKED_HOLDOUT.value,
                    holdout["data_snapshot_id"],
                    AttemptStatus.COMPLETED.value,
                ),
            ).fetchone()
            if completed_attempt is None:
                raise ExperimentLedgerConflict(
                    "holdout completion requires a completed lockbox attempt"
                )
            connection.execute(
                """
                UPDATE holdouts
                SET status = ?, completed_at = ?, result_manifest_sha256 = ?, passed = ?
                WHERE holdout_id = ?
                """,
                (
                    HoldoutStatus.COMPLETED.value,
                    _iso(completed_at),
                    result_manifest_sha256,
                    int(passed),
                    holdout_id,
                ),
            )
            event = self._append_event(
                connection,
                campaign_id=str(holdout["campaign_id"]),
                event_type=LedgerEventType.HOLDOUT_COMPLETED,
                subject_id=holdout_id,
                occurred_at=completed_at,
                actor=actor,
                payload={
                    "holdout_id": holdout_id,
                    "candidate_id": str(holdout["candidate_id"]),
                    "result_manifest_sha256": result_manifest_sha256,
                    "passed": passed,
                },
            )
        return event

    def current_candidate_state(self, candidate_id: str) -> ResearchState:
        row = _candidate_row(self._require_connection(), candidate_id)
        return ResearchState(str(row["current_state"]))

    def attempt_status(self, attempt_id: str) -> AttemptStatus:
        row = _attempt_row(self._require_connection(), attempt_id)
        return AttemptStatus(str(row["status"]))

    def iter_events(self, *, campaign_id: str | None = None) -> Iterator[LedgerEvent]:
        connection = self._require_connection()
        if campaign_id is None:
            rows = connection.execute("SELECT * FROM ledger_events ORDER BY sequence").fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM ledger_events WHERE campaign_id = ? ORDER BY sequence",
                (campaign_id,),
            ).fetchall()
        for row in rows:
            yield _event_from_row(row)

    def verify_integrity(self) -> None:
        connection = self._require_connection()
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check != "ok":
            raise ExperimentLedgerIntegrityError("SQLite quick_check did not pass")
        schema_version = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if schema_version is None or str(schema_version[0]) != "1":
            raise ExperimentLedgerIntegrityError("experiment ledger schema version is invalid")
        expected_previous = _ZERO_HASH
        expected_sequence = 1
        for row in connection.execute("SELECT * FROM ledger_events ORDER BY sequence"):
            event = _event_from_row(row)
            if event.sequence != expected_sequence:
                raise ExperimentLedgerIntegrityError("ledger event sequence is not contiguous")
            if event.previous_event_sha256 != expected_previous:
                raise ExperimentLedgerIntegrityError("ledger previous-event hash is invalid")
            payload_sha256 = sha256_json(event.payload)
            if payload_sha256 != event.payload_sha256:
                raise ExperimentLedgerIntegrityError("ledger payload hash is invalid")
            expected_hash = ledger_event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                campaign_id=event.campaign_id,
                event_type=event.event_type,
                subject_id=event.subject_id,
                occurred_at=event.occurred_at,
                actor=event.actor,
                payload_sha256=event.payload_sha256,
                previous_event_sha256=event.previous_event_sha256,
            )
            if event.event_sha256 != expected_hash:
                raise ExperimentLedgerIntegrityError("ledger event hash is invalid")
            expected_previous = event.event_sha256
            expected_sequence += 1
        _verify_projection_counts(connection)
        retained_projection = connection.execute(
            "SELECT value FROM metadata WHERE key = 'projection_sha256'"
        ).fetchone()
        if retained_projection is None or str(retained_projection[0]) != _projection_sha256(
            connection
        ):
            raise ExperimentLedgerIntegrityError("experiment-ledger projection digest is invalid")

    def close(self) -> None:
        if self._connection is None:
            return
        self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._connection.close()
        self._connection = None

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        campaign_id: str,
        event_type: LedgerEventType,
        subject_id: str,
        occurred_at: datetime,
        actor: str,
        payload: dict[str, JsonValue],
    ) -> LedgerEvent:
        _aware(occurred_at, "occurred_at")
        previous = connection.execute(
            "SELECT sequence, occurred_at, event_sha256 "
            "FROM ledger_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if previous is not None and occurred_at.astimezone(UTC) < _parse_datetime(
            str(previous["occurred_at"])
        ):
            raise ExperimentLedgerConflict(
                "ledger events must be appended in nondecreasing timestamp order"
            )
        sequence = int(previous["sequence"]) + 1 if previous is not None else 1
        previous_sha256 = str(previous["event_sha256"]) if previous is not None else _ZERO_HASH
        payload_sha256 = sha256_json(payload)
        seed: dict[str, JsonValue] = {
            "campaign_id": campaign_id,
            "event_type": event_type.value,
            "subject_id": subject_id,
            "occurred_at": _iso(occurred_at),
            "actor": actor,
            "payload_sha256": payload_sha256,
            "previous_event_sha256": previous_sha256,
        }
        event_id = f"event:{sha256_json(seed)}"
        event_sha256 = ledger_event_hash(
            sequence=sequence,
            event_id=event_id,
            campaign_id=campaign_id,
            event_type=event_type,
            subject_id=subject_id,
            occurred_at=occurred_at,
            actor=actor,
            payload_sha256=payload_sha256,
            previous_event_sha256=previous_sha256,
        )
        event = LedgerEvent(
            sequence=sequence,
            event_id=event_id,
            campaign_id=campaign_id,
            event_type=event_type,
            subject_id=subject_id,
            occurred_at=occurred_at.astimezone(UTC),
            actor=actor,
            payload=payload,
            payload_sha256=payload_sha256,
            previous_event_sha256=previous_sha256,
            event_sha256=event_sha256,
        )
        connection.execute(
            """
            INSERT INTO ledger_events(
                sequence, event_id, campaign_id, event_type, subject_id,
                occurred_at, actor, payload_json, payload_sha256,
                previous_event_sha256, event_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.sequence,
                event.event_id,
                event.campaign_id,
                event.event_type.value,
                event.subject_id,
                _iso(event.occurred_at),
                event.actor,
                canonical_json(event.payload),
                event.payload_sha256,
                event.previous_event_sha256,
                event.event_sha256,
            ),
        )
        return event

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._require_connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'projection_sha256'",
                (_projection_sha256(connection),),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise ExperimentLedgerError("experiment-ledger transaction failed") from exc
        except BaseException:
            connection.rollback()
            raise

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ExperimentLedgerError("experiment ledger is closed")
        return self._connection


def _campaign_payload(campaign: CampaignRegistration) -> dict[str, JsonValue]:
    return {
        "campaign_id": campaign.campaign_id,
        "governance_policy_sha256": campaign.governance_policy_sha256,
        "hypothesis_catalog_sha256": campaign.hypothesis_catalog_sha256,
        "data_contract_manifest_sha256": campaign.data_contract_manifest_sha256,
        "baseline_commit": campaign.baseline_commit,
        "registered_at": _iso(campaign.registered_at),
    }


def _candidate_payload(candidate: CandidateRegistration) -> dict[str, JsonValue]:
    return {
        "candidate_id": candidate.candidate_id,
        "campaign_id": candidate.campaign_id,
        "family_id": candidate.family_id,
        "candidate_index": candidate.candidate_index,
        "candidate_ceiling": candidate.candidate_ceiling,
        "specification_sha256": candidate.specification_sha256,
        "code_commit": candidate.code_commit,
        "registered_at": _iso(candidate.registered_at),
        "initial_state": ResearchState.HYPOTHESIS.value,
    }


def _freeze_payload(freeze: PreregistrationFreeze) -> dict[str, JsonValue]:
    return {
        "candidate_id": freeze.candidate_id,
        "protocol_id": freeze.protocol_id,
        "protocol_sha256": freeze.protocol_sha256,
        "data_snapshot_id": freeze.data_snapshot_id,
        "data_snapshot_manifest_sha256": freeze.data_snapshot_manifest_sha256,
        "partition_plan_sha256": freeze.partition_plan_sha256,
        "benchmark_set_sha256": freeze.benchmark_set_sha256,
        "cost_model_set_sha256": freeze.cost_model_set_sha256,
        "candidate_budget_sha256": freeze.candidate_budget_sha256,
        "frozen_at": _iso(freeze.frozen_at),
    }


def _attempt_payload(attempt: AttemptRegistration) -> dict[str, JsonValue]:
    return {
        "attempt_id": attempt.attempt_id,
        "candidate_id": attempt.candidate_id,
        "comparison_group_id": attempt.comparison_group_id,
        "stage": attempt.stage.value,
        "protocol_id": attempt.protocol_id,
        "data_snapshot_id": attempt.data_snapshot_id,
        "partition_id": attempt.partition_id,
        "code_commit": attempt.code_commit,
        "configuration_sha256": attempt.configuration_sha256,
        "benchmark_set_sha256": attempt.benchmark_set_sha256,
        "cost_model_sha256": attempt.cost_model_sha256,
        "inference_plan_sha256": attempt.inference_plan_sha256,
        "registered_at": _iso(attempt.registered_at),
    }


def _artifact_payload(artifact: ArtifactRecord) -> dict[str, JsonValue]:
    return {
        "artifact_id": artifact.artifact_id,
        "attempt_id": artifact.attempt_id,
        "name": artifact.name,
        "sha256": artifact.sha256,
        "byte_count": artifact.byte_count,
        "media_type": artifact.media_type,
        "role": artifact.role,
        "license_class": artifact.license_class,
        "retained_at": _iso(artifact.retained_at),
    }


def _validate_attempt_stage(
    connection: sqlite3.Connection,
    attempt: AttemptRegistration,
    state: ResearchState,
) -> None:
    allowed: dict[AttemptStage, frozenset[ResearchState]] = {
        AttemptStage.DEVELOPMENT: frozenset({ResearchState.DEVELOPMENT}),
        AttemptStage.VALIDATION: frozenset({ResearchState.DEVELOPMENT}),
        AttemptStage.TEST: frozenset({ResearchState.TEST_ELIGIBLE}),
        AttemptStage.ROBUSTNESS: frozenset(
            {ResearchState.DEVELOPMENT, ResearchState.TEST_ELIGIBLE}
        ),
        AttemptStage.PLACEBO: frozenset({ResearchState.DEVELOPMENT, ResearchState.TEST_ELIGIBLE}),
        AttemptStage.CAPACITY: frozenset({ResearchState.DEVELOPMENT, ResearchState.TEST_ELIGIBLE}),
        AttemptStage.LOCKED_HOLDOUT: frozenset({ResearchState.HOLDOUT_ELIGIBLE}),
        AttemptStage.SHADOW: frozenset({ResearchState.SHADOW_ELIGIBLE}),
        AttemptStage.PAPER: frozenset({ResearchState.PAPER_ELIGIBLE}),
    }
    if state not in allowed[attempt.stage]:
        raise ExperimentLedgerConflict(
            f"{attempt.stage.value} attempt is not allowed in candidate state {state.value}"
        )
    if attempt.stage is AttemptStage.LOCKED_HOLDOUT:
        opened = connection.execute(
            """
            SELECT 1 FROM holdouts
            WHERE candidate_id = ? AND status = ?
            LIMIT 1
            """,
            (attempt.candidate_id, HoldoutStatus.OPENED.value),
        ).fetchone()
        if opened is None:
            raise ExperimentLedgerConflict("locked-holdout attempt requires an opened lockbox")
        holdout = connection.execute(
            """
            SELECT data_snapshot_id FROM holdouts
            WHERE candidate_id = ? AND status = ?
            LIMIT 1
            """,
            (attempt.candidate_id, HoldoutStatus.OPENED.value),
        ).fetchone()
        if holdout is None or str(holdout["data_snapshot_id"]) != attempt.data_snapshot_id:
            raise ExperimentLedgerConflict(
                "locked-holdout attempt must use the opened lockbox snapshot"
            )


def _require_all_candidate_comparisons_opened(
    connection: sqlite3.Connection,
    candidate_id: str,
) -> None:
    registered_groups = int(
        connection.execute(
            "SELECT COUNT(DISTINCT comparison_group_id) FROM attempts WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()[0]
    )
    opened_groups = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM comparison_groups
            WHERE comparison_group_id IN (
                SELECT DISTINCT comparison_group_id FROM attempts
                WHERE candidate_id = ?
            )
            """,
            (candidate_id,),
        ).fetchone()[0]
    )
    if registered_groups == 0 or opened_groups != registered_groups:
        raise ExperimentLedgerConflict(
            "promotion requires every candidate-assigned comparison group to be opened"
        )


def _candidate_row(connection: sqlite3.Connection, candidate_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise ExperimentLedgerConflict("candidate is not registered")
    return cast(sqlite3.Row, row)


def _attempt_row(connection: sqlite3.Connection, attempt_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM attempts WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    if row is None:
        raise ExperimentLedgerConflict("attempt is not registered")
    return cast(sqlite3.Row, row)


def _holdout_row(connection: sqlite3.Connection, holdout_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM holdouts WHERE holdout_id = ?",
        (holdout_id,),
    ).fetchone()
    if row is None:
        raise ExperimentLedgerConflict("holdout is not sealed")
    return cast(sqlite3.Row, row)


def _require_campaign(connection: sqlite3.Connection, campaign_id: str) -> None:
    if not _exists(connection, "SELECT 1 FROM campaigns WHERE campaign_id = ?", campaign_id):
        raise ExperimentLedgerConflict("campaign is not registered")


def _exists(connection: sqlite3.Connection, query: str, value: str) -> bool:
    return connection.execute(query, (value,)).fetchone() is not None


def _event_from_row(row: sqlite3.Row) -> LedgerEvent:
    try:
        payload_value = json.loads(str(row["payload_json"]))
    except json.JSONDecodeError as exc:
        raise ExperimentLedgerIntegrityError("ledger payload is not valid JSON") from exc
    if not isinstance(payload_value, dict) or any(
        not isinstance(key, str) for key in payload_value
    ):
        raise ExperimentLedgerIntegrityError("ledger payload is not a JSON object")
    payload = cast(dict[str, JsonValue], payload_value)
    try:
        return LedgerEvent(
            sequence=int(row["sequence"]),
            event_id=str(row["event_id"]),
            campaign_id=str(row["campaign_id"]),
            event_type=LedgerEventType(str(row["event_type"])),
            subject_id=str(row["subject_id"]),
            occurred_at=_parse_datetime(str(row["occurred_at"])),
            actor=str(row["actor"]),
            payload=payload,
            payload_sha256=str(row["payload_sha256"]),
            previous_event_sha256=str(row["previous_event_sha256"]),
            event_sha256=str(row["event_sha256"]),
        )
    except (TypeError, ValueError) as exc:
        raise ExperimentLedgerIntegrityError("ledger event is structurally invalid") from exc


def _json_row(row: sqlite3.Row) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    column_names = row.keys()
    for key in column_names:
        value = row[key]
        if value is None or isinstance(value, str | int | bool):
            normalized[str(key)] = value
        else:
            raise ExperimentLedgerIntegrityError(
                f"unsupported projection value type in {key}: {type(value).__name__}"
            )
    return normalized


def _projection_rows(connection: sqlite3.Connection) -> dict[str, list[JsonValue]]:
    return {
        "campaigns": [
            _json_row(row)
            for row in connection.execute("SELECT * FROM campaigns ORDER BY campaign_id")
        ],
        "candidates": [
            _json_row(row)
            for row in connection.execute("SELECT * FROM candidates ORDER BY candidate_id")
        ],
        "preregistrations": [
            _json_row(row)
            for row in connection.execute("SELECT * FROM preregistrations ORDER BY candidate_id")
        ],
        "attempts": [
            _json_row(row)
            for row in connection.execute("SELECT * FROM attempts ORDER BY attempt_id")
        ],
        "artifacts": [
            _json_row(row)
            for row in connection.execute("SELECT * FROM artifacts ORDER BY artifact_id")
        ],
        "comparison_groups": [
            _json_row(row)
            for row in connection.execute(
                "SELECT * FROM comparison_groups ORDER BY comparison_group_id"
            )
        ],
        "holdouts": [
            _json_row(row)
            for row in connection.execute("SELECT * FROM holdouts ORDER BY holdout_id")
        ],
        "holdout_approvals": [
            _json_row(row)
            for row in connection.execute("SELECT * FROM holdout_approvals ORDER BY approval_id")
        ],
    }


def _projection_sha256(connection: sqlite3.Connection) -> str:
    payload = cast(JsonValue, _projection_rows(connection))
    return sha256_json(payload)


def _verify_projection_counts(connection: sqlite3.Connection) -> None:
    pairs = (
        (LedgerEventType.CAMPAIGN_REGISTERED, "campaigns"),
        (LedgerEventType.CANDIDATE_REGISTERED, "candidates"),
        (LedgerEventType.PREREGISTRATION_FROZEN, "preregistrations"),
        (LedgerEventType.ATTEMPT_REGISTERED, "attempts"),
        (LedgerEventType.ARTIFACT_RETAINED, "artifacts"),
        (LedgerEventType.COMPARISON_OPENED, "comparison_groups"),
        (LedgerEventType.HOLDOUT_SEALED, "holdouts"),
        (LedgerEventType.HOLDOUT_OPEN_APPROVED, "holdout_approvals"),
    )
    projection_counts = {
        "campaigns": int(connection.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]),
        "candidates": int(connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]),
        "preregistrations": int(
            connection.execute("SELECT COUNT(*) FROM preregistrations").fetchone()[0]
        ),
        "attempts": int(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]),
        "artifacts": int(connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]),
        "comparison_groups": int(
            connection.execute("SELECT COUNT(*) FROM comparison_groups").fetchone()[0]
        ),
        "holdouts": int(connection.execute("SELECT COUNT(*) FROM holdouts").fetchone()[0]),
        "holdout_approvals": int(
            connection.execute("SELECT COUNT(*) FROM holdout_approvals").fetchone()[0]
        ),
    }
    for event_type, table in pairs:
        event_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM ledger_events WHERE event_type = ?",
                (event_type.value,),
            ).fetchone()[0]
        )
        if event_count != projection_counts[table]:
            raise ExperimentLedgerIntegrityError(
                f"ledger projection count mismatch for {event_type.value}"
            )


def _prepare_secure_database_path(path: Path) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if parent.resolve(strict=True) != parent:
            raise ExperimentLedgerError(
                "experiment-ledger parent path must not contain symlinks or traversal"
            )
        parent_metadata = parent.lstat()
    except OSError as exc:
        raise ExperimentLedgerError("experiment-ledger parent directory is unavailable") from exc
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise ExperimentLedgerError("experiment-ledger parent must be a directory")
    if stat.S_IMODE(parent_metadata.st_mode) & 0o022:
        raise ExperimentLedgerError("experiment-ledger parent must not be group- or world-writable")
    _require_owner(parent_metadata.st_uid, "experiment-ledger parent")

    try:
        database_metadata = path.lstat()
    except FileNotFoundError:
        if not hasattr(os, "O_CLOEXEC"):
            raise ExperimentLedgerError(
                "secure experiment-ledger creation is unavailable"
            ) from None
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC
        try:
            file_descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ExperimentLedgerError(
                "experiment-ledger database could not be created securely"
            ) from exc
        os.close(file_descriptor)
        return
    except OSError as exc:
        raise ExperimentLedgerError("experiment-ledger database path is unavailable") from exc

    if stat.S_ISLNK(database_metadata.st_mode):
        raise ExperimentLedgerError("experiment-ledger database must not be a symlink")
    if not stat.S_ISREG(database_metadata.st_mode):
        raise ExperimentLedgerError("experiment-ledger database must be a regular file")
    if stat.S_IMODE(database_metadata.st_mode) & 0o177:
        raise ExperimentLedgerError("experiment-ledger database permissions must be mode 0600")
    _require_owner(database_metadata.st_uid, "experiment-ledger database")
    if database_metadata.st_size:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            try:
                header = os.read(descriptor, 16)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise ExperimentLedgerError("experiment-ledger header could not be read") from exc
        if header != b"SQLite format 3\x00":
            raise ExperimentLedgerError("experiment-ledger database header is invalid")


def _require_owner(owner_uid: int, label: str) -> None:
    get_euid = getattr(os, "geteuid", None)
    if get_euid is not None and owner_uid != get_euid():
        raise ExperimentLedgerError(f"{label} is not owned by the service user")


def _require_identifier(value: str, field_name: str) -> None:
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789._:-"
    if (
        not 8 <= len(value) <= 200
        or value[0] not in allowed[:36]
        or any(character not in allowed for character in value)
    ):
        raise ValueError(f"{field_name} has an invalid immutable identifier")


def _require_sha256(value: str, field_name: str) -> None:
    if len(value) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _iso(value: datetime) -> str:
    _aware(value, "timestamp")
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _aware(parsed, "stored timestamp")
    return parsed.astimezone(UTC)
