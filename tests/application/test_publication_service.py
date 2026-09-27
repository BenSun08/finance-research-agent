"""Checkpointed brief validation and publication from one frozen packet."""

from datetime import timedelta

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.preparation_service import stage_research_packet
from finance_research_agent.application.publication_service import (
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
    assert len(stored.checkpoints[-1].artifact_hashes) == 3


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
