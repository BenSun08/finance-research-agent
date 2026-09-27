"""Checkpointed brief validation and publication from one frozen packet."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.preparation_service import stage_research_packet
from finance_research_agent.application.publication_service import (
    publish_validated_brief,
    validate_staged_brief,
)
from finance_research_agent.domain.enums import ExecutionStatus
from finance_research_agent.domain.models import RunCheckpoint
from finance_research_agent.domain.types import FrozenMap


def _prepared(tmp_path, valid_packet):
    run = valid_packet.run.model_copy(
        update={"execution_status": ExecutionStatus.AWAITING_SYNTHESIS}
    )
    packet = build_research_packet(
        run=run,
        evidence=valid_packet.evidence,
        snapshots=valid_packet.market,
        events=valid_packet.events,
        metrics=valid_packet.metrics,
        gates=valid_packet.gates,
        candidates=valid_packet.candidates,
        exclusions=valid_packet.candidate_exclusions,
        plans=valid_packet.deterministic_plan_inputs,
        capabilities=valid_packet.capability_states,
        observations=valid_packet.prior_plan_observations,
        max_serialized_bytes=valid_packet.synthesis_constraints.max_serialized_bytes,
    )
    repo = FileSystemRunRepository(tmp_path)
    initial = run.model_copy(update={"execution_status": ExecutionStatus.ANALYZING})
    repo.create(initial)
    repo.checkpoint(
        run.run_id,
        RunCheckpoint(
            run_id=run.run_id,
            stage="ANALYZING",
            execution_status=ExecutionStatus.ANALYZING,
            data_quality_status=run.data_quality_status,
            delivery_status=run.delivery_status,
            written_at=run.invoked_at,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({}),
            resumable=True,
        ),
    )
    repo.freeze_evidence(run.run_id, run.evidence_cutoff_at)
    at = run.evidence_cutoff_at + timedelta(seconds=1)
    stage_research_packet(repo, packet, at)
    return repo, packet, at


def test_validation_records_attempt_and_retry_without_using_repair(
    tmp_path, valid_packet, valid_brief_draft
):
    repo, packet, at = _prepared(tmp_path, valid_packet)
    bad = valid_brief_draft.model_copy(update={"run_id": "premarket-2026-01-01-r2"})

    first, repair = validate_staged_brief(repo, packet, bad, at)
    repeated, repeated_repair = validate_staged_brief(repo, packet, bad, at)

    assert not first.is_valid and first.validation_attempt == 1
    assert repair is not None and repair.validation_attempt == 2
    assert repeated == first and repeated_repair == repair
    stored = repo.load(packet.run.run_id)
    assert stored is not None
    assert [c.stage for c in stored.checkpoints].count("VALIDATING") == 1
    assert len(stored.checkpoints[-1].artifact_hashes) == 4
    assert stored.checkpoints[-1].artifact_hashes["reduced_report"] == (
        stored.checkpoints[-2].artifact_hashes["reduced_report"]
    )


def test_validation_limits_repair_to_two_attempts(tmp_path, valid_packet, valid_brief_draft):
    repo, packet, at = _prepared(tmp_path, valid_packet)
    for attempt in range(1, 4):
        bad = valid_brief_draft.model_copy(
            update={"run_id": f"premarket-2026-01-01-r{attempt + 1}"}
        )
        report, repair = validate_staged_brief(repo, packet, bad, at + timedelta(seconds=attempt))
        assert report.validation_attempt == attempt
        assert (repair is not None) == (attempt < 3)
    with pytest.raises(ValueError, match="repair|attempt"):
        validate_staged_brief(repo, packet, valid_brief_draft, at + timedelta(seconds=4))


def test_validation_cannot_backdate_staged_checkpoint(
    tmp_path, valid_packet, valid_brief_draft
):
    repo, packet, at = _prepared(tmp_path, valid_packet)
    backdated = at - timedelta(milliseconds=500)
    assert backdated > packet.run.evidence_cutoff_at

    with pytest.raises(ValueError, match="current checkpoint"):
        validate_staged_brief(repo, packet, valid_brief_draft, backdated)

    stored = repo.load(packet.run.run_id)
    assert stored is not None and stored.checkpoints[-1].stage == "AWAITING_SYNTHESIS"


def test_concurrent_validation_cannot_record_duplicate_attempts(
    tmp_path, valid_packet, valid_brief_draft
):
    barrier = Barrier(2)

    class ContendedRepository(FileSystemRunRepository):
        def stage_artifact(self, run_id, artifact_name, payload):
            digest = super().stage_artifact(run_id, artifact_name, payload)
            if artifact_name.startswith("validation_report_"):
                barrier.wait(timeout=5)
            return digest

    repo, packet, at = _prepared(tmp_path, valid_packet)
    contended = ContendedRepository(tmp_path)
    drafts = tuple(
        valid_brief_draft.model_copy(
            update={"run_id": f"premarket-2026-01-01-r{revision}"}
        )
        for revision in (2, 3)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(validate_staged_brief, contended, packet, draft, at)
            for draft in drafts
        ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except ValueError:
                outcomes.append(None)
    assert sum(outcome is not None for outcome in outcomes) == 1
    stored = repo.load(packet.run.run_id)
    assert stored is not None and [c.stage for c in stored.checkpoints].count("VALIDATING") == 1


def test_publication_requires_valid_recorded_attempt_and_is_replayable(
    tmp_path, valid_packet, valid_brief_draft
):
    repo, packet, at = _prepared(tmp_path, valid_packet)
    with pytest.raises(ValueError, match="valid"):
        publish_validated_brief(repo, packet, at)
    draft = valid_brief_draft.model_copy(
        update={"execution_status": ExecutionStatus.AWAITING_SYNTHESIS}
    )
    report, _ = validate_staged_brief(repo, packet, draft, at)
    assert report.is_valid

    before_publication = datetime.now(UTC)
    artifact = publish_validated_brief(repo, packet, at + timedelta(seconds=1))

    assert artifact.run_id == packet.run.run_id
    assert artifact.published_at >= before_publication
    stored = repo.load(packet.run.run_id)
    assert stored is not None and stored.published
    assert stored.checkpoints[-1].stage == "PUBLISHED"
    bundle = repo.load_published_bundle(packet.run.run_id)
    assert bundle is not None and bundle.run.execution_status is ExecutionStatus.PUBLISHED
    assert bundle.model_dump(mode="json")["bundle"]["research_packet"] == packet.model_dump(
        mode="json"
    )
    assert repo.get_report(packet.run.run_id).startswith("# Premarket Research Brief")
    assert publish_validated_brief(repo, packet, at + timedelta(seconds=2)) == artifact


def test_invalid_attempt_cannot_publish(tmp_path, valid_packet, valid_brief_draft):
    repo, packet, at = _prepared(tmp_path, valid_packet)
    bad = valid_brief_draft.model_copy(update={"run_id": "premarket-2026-01-01-r2"})
    validate_staged_brief(repo, packet, bad, at)
    with pytest.raises(ValueError, match="valid"):
        publish_validated_brief(repo, packet, at + timedelta(seconds=1))
    stored = repo.load(packet.run.run_id)
    assert stored is not None and stored.checkpoints[-1].stage == "VALIDATING"


def test_publication_retries_after_atomic_failure(tmp_path, valid_packet, valid_brief_draft):
    repo, packet, at = _prepared(tmp_path, valid_packet)
    draft = valid_brief_draft.model_copy(
        update={"execution_status": ExecutionStatus.AWAITING_SYNTHESIS}
    )
    validate_staged_brief(repo, packet, draft, at)
    repo.inject_failure_before_rename = True
    with pytest.raises(Exception, match="injected failure"):
        publish_validated_brief(repo, packet, at + timedelta(seconds=1))
    repo.inject_failure_before_rename = False

    artifact = publish_validated_brief(repo, packet, at + timedelta(seconds=2))

    assert artifact.run_id == packet.run.run_id
    stored = repo.load(packet.run.run_id)
    assert stored is not None and [c.stage for c in stored.checkpoints].count("PUBLISHED") == 1
