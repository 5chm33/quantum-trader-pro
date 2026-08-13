from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quantum_trader.adapters.sqlite_experiment_ledger import (
    ExperimentLedgerConflict,
    ExperimentLedgerError,
    ExperimentLedgerIntegrityError,
    SQLiteExperimentLedger,
)
from quantum_trader.domain.experiments import (
    ArtifactRecord,
    AttemptRegistration,
    AttemptStage,
    AttemptStatus,
    CampaignRegistration,
    CandidateRegistration,
    HoldoutApproval,
    HoldoutSeal,
    PreregistrationFreeze,
    ResearchState,
    canonical_json,
    ledger_event_hash,
    sha256_json,
    validate_state_transition,
)

BASE = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
COMMIT = "a" * 40
ACTOR = "research-controller"
CAMPAIGN_ID = "campaign:a-plus-strategy-v1"
CANDIDATE_ID = "candidate:h01:001"
PROTOCOL_ID = "protocol:a-plus-strategy-v1"
DEVELOPMENT_SNAPSHOT_ID = "snapshot:development-v1"
HOLDOUT_SNAPSHOT_ID = "snapshot:holdout-v1"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _campaign(at: datetime = BASE) -> CampaignRegistration:
    return CampaignRegistration(
        campaign_id=CAMPAIGN_ID,
        governance_policy_sha256=_digest("governance"),
        hypothesis_catalog_sha256=_digest("catalog"),
        data_contract_manifest_sha256=_digest("contracts"),
        baseline_commit=COMMIT,
        registered_at=at,
    )


def _candidate(
    *,
    candidate_id: str = CANDIDATE_ID,
    family_id: str = "H01",
    candidate_index: int = 1,
    candidate_ceiling: int = 2,
    at: datetime = BASE + timedelta(seconds=1),
) -> CandidateRegistration:
    return CandidateRegistration(
        candidate_id=candidate_id,
        campaign_id=CAMPAIGN_ID,
        family_id=family_id,
        candidate_index=candidate_index,
        candidate_ceiling=candidate_ceiling,
        specification_sha256=_digest(candidate_id),
        code_commit=COMMIT,
        registered_at=at,
    )


def _freeze(at: datetime = BASE + timedelta(seconds=3)) -> PreregistrationFreeze:
    return PreregistrationFreeze(
        candidate_id=CANDIDATE_ID,
        protocol_id=PROTOCOL_ID,
        protocol_sha256=_digest("protocol"),
        data_snapshot_id=DEVELOPMENT_SNAPSHOT_ID,
        data_snapshot_manifest_sha256=_digest("development-manifest"),
        partition_plan_sha256=_digest("partitions"),
        benchmark_set_sha256=_digest("benchmarks"),
        cost_model_set_sha256=_digest("costs"),
        candidate_budget_sha256=_digest("budget"),
        frozen_at=at,
    )


def _attempt(
    *,
    attempt_id: str,
    group_id: str,
    stage: AttemptStage,
    at: datetime,
    snapshot_id: str = DEVELOPMENT_SNAPSHOT_ID,
    candidate_id: str = CANDIDATE_ID,
) -> AttemptRegistration:
    return AttemptRegistration(
        attempt_id=attempt_id,
        candidate_id=candidate_id,
        comparison_group_id=group_id,
        stage=stage,
        protocol_id=PROTOCOL_ID,
        data_snapshot_id=snapshot_id,
        partition_id=f"partition:{stage.value}:001",
        code_commit=COMMIT,
        configuration_sha256=_digest(f"configuration:{attempt_id}"),
        benchmark_set_sha256=_digest("benchmarks"),
        cost_model_sha256=_digest("cost-model"),
        inference_plan_sha256=_digest("inference"),
        registered_at=at,
    )


def _artifact(attempt_id: str, at: datetime) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=f"artifact:{attempt_id}:summary",
        attempt_id=attempt_id,
        name="summary.json",
        sha256=_digest(f"artifact:{attempt_id}"),
        byte_count=1234,
        media_type="application/json",
        role="result_summary",
        license_class="open",
        retained_at=at,
    )


def _register_candidate_in_development(
    ledger: SQLiteExperimentLedger,
    *,
    start: datetime = BASE,
) -> None:
    ledger.register_campaign(_campaign(start), actor=ACTOR)
    ledger.register_candidate(_candidate(at=start + timedelta(seconds=1)), actor=ACTOR)
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.DEVELOPMENT,
        occurred_at=start + timedelta(seconds=2),
        gate_evidence_sha256=_digest("hypothesis-gate"),
    )
    ledger.freeze_preregistration(_freeze(start + timedelta(seconds=3)), actor=ACTOR)


def _complete(
    ledger: SQLiteExperimentLedger,
    attempt: AttemptRegistration,
    *,
    start_at: datetime,
    artifact_at: datetime,
    complete_at: datetime,
) -> None:
    ledger.register_attempt(attempt, actor=ACTOR)
    ledger.start_attempt(attempt.attempt_id, actor=ACTOR, started_at=start_at)
    ledger.complete_attempt(
        attempt.attempt_id,
        actor=ACTOR,
        completed_at=complete_at,
        result_summary_sha256=_digest(f"result:{attempt.attempt_id}"),
        artifacts=[_artifact(attempt.attempt_id, artifact_at)],
    )


def _advance_to_holdout_eligible(
    ledger: SQLiteExperimentLedger,
    *,
    start: datetime = BASE,
) -> datetime:
    _register_candidate_in_development(ledger, start=start)
    development = _attempt(
        attempt_id="attempt:h01:development:001",
        group_id="comparison:h01:development",
        stage=AttemptStage.DEVELOPMENT,
        at=start + timedelta(seconds=4),
    )
    _complete(
        ledger,
        development,
        start_at=start + timedelta(seconds=5),
        artifact_at=start + timedelta(seconds=6),
        complete_at=start + timedelta(seconds=7),
    )
    ledger.open_comparison(
        development.comparison_group_id,
        actor=ACTOR,
        opened_at=start + timedelta(seconds=8),
    )
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.TEST_ELIGIBLE,
        occurred_at=start + timedelta(seconds=9),
        gate_evidence_sha256=_digest("development-gates"),
    )
    test_attempt = _attempt(
        attempt_id="attempt:h01:test:001",
        group_id="comparison:h01:test",
        stage=AttemptStage.TEST,
        at=start + timedelta(seconds=10),
    )
    _complete(
        ledger,
        test_attempt,
        start_at=start + timedelta(seconds=11),
        artifact_at=start + timedelta(seconds=12),
        complete_at=start + timedelta(seconds=13),
    )
    ledger.open_comparison(
        test_attempt.comparison_group_id,
        actor=ACTOR,
        opened_at=start + timedelta(seconds=14),
    )
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.HOLDOUT_ELIGIBLE,
        occurred_at=start + timedelta(seconds=15),
        gate_evidence_sha256=_digest("preholdout-gates"),
    )
    return start + timedelta(seconds=15)


def _open_holdout(
    ledger: SQLiteExperimentLedger,
    *,
    start: datetime = BASE,
) -> None:
    ledger.seal_holdout(
        HoldoutSeal(
            holdout_id="holdout:a-plus-strategy-v1",
            campaign_id=CAMPAIGN_ID,
            candidate_id=CANDIDATE_ID,
            boundary_sha256=_digest("holdout-boundary"),
            provider_query_sha256=_digest("holdout-query"),
            bytes_retrieved=False,
            sealed_at=start + timedelta(seconds=16),
        ),
        actor=ACTOR,
    )
    approval = HoldoutApproval(
        approval_id="approval:holdout:001",
        campaign_id=CAMPAIGN_ID,
        holdout_id="holdout:a-plus-strategy-v1",
        acknowledgment_sha256=_digest("user-acknowledgment"),
        conversation_receipt_sha256=_digest("conversation-receipt"),
        approved_at=start + timedelta(seconds=17),
        expires_at=start + timedelta(minutes=5),
    )
    ledger.approve_holdout(approval, actor=ACTOR)
    ledger.open_holdout(
        "holdout:a-plus-strategy-v1",
        actor=ACTOR,
        opened_at=start + timedelta(seconds=18),
        approval_id=approval.approval_id,
        data_snapshot_id=HOLDOUT_SNAPSHOT_ID,
        data_snapshot_manifest_sha256=_digest("holdout-snapshot-manifest"),
    )


def test_full_governed_lifecycle_and_restart(tmp_path: Path) -> None:
    path = (tmp_path / "ledger" / "experiments.sqlite3").resolve()
    ledger = SQLiteExperimentLedger(path)
    _advance_to_holdout_eligible(ledger)
    _open_holdout(ledger)

    holdout_attempt = _attempt(
        attempt_id="attempt:h01:holdout:001",
        group_id="comparison:h01:holdout",
        stage=AttemptStage.LOCKED_HOLDOUT,
        at=BASE + timedelta(seconds=19),
        snapshot_id=HOLDOUT_SNAPSHOT_ID,
    )
    _complete(
        ledger,
        holdout_attempt,
        start_at=BASE + timedelta(seconds=20),
        artifact_at=BASE + timedelta(seconds=21),
        complete_at=BASE + timedelta(seconds=22),
    )
    ledger.complete_holdout(
        "holdout:a-plus-strategy-v1",
        actor=ACTOR,
        completed_at=BASE + timedelta(seconds=23),
        result_manifest_sha256=_digest("holdout-result-manifest"),
        passed=True,
    )
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.SHADOW_ELIGIBLE,
        occurred_at=BASE + timedelta(seconds=24),
        gate_evidence_sha256=_digest("holdout-gate"),
    )

    shadow = _attempt(
        attempt_id="attempt:h01:shadow:001",
        group_id="comparison:h01:shadow",
        stage=AttemptStage.SHADOW,
        at=BASE + timedelta(seconds=25),
        snapshot_id="snapshot:shadow-v1",
    )
    _complete(
        ledger,
        shadow,
        start_at=BASE + timedelta(seconds=26),
        artifact_at=BASE + timedelta(seconds=27),
        complete_at=BASE + timedelta(seconds=28),
    )
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.PAPER_ELIGIBLE,
        occurred_at=BASE + timedelta(seconds=29),
        gate_evidence_sha256=_digest("shadow-gate"),
    )

    paper = _attempt(
        attempt_id="attempt:h01:paper:001",
        group_id="comparison:h01:paper",
        stage=AttemptStage.PAPER,
        at=BASE + timedelta(seconds=30),
        snapshot_id="snapshot:paper-v1",
    )
    _complete(
        ledger,
        paper,
        start_at=BASE + timedelta(seconds=31),
        artifact_at=BASE + timedelta(seconds=32),
        complete_at=BASE + timedelta(seconds=33),
    )
    final_event = ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.STRATEGY_A_PLUS,
        occurred_at=BASE + timedelta(seconds=34),
        gate_evidence_sha256=_digest("paper-and-a-plus-gates"),
    )

    assert final_event.payload["to_state"] == "strategy_a_plus"
    assert ledger.current_candidate_state(CANDIDATE_ID) is ResearchState.STRATEGY_A_PLUS
    assert ledger.attempt_status(paper.attempt_id) is AttemptStatus.COMPLETED
    events = tuple(ledger.iter_events(campaign_id=CAMPAIGN_ID))
    assert events[0].previous_event_sha256 == "0" * 64
    assert events[-1].event_sha256 == final_event.event_sha256
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert path.stat().st_mode & 0o777 == 0o600
    ledger.verify_integrity()
    ledger.close()
    ledger.close()

    reopened = SQLiteExperimentLedger(path)
    assert reopened.current_candidate_state(CANDIDATE_ID) is ResearchState.STRATEGY_A_PLUS
    assert tuple(reopened.iter_events())[-1].event_sha256 == final_event.event_sha256
    reopened.verify_integrity()
    reopened.close()


def test_candidate_budget_and_identity_conflicts(tmp_path: Path) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "budget" / "ledger.db").resolve())
    ledger.register_campaign(_campaign(), actor=ACTOR)
    ledger.register_candidate(_candidate(), actor=ACTOR)
    ledger.register_candidate(
        _candidate(
            candidate_id="candidate:h01:002",
            candidate_index=2,
            at=BASE + timedelta(seconds=2),
        ),
        actor=ACTOR,
    )
    with pytest.raises(ValueError, match="exceeds"):
        _candidate(
            candidate_id="candidate:h01:003",
            candidate_index=3,
            candidate_ceiling=2,
            at=BASE + timedelta(seconds=3),
        )
    with pytest.raises(ExperimentLedgerConflict, match="already registered"):
        ledger.register_candidate(_candidate(), actor=ACTOR)
    with pytest.raises(ExperimentLedgerConflict, match="family ceiling changed"):
        ledger.register_candidate(
            _candidate(
                candidate_id="candidate:h01:004",
                candidate_index=3,
                candidate_ceiling=4,
                at=BASE + timedelta(seconds=3),
            ),
            actor=ACTOR,
        )
    ledger.verify_integrity()
    ledger.close()


def test_preregistration_and_attempt_boundaries_fail_closed(tmp_path: Path) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "bounds" / "ledger.db").resolve())
    ledger.register_campaign(_campaign(), actor=ACTOR)
    ledger.register_candidate(_candidate(), actor=ACTOR)
    attempt = _attempt(
        attempt_id="attempt:h01:development:001",
        group_id="comparison:h01:development",
        stage=AttemptStage.DEVELOPMENT,
        at=BASE + timedelta(seconds=3),
    )
    with pytest.raises(ExperimentLedgerConflict, match="frozen preregistration"):
        ledger.register_attempt(attempt, actor=ACTOR)
    with pytest.raises(ExperimentLedgerConflict, match=r"only while.*development"):
        ledger.freeze_preregistration(_freeze(), actor=ACTOR)

    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.DEVELOPMENT,
        occurred_at=BASE + timedelta(seconds=2),
        gate_evidence_sha256=_digest("hypothesis"),
    )
    ledger.freeze_preregistration(_freeze(), actor=ACTOR)
    with pytest.raises(ExperimentLedgerConflict, match="already frozen"):
        ledger.freeze_preregistration(_freeze(BASE + timedelta(seconds=4)), actor=ACTOR)
    wrong_snapshot = _attempt(
        attempt_id="attempt:h01:development:wrong-snapshot",
        group_id="comparison:h01:development",
        stage=AttemptStage.DEVELOPMENT,
        at=BASE + timedelta(seconds=4),
        snapshot_id="snapshot:wrong-development",
    )
    with pytest.raises(ExperimentLedgerConflict, match="data snapshot differs"):
        ledger.register_attempt(wrong_snapshot, actor=ACTOR)
    premature_test = _attempt(
        attempt_id="attempt:h01:test:premature",
        group_id="comparison:h01:test",
        stage=AttemptStage.TEST,
        at=BASE + timedelta(seconds=4),
    )
    with pytest.raises(ExperimentLedgerConflict, match="not allowed"):
        ledger.register_attempt(premature_test, actor=ACTOR)
    ledger.close()


def test_comparison_waits_for_every_terminal_attempt(tmp_path: Path) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "compare" / "ledger.db").resolve())
    _register_candidate_in_development(ledger)
    first = _attempt(
        attempt_id="attempt:h01:development:001",
        group_id="comparison:h01:development",
        stage=AttemptStage.DEVELOPMENT,
        at=BASE + timedelta(seconds=4),
    )
    second = _attempt(
        attempt_id="attempt:h01:development:002",
        group_id="comparison:h01:development",
        stage=AttemptStage.DEVELOPMENT,
        at=BASE + timedelta(seconds=5),
    )
    ledger.register_attempt(first, actor=ACTOR)
    ledger.register_attempt(second, actor=ACTOR)
    ledger.start_attempt(first.attempt_id, actor=ACTOR, started_at=BASE + timedelta(seconds=6))
    ledger.complete_attempt(
        first.attempt_id,
        actor=ACTOR,
        completed_at=BASE + timedelta(seconds=8),
        result_summary_sha256=_digest("first"),
        artifacts=[_artifact(first.attempt_id, BASE + timedelta(seconds=7))],
    )
    with pytest.raises(ExperimentLedgerConflict, match="every assigned attempt is terminal"):
        ledger.open_comparison(
            first.comparison_group_id,
            actor=ACTOR,
            opened_at=BASE + timedelta(seconds=9),
        )
    ledger.fail_attempt(
        second.attempt_id,
        actor=ACTOR,
        failed_at=BASE + timedelta(seconds=10),
        reason_code="controlled_failure",
        reason_sha256=_digest("controlled failure detail"),
    )
    opened = ledger.open_comparison(
        first.comparison_group_id,
        actor=ACTOR,
        opened_at=BASE + timedelta(seconds=11),
    )
    assert opened.payload["attempt_count"] == 2
    with pytest.raises(ExperimentLedgerConflict, match="already opened"):
        ledger.open_comparison(
            first.comparison_group_id,
            actor=ACTOR,
            opened_at=BASE + timedelta(seconds=12),
        )
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.TEST_ELIGIBLE,
        occurred_at=BASE + timedelta(seconds=12),
        gate_evidence_sha256=_digest("selection"),
    )
    assert ledger.current_candidate_state(CANDIDATE_ID) is ResearchState.TEST_ELIGIBLE
    ledger.close()


def test_attempt_terminal_states_and_artifact_chronology_are_immutable(tmp_path: Path) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "terminal" / "ledger.db").resolve())
    _register_candidate_in_development(ledger)
    attempt = _attempt(
        attempt_id="attempt:h01:development:001",
        group_id="comparison:h01:development",
        stage=AttemptStage.DEVELOPMENT,
        at=BASE + timedelta(seconds=4),
    )
    ledger.register_attempt(attempt, actor=ACTOR)
    with pytest.raises(ExperimentLedgerConflict, match="not registered"):
        ledger.start_attempt(
            "attempt:missing:001",
            actor=ACTOR,
            started_at=BASE + timedelta(seconds=5),
        )
    ledger.start_attempt(attempt.attempt_id, actor=ACTOR, started_at=BASE + timedelta(seconds=5))
    with pytest.raises(ValueError, match="after attempt completion"):
        ledger.complete_attempt(
            attempt.attempt_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=6),
            result_summary_sha256=_digest("result"),
            artifacts=[_artifact(attempt.attempt_id, BASE + timedelta(seconds=7))],
        )
    with pytest.raises(ExperimentLedgerConflict, match="before attempt start"):
        ledger.complete_attempt(
            attempt.attempt_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=7),
            result_summary_sha256=_digest("result"),
            artifacts=[_artifact(attempt.attempt_id, BASE + timedelta(seconds=4))],
        )
    ledger.abort_attempt(
        attempt.attempt_id,
        actor=ACTOR,
        aborted_at=BASE + timedelta(seconds=8),
        reason_code="operator_abort",
        reason_sha256=_digest("abort"),
    )
    assert ledger.attempt_status(attempt.attempt_id) is AttemptStatus.ABORTED
    with pytest.raises(ExperimentLedgerConflict, match="terminal attempt state"):
        ledger.fail_attempt(
            attempt.attempt_id,
            actor=ACTOR,
            failed_at=BASE + timedelta(seconds=9),
            reason_code="rewrite",
            reason_sha256=_digest("rewrite"),
        )
    with pytest.raises(ExperimentLedgerConflict, match="only a started attempt"):
        ledger.complete_attempt(
            attempt.attempt_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=9),
            result_summary_sha256=_digest("result"),
            artifacts=[_artifact(attempt.attempt_id, BASE + timedelta(seconds=9))],
        )
    ledger.close()


def test_holdout_requires_approval_snapshot_attempt_and_one_time_use(tmp_path: Path) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "holdout" / "ledger.db").resolve())
    _advance_to_holdout_eligible(ledger)
    seal = HoldoutSeal(
        holdout_id="holdout:a-plus-strategy-v1",
        campaign_id=CAMPAIGN_ID,
        candidate_id=CANDIDATE_ID,
        boundary_sha256=_digest("boundary"),
        provider_query_sha256=_digest("query"),
        bytes_retrieved=False,
        sealed_at=BASE + timedelta(seconds=16),
    )
    ledger.seal_holdout(seal, actor=ACTOR)
    with pytest.raises(ExperimentLedgerConflict, match="only one lockbox"):
        ledger.seal_holdout(
            HoldoutSeal(
                holdout_id="holdout:a-plus-strategy-v1-second",
                campaign_id=CAMPAIGN_ID,
                candidate_id=CANDIDATE_ID,
                boundary_sha256=_digest("boundary-2"),
                provider_query_sha256=_digest("query-2"),
                bytes_retrieved=False,
                sealed_at=BASE + timedelta(seconds=17),
            ),
            actor=ACTOR,
        )
    with pytest.raises(ExperimentLedgerConflict, match="explicit holdout approval"):
        ledger.open_holdout(
            seal.holdout_id,
            actor=ACTOR,
            opened_at=BASE + timedelta(seconds=17),
            approval_id="approval:missing:001",
            data_snapshot_id=HOLDOUT_SNAPSHOT_ID,
            data_snapshot_manifest_sha256=_digest("manifest"),
        )
    approval = HoldoutApproval(
        approval_id="approval:holdout:001",
        campaign_id=CAMPAIGN_ID,
        holdout_id=seal.holdout_id,
        acknowledgment_sha256=_digest("ack"),
        conversation_receipt_sha256=_digest("receipt"),
        approved_at=BASE + timedelta(seconds=18),
        expires_at=BASE + timedelta(seconds=20),
    )
    ledger.approve_holdout(approval, actor=ACTOR)
    with pytest.raises(ExperimentLedgerConflict, match="expired"):
        ledger.open_holdout(
            seal.holdout_id,
            actor=ACTOR,
            opened_at=BASE + timedelta(seconds=21),
            approval_id=approval.approval_id,
            data_snapshot_id=HOLDOUT_SNAPSHOT_ID,
            data_snapshot_manifest_sha256=_digest("manifest"),
        )
    current_approval = HoldoutApproval(
        approval_id="approval:holdout:002",
        campaign_id=CAMPAIGN_ID,
        holdout_id=seal.holdout_id,
        acknowledgment_sha256=_digest("ack-2"),
        conversation_receipt_sha256=_digest("receipt-2"),
        approved_at=BASE + timedelta(seconds=21),
        expires_at=BASE + timedelta(minutes=5),
    )
    ledger.approve_holdout(current_approval, actor=ACTOR)
    ledger.open_holdout(
        seal.holdout_id,
        actor=ACTOR,
        opened_at=BASE + timedelta(seconds=22),
        approval_id=current_approval.approval_id,
        data_snapshot_id=HOLDOUT_SNAPSHOT_ID,
        data_snapshot_manifest_sha256=_digest("manifest"),
    )
    with pytest.raises(ExperimentLedgerConflict, match="exactly once"):
        ledger.open_holdout(
            seal.holdout_id,
            actor=ACTOR,
            opened_at=BASE + timedelta(seconds=23),
            approval_id=current_approval.approval_id,
            data_snapshot_id=HOLDOUT_SNAPSHOT_ID,
            data_snapshot_manifest_sha256=_digest("manifest"),
        )
    with pytest.raises(ExperimentLedgerConflict, match="completed lockbox attempt"):
        ledger.complete_holdout(
            seal.holdout_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=23),
            result_manifest_sha256=_digest("result-manifest"),
            passed=False,
        )
    wrong_snapshot_attempt = _attempt(
        attempt_id="attempt:h01:holdout:wrong",
        group_id="comparison:h01:holdout",
        stage=AttemptStage.LOCKED_HOLDOUT,
        at=BASE + timedelta(seconds=23),
        snapshot_id="snapshot:wrong-holdout",
    )
    with pytest.raises(ExperimentLedgerConflict, match="opened lockbox snapshot"):
        ledger.register_attempt(wrong_snapshot_attempt, actor=ACTOR)
    ledger.close()


def test_hash_chain_and_projection_tampering_are_detected_on_reopen(tmp_path: Path) -> None:
    event_path = (tmp_path / "event-tamper" / "ledger.db").resolve()
    event_ledger = SQLiteExperimentLedger(event_path)
    event_ledger.register_campaign(_campaign(), actor=ACTOR)
    event_ledger.close()
    with sqlite3.connect(event_path) as connection:
        connection.execute(
            "UPDATE ledger_events SET payload_json = ? WHERE sequence = 1",
            (canonical_json({"campaign_id": "campaign:tampered"}),),
        )
    with pytest.raises(ExperimentLedgerIntegrityError, match="payload hash"):
        SQLiteExperimentLedger(event_path)

    projection_path = (tmp_path / "projection-tamper" / "ledger.db").resolve()
    projection_ledger = SQLiteExperimentLedger(projection_path)
    projection_ledger.register_campaign(_campaign(), actor=ACTOR)
    projection_ledger.register_candidate(_candidate(), actor=ACTOR)
    projection_ledger.close()
    with sqlite3.connect(projection_path) as connection:
        connection.execute(
            "UPDATE candidates SET current_state = ? WHERE candidate_id = ?",
            (ResearchState.STRATEGY_A_PLUS.value, CANDIDATE_ID),
        )
    with pytest.raises(ExperimentLedgerIntegrityError, match="projection digest"):
        SQLiteExperimentLedger(projection_path)


def test_secure_database_paths_and_closed_store(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        SQLiteExperimentLedger(Path("relative.db"))

    broad_parent = tmp_path / "broad-parent"
    broad_parent.mkdir(mode=0o777)
    broad_parent.chmod(0o777)
    with pytest.raises(ExperimentLedgerError, match="group- or world-writable"):
        SQLiteExperimentLedger((broad_parent / "ledger.db").resolve())

    corrupt_parent = tmp_path / "corrupt"
    corrupt_parent.mkdir(mode=0o700)
    corrupt = corrupt_parent / "ledger.db"
    corrupt.write_bytes(b"not sqlite")
    corrupt.chmod(0o600)
    with pytest.raises(ExperimentLedgerError, match="header is invalid"):
        SQLiteExperimentLedger(corrupt.resolve())

    target_parent = tmp_path / "target"
    target_parent.mkdir(mode=0o700)
    target = target_parent / "target.db"
    target.touch(mode=0o600)
    link_parent = tmp_path / "link-parent"
    link_parent.mkdir(mode=0o700)
    link = link_parent / "ledger.db"
    link.symlink_to(target)
    with pytest.raises(ExperimentLedgerError, match="must not be a symlink"):
        SQLiteExperimentLedger(link)

    path = (tmp_path / "closed" / "ledger.db").resolve()
    ledger = SQLiteExperimentLedger(path)
    ledger.close()
    with pytest.raises(ExperimentLedgerError, match="closed"):
        tuple(ledger.iter_events())


def test_domain_hashes_state_machine_and_validation_edges() -> None:
    value = {"z": 1, "a": [True, None, "x"]}
    assert canonical_json(value) == '{"a":[true,null,"x"],"z":1}'
    assert sha256_json(value) == _digest(canonical_json(value))
    event_hash = ledger_event_hash(
        sequence=1,
        event_id="event:00000001",
        campaign_id=CAMPAIGN_ID,
        event_type=__import__(
            "quantum_trader.domain.experiments", fromlist=["LedgerEventType"]
        ).LedgerEventType.CAMPAIGN_REGISTERED,
        subject_id=CAMPAIGN_ID,
        occurred_at=BASE,
        actor=ACTOR,
        payload_sha256=_digest("payload"),
        previous_event_sha256="0" * 64,
    )
    assert len(event_hash) == 64
    validate_state_transition(ResearchState.HYPOTHESIS, ResearchState.DEVELOPMENT)
    validate_state_transition(ResearchState.DEVELOPMENT, ResearchState.REJECTED)
    with pytest.raises(ValueError, match="invalid research state transition"):
        validate_state_transition(ResearchState.HYPOTHESIS, ResearchState.STRATEGY_A_PLUS)
    with pytest.raises(ValueError, match="family_id"):
        _candidate(family_id="bad")
    with pytest.raises(ValueError, match="exceeds"):
        _candidate(candidate_index=3, candidate_ceiling=2)
    with pytest.raises(ValueError, match="bytes are retrieved"):
        HoldoutSeal(
            holdout_id="holdout:invalid:001",
            campaign_id=CAMPAIGN_ID,
            candidate_id=CANDIDATE_ID,
            boundary_sha256=_digest("boundary"),
            provider_query_sha256=_digest("query"),
            bytes_retrieved=True,
            sealed_at=BASE,
        )
    with pytest.raises(ValueError, match="expire after"):
        HoldoutApproval(
            approval_id="approval:invalid:001",
            campaign_id=CAMPAIGN_ID,
            holdout_id="holdout:invalid:001",
            acknowledgment_sha256=_digest("ack"),
            conversation_receipt_sha256=_digest("receipt"),
            approved_at=BASE,
            expires_at=BASE,
        )


def test_registration_protocol_start_and_terminal_edges(tmp_path: Path) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "registration-edges" / "ledger.db").resolve())
    ledger.register_campaign(_campaign(), actor=ACTOR)
    with pytest.raises(ExperimentLedgerConflict, match="already registered"):
        ledger.register_campaign(_campaign(BASE + timedelta(seconds=1)), actor=ACTOR)
    with pytest.raises(ExperimentLedgerConflict, match="nondecreasing"):
        ledger.register_candidate(_candidate(at=BASE - timedelta(seconds=1)), actor=ACTOR)

    ledger.register_candidate(_candidate(), actor=ACTOR)
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.DEVELOPMENT,
        occurred_at=BASE + timedelta(seconds=2),
        gate_evidence_sha256=_digest("hypothesis"),
    )
    ledger.freeze_preregistration(_freeze(), actor=ACTOR)
    wrong_protocol = AttemptRegistration(
        attempt_id="attempt:h01:wrong-protocol",
        candidate_id=CANDIDATE_ID,
        comparison_group_id="comparison:h01:development",
        stage=AttemptStage.DEVELOPMENT,
        protocol_id="protocol:other-v1",
        data_snapshot_id=DEVELOPMENT_SNAPSHOT_ID,
        partition_id="partition:development:wrong-protocol",
        code_commit=COMMIT,
        configuration_sha256=_digest("wrong-protocol"),
        benchmark_set_sha256=_digest("benchmarks"),
        cost_model_sha256=_digest("cost-model"),
        inference_plan_sha256=_digest("inference"),
        registered_at=BASE + timedelta(seconds=4),
    )
    with pytest.raises(ExperimentLedgerConflict, match="protocol differs"):
        ledger.register_attempt(wrong_protocol, actor=ACTOR)

    valid = _attempt(
        attempt_id="attempt:h01:development:edge",
        group_id="comparison:h01:development-edge",
        stage=AttemptStage.DEVELOPMENT,
        at=BASE + timedelta(seconds=5),
    )
    ledger.register_attempt(valid, actor=ACTOR)
    with pytest.raises(ExperimentLedgerConflict, match="before registration"):
        ledger.start_attempt(
            valid.attempt_id,
            actor=ACTOR,
            started_at=BASE + timedelta(seconds=4),
        )
    ledger.start_attempt(valid.attempt_id, actor=ACTOR, started_at=BASE + timedelta(seconds=6))
    with pytest.raises(ExperimentLedgerConflict, match="only a registered attempt"):
        ledger.start_attempt(
            valid.attempt_id,
            actor=ACTOR,
            started_at=BASE + timedelta(seconds=7),
        )
    with pytest.raises(ValueError, match="reason_code"):
        ledger.fail_attempt(
            valid.attempt_id,
            actor=ACTOR,
            failed_at=BASE + timedelta(seconds=7),
            reason_code="   ",
            reason_sha256=_digest("reason"),
        )
    with pytest.raises(ExperimentLedgerConflict, match="before registration"):
        ledger.fail_attempt(
            valid.attempt_id,
            actor=ACTOR,
            failed_at=BASE + timedelta(seconds=4),
            reason_code="early",
            reason_sha256=_digest("reason"),
        )
    ledger.fail_attempt(
        valid.attempt_id,
        actor=ACTOR,
        failed_at=BASE + timedelta(seconds=8),
        reason_code="controlled_failure",
        reason_sha256=_digest("reason"),
    )
    assert ledger.attempt_status(valid.attempt_id) is AttemptStatus.FAILED
    ledger.close()


def test_secure_path_timestamp_and_digest_validation_edges(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    symlink_parent = tmp_path / "symlink-parent"
    symlink_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ExperimentLedgerError, match="must not contain symlinks"):
        SQLiteExperimentLedger(symlink_parent / "ledger.db")

    file_parent = tmp_path / "file-parent"
    file_parent.mkdir(mode=0o700)
    broad = file_parent / "broad.db"
    broad.touch(mode=0o600)
    broad.chmod(0o644)
    with pytest.raises(ExperimentLedgerError, match="permissions"):
        SQLiteExperimentLedger(broad.resolve())

    directory_path = file_parent / "directory.db"
    directory_path.mkdir(mode=0o700)
    with pytest.raises(ExperimentLedgerError, match="regular file"):
        SQLiteExperimentLedger(directory_path.resolve())

    ledger = SQLiteExperimentLedger((tmp_path / "validation" / "ledger.db").resolve())
    ledger.register_campaign(_campaign(), actor=ACTOR)
    with pytest.raises(ValueError, match="timezone-aware"):
        ledger.register_candidate(
            _candidate(at=datetime(2026, 1, 2, 14, 30)),
            actor=ACTOR,
        )
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        ledger.register_campaign(
            CampaignRegistration(
                campaign_id="campaign:invalid:002",
                governance_policy_sha256="X" * 64,
                hypothesis_catalog_sha256=_digest("catalog"),
                data_contract_manifest_sha256=_digest("contracts"),
                baseline_commit=COMMIT,
                registered_at=BASE + timedelta(seconds=1),
            ),
            actor=ACTOR,
        )
    ledger.register_candidate(_candidate(), actor=ACTOR)
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.REJECTED,
        occurred_at=BASE + timedelta(seconds=2),
        gate_evidence_sha256=_digest("reject"),
    )
    assert ledger.current_candidate_state(CANDIDATE_ID) is ResearchState.REJECTED
    with pytest.raises(ValueError, match="invalid immutable identifier"):
        ledger.open_holdout(
            "holdout:missing:001",
            actor=ACTOR,
            opened_at=BASE + timedelta(seconds=3),
            approval_id="approval:missing:001",
            data_snapshot_id="bad",
            data_snapshot_manifest_sha256=_digest("snapshot"),
        )
    ledger.close()


def test_holdout_approval_chronology_snapshot_and_completion_edges(tmp_path: Path) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "holdout-edges" / "ledger.db").resolve())
    _advance_to_holdout_eligible(ledger)
    seal = HoldoutSeal(
        holdout_id="holdout:edge:001",
        campaign_id=CAMPAIGN_ID,
        candidate_id=CANDIDATE_ID,
        boundary_sha256=_digest("edge-boundary"),
        provider_query_sha256=_digest("edge-query"),
        bytes_retrieved=False,
        sealed_at=BASE + timedelta(seconds=16),
    )
    ledger.seal_holdout(seal, actor=ACTOR)
    predated = HoldoutApproval(
        approval_id="approval:edge:predated",
        campaign_id=CAMPAIGN_ID,
        holdout_id=seal.holdout_id,
        acknowledgment_sha256=_digest("ack-predated"),
        conversation_receipt_sha256=_digest("receipt-predated"),
        approved_at=BASE + timedelta(seconds=15),
        expires_at=BASE + timedelta(minutes=1),
    )
    with pytest.raises(ExperimentLedgerConflict, match="cannot predate sealing"):
        ledger.approve_holdout(predated, actor=ACTOR)

    approval = HoldoutApproval(
        approval_id="approval:edge:valid",
        campaign_id=CAMPAIGN_ID,
        holdout_id=seal.holdout_id,
        acknowledgment_sha256=_digest("ack-valid"),
        conversation_receipt_sha256=_digest("receipt-valid"),
        approved_at=BASE + timedelta(seconds=17),
        expires_at=BASE + timedelta(minutes=1),
    )
    ledger.approve_holdout(approval, actor=ACTOR)
    with pytest.raises(ExperimentLedgerConflict, match="before approval"):
        ledger.open_holdout(
            seal.holdout_id,
            actor=ACTOR,
            opened_at=BASE + timedelta(seconds=16),
            approval_id=approval.approval_id,
            data_snapshot_id=HOLDOUT_SNAPSHOT_ID,
            data_snapshot_manifest_sha256=_digest("holdout-manifest"),
        )
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        ledger.open_holdout(
            seal.holdout_id,
            actor=ACTOR,
            opened_at=BASE + timedelta(seconds=18),
            approval_id=approval.approval_id,
            data_snapshot_id=HOLDOUT_SNAPSHOT_ID,
            data_snapshot_manifest_sha256="A" * 64,
        )
    ledger.open_holdout(
        seal.holdout_id,
        actor=ACTOR,
        opened_at=BASE + timedelta(seconds=18),
        approval_id=approval.approval_id,
        data_snapshot_id=HOLDOUT_SNAPSHOT_ID,
        data_snapshot_manifest_sha256=_digest("holdout-manifest"),
    )
    holdout_attempt = _attempt(
        attempt_id="attempt:h01:holdout:edge",
        group_id="comparison:h01:holdout-edge",
        stage=AttemptStage.LOCKED_HOLDOUT,
        at=BASE + timedelta(seconds=19),
        snapshot_id=HOLDOUT_SNAPSHOT_ID,
    )
    _complete(
        ledger,
        holdout_attempt,
        start_at=BASE + timedelta(seconds=20),
        artifact_at=BASE + timedelta(seconds=21),
        complete_at=BASE + timedelta(seconds=22),
    )
    with pytest.raises(ExperimentLedgerConflict, match="cannot complete before opening"):
        ledger.complete_holdout(
            seal.holdout_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=17),
            result_manifest_sha256=_digest("result"),
            passed=True,
        )
    ledger.complete_holdout(
        seal.holdout_id,
        actor=ACTOR,
        completed_at=BASE + timedelta(seconds=23),
        result_manifest_sha256=_digest("result"),
        passed=True,
    )
    with pytest.raises(ExperimentLedgerConflict, match="only an opened holdout"):
        ledger.complete_holdout(
            seal.holdout_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=24),
            result_manifest_sha256=_digest("result-again"),
            passed=False,
        )
    ledger.close()


def test_artifact_input_validation_and_deterministic_order(tmp_path: Path) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "artifacts" / "ledger.db").resolve())
    _register_candidate_in_development(ledger)
    attempt = _attempt(
        attempt_id="attempt:h01:artifact-order",
        group_id="comparison:h01:artifact-order",
        stage=AttemptStage.DEVELOPMENT,
        at=BASE + timedelta(seconds=4),
    )
    ledger.register_attempt(attempt, actor=ACTOR)
    ledger.start_attempt(attempt.attempt_id, actor=ACTOR, started_at=BASE + timedelta(seconds=5))
    first = ArtifactRecord(
        artifact_id="artifact:order:first",
        attempt_id=attempt.attempt_id,
        name="first.json",
        sha256=_digest("first-artifact"),
        byte_count=1,
        media_type="application/json",
        role="summary",
        license_class="open",
        retained_at=BASE + timedelta(seconds=7),
    )
    second = ArtifactRecord(
        artifact_id="artifact:order:second",
        attempt_id=attempt.attempt_id,
        name="second.json",
        sha256=_digest("second-artifact"),
        byte_count=2,
        media_type="application/json",
        role="diagnostic",
        license_class="open",
        retained_at=BASE + timedelta(seconds=6),
    )
    wrong_attempt = ArtifactRecord(
        artifact_id="artifact:wrong:001",
        attempt_id="attempt:other:001",
        name="wrong.json",
        sha256=_digest("wrong"),
        byte_count=1,
        media_type="application/json",
        role="summary",
        license_class="open",
        retained_at=BASE + timedelta(seconds=6),
    )
    with pytest.raises(ValueError, match="at least one artifact"):
        ledger.complete_attempt(
            attempt.attempt_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=8),
            result_summary_sha256=_digest("result"),
            artifacts=[],
        )
    with pytest.raises(ValueError, match="must belong"):
        ledger.complete_attempt(
            attempt.attempt_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=8),
            result_summary_sha256=_digest("result"),
            artifacts=[wrong_attempt],
        )
    with pytest.raises(ValueError, match="artifact IDs"):
        ledger.complete_attempt(
            attempt.attempt_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=8),
            result_summary_sha256=_digest("result"),
            artifacts=[first, first],
        )
    duplicate_name = ArtifactRecord(
        artifact_id="artifact:order:duplicate-name",
        attempt_id=attempt.attempt_id,
        name=first.name,
        sha256=_digest("duplicate-name"),
        byte_count=1,
        media_type="application/json",
        role="summary",
        license_class="open",
        retained_at=BASE + timedelta(seconds=6),
    )
    with pytest.raises(ValueError, match="artifact names"):
        ledger.complete_attempt(
            attempt.attempt_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=8),
            result_summary_sha256=_digest("result"),
            artifacts=[first, duplicate_name],
        )
    ledger.complete_attempt(
        attempt.attempt_id,
        actor=ACTOR,
        completed_at=BASE + timedelta(seconds=8),
        result_summary_sha256=_digest("result"),
        artifacts=[first, second],
    )
    events = tuple(ledger.iter_events())
    retained = [
        event.subject_id for event in events if event.event_type.value == "artifact_retained"
    ]
    assert retained == [second.artifact_id, first.artifact_id]
    ledger.close()


def test_event_sequence_tampering_is_detected_on_reopen(tmp_path: Path) -> None:
    path = (tmp_path / "sequence-tamper" / "ledger.db").resolve()
    ledger = SQLiteExperimentLedger(path)
    ledger.register_campaign(_campaign(), actor=ACTOR)
    ledger.register_candidate(_candidate(), actor=ACTOR)
    ledger.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE ledger_events SET sequence = 3 WHERE sequence = 2")
    with pytest.raises(ExperimentLedgerIntegrityError, match="sequence is not contiguous"):
        SQLiteExperimentLedger(path)


def test_promotion_requires_complete_comparisons_and_completed_test_evidence(
    tmp_path: Path,
) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "promotion-barriers" / "ledger.db").resolve())
    _register_candidate_in_development(ledger)
    with pytest.raises(ExperimentLedgerConflict, match="completed development"):
        ledger.transition_candidate(
            CANDIDATE_ID,
            actor=ACTOR,
            target=ResearchState.TEST_ELIGIBLE,
            occurred_at=BASE + timedelta(seconds=4),
            gate_evidence_sha256=_digest("no-development"),
        )
    development = _attempt(
        attempt_id="attempt:h01:development:barrier",
        group_id="comparison:h01:development-barrier",
        stage=AttemptStage.DEVELOPMENT,
        at=BASE + timedelta(seconds=4),
    )
    _complete(
        ledger,
        development,
        start_at=BASE + timedelta(seconds=5),
        artifact_at=BASE + timedelta(seconds=6),
        complete_at=BASE + timedelta(seconds=7),
    )
    with pytest.raises(ExperimentLedgerConflict, match="every candidate-assigned"):
        ledger.transition_candidate(
            CANDIDATE_ID,
            actor=ACTOR,
            target=ResearchState.TEST_ELIGIBLE,
            occurred_at=BASE + timedelta(seconds=8),
            gate_evidence_sha256=_digest("unopened-comparison"),
        )
    ledger.open_comparison(
        development.comparison_group_id,
        actor=ACTOR,
        opened_at=BASE + timedelta(seconds=8),
    )
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.TEST_ELIGIBLE,
        occurred_at=BASE + timedelta(seconds=9),
        gate_evidence_sha256=_digest("opened-development"),
    )
    with pytest.raises(ExperimentLedgerConflict, match="completed test evidence"):
        ledger.transition_candidate(
            CANDIDATE_ID,
            actor=ACTOR,
            target=ResearchState.HOLDOUT_ELIGIBLE,
            occurred_at=BASE + timedelta(seconds=10),
            gate_evidence_sha256=_digest("no-test"),
        )
    failed_test = _attempt(
        attempt_id="attempt:h01:test:failed",
        group_id="comparison:h01:test-failed",
        stage=AttemptStage.TEST,
        at=BASE + timedelta(seconds=10),
    )
    ledger.register_attempt(failed_test, actor=ACTOR)
    ledger.fail_attempt(
        failed_test.attempt_id,
        actor=ACTOR,
        failed_at=BASE + timedelta(seconds=11),
        reason_code="predeclared_failure",
        reason_sha256=_digest("failed-test"),
    )
    ledger.open_comparison(
        failed_test.comparison_group_id,
        actor=ACTOR,
        opened_at=BASE + timedelta(seconds=12),
    )
    with pytest.raises(ExperimentLedgerConflict, match="every registered attempt to complete"):
        ledger.transition_candidate(
            CANDIDATE_ID,
            actor=ACTOR,
            target=ResearchState.HOLDOUT_ELIGIBLE,
            occurred_at=BASE + timedelta(seconds=13),
            gate_evidence_sha256=_digest("failed-test-gate"),
        )
    ledger.close()


def test_shadow_and_paper_promotion_require_earned_evidence(tmp_path: Path) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "earned-promotion" / "ledger.db").resolve())
    _advance_to_holdout_eligible(ledger)
    with pytest.raises(ExperimentLedgerConflict, match="passing holdout"):
        ledger.transition_candidate(
            CANDIDATE_ID,
            actor=ACTOR,
            target=ResearchState.SHADOW_ELIGIBLE,
            occurred_at=BASE + timedelta(seconds=16),
            gate_evidence_sha256=_digest("no-holdout"),
        )
    _open_holdout(ledger)
    holdout_attempt = _attempt(
        attempt_id="attempt:h01:holdout:earned",
        group_id="comparison:h01:holdout-earned",
        stage=AttemptStage.LOCKED_HOLDOUT,
        at=BASE + timedelta(seconds=19),
        snapshot_id=HOLDOUT_SNAPSHOT_ID,
    )
    _complete(
        ledger,
        holdout_attempt,
        start_at=BASE + timedelta(seconds=20),
        artifact_at=BASE + timedelta(seconds=21),
        complete_at=BASE + timedelta(seconds=22),
    )
    ledger.complete_holdout(
        "holdout:a-plus-strategy-v1",
        actor=ACTOR,
        completed_at=BASE + timedelta(seconds=23),
        result_manifest_sha256=_digest("earned-holdout"),
        passed=True,
    )
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.SHADOW_ELIGIBLE,
        occurred_at=BASE + timedelta(seconds=24),
        gate_evidence_sha256=_digest("earned-holdout"),
    )
    with pytest.raises(ExperimentLedgerConflict, match="completed shadow attempt"):
        ledger.transition_candidate(
            CANDIDATE_ID,
            actor=ACTOR,
            target=ResearchState.PAPER_ELIGIBLE,
            occurred_at=BASE + timedelta(seconds=25),
            gate_evidence_sha256=_digest("no-shadow"),
        )
    shadow = _attempt(
        attempt_id="attempt:h01:shadow:earned",
        group_id="comparison:h01:shadow-earned",
        stage=AttemptStage.SHADOW,
        at=BASE + timedelta(seconds=25),
        snapshot_id="snapshot:shadow-earned",
    )
    _complete(
        ledger,
        shadow,
        start_at=BASE + timedelta(seconds=26),
        artifact_at=BASE + timedelta(seconds=27),
        complete_at=BASE + timedelta(seconds=28),
    )
    ledger.transition_candidate(
        CANDIDATE_ID,
        actor=ACTOR,
        target=ResearchState.PAPER_ELIGIBLE,
        occurred_at=BASE + timedelta(seconds=29),
        gate_evidence_sha256=_digest("earned-shadow"),
    )
    with pytest.raises(ExperimentLedgerConflict, match="completed paper attempt"):
        ledger.transition_candidate(
            CANDIDATE_ID,
            actor=ACTOR,
            target=ResearchState.STRATEGY_A_PLUS,
            occurred_at=BASE + timedelta(seconds=30),
            gate_evidence_sha256=_digest("no-paper"),
        )
    ledger.close()


def test_malformed_event_payload_is_detected_on_reopen(tmp_path: Path) -> None:
    path = (tmp_path / "malformed-event" / "ledger.db").resolve()
    ledger = SQLiteExperimentLedger(path)
    ledger.register_campaign(_campaign(), actor=ACTOR)
    ledger.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE ledger_events SET payload_json = '{not valid json' WHERE sequence = 1"
        )
    with pytest.raises(ExperimentLedgerIntegrityError, match="not valid JSON"):
        SQLiteExperimentLedger(path)


def test_projection_commitment_and_nonobject_event_payloads_fail_closed(tmp_path: Path) -> None:
    projection_path = (tmp_path / "missing-projection" / "ledger.db").resolve()
    projection_ledger = SQLiteExperimentLedger(projection_path)
    projection_ledger.register_campaign(_campaign(), actor=ACTOR)
    projection_ledger.close()
    with sqlite3.connect(projection_path) as connection:
        connection.execute("DELETE FROM metadata WHERE key = 'projection_sha256'")
    with pytest.raises(ExperimentLedgerIntegrityError, match="lacks a projection digest"):
        SQLiteExperimentLedger(projection_path)

    payload_path = (tmp_path / "nonobject-event" / "ledger.db").resolve()
    payload_ledger = SQLiteExperimentLedger(payload_path)
    payload_ledger.register_campaign(_campaign(), actor=ACTOR)
    payload_ledger.close()
    with sqlite3.connect(payload_path) as connection:
        connection.execute("UPDATE ledger_events SET payload_json = '[]' WHERE sequence = 1")
    with pytest.raises(ExperimentLedgerIntegrityError, match="not a JSON object"):
        SQLiteExperimentLedger(payload_path)


def test_attempt_completion_before_start_is_rejected(tmp_path: Path) -> None:
    ledger = SQLiteExperimentLedger((tmp_path / "completion-order" / "ledger.db").resolve())
    _register_candidate_in_development(ledger)
    attempt = _attempt(
        attempt_id="attempt:h01:completion-order",
        group_id="comparison:h01:completion-order",
        stage=AttemptStage.DEVELOPMENT,
        at=BASE + timedelta(seconds=4),
    )
    ledger.register_attempt(attempt, actor=ACTOR)
    ledger.start_attempt(attempt.attempt_id, actor=ACTOR, started_at=BASE + timedelta(seconds=5))
    with pytest.raises(ExperimentLedgerConflict, match="cannot complete before it starts"):
        ledger.complete_attempt(
            attempt.attempt_id,
            actor=ACTOR,
            completed_at=BASE + timedelta(seconds=4),
            result_summary_sha256=_digest("early-result"),
            artifacts=[_artifact(attempt.attempt_id, BASE + timedelta(seconds=4))],
        )
    ledger.close()
