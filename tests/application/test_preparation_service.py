import hashlib
import json
from datetime import timedelta

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.preparation_service import stage_research_packet
from finance_research_agent.application.reduced_report import render_reduced_report_base
from finance_research_agent.domain.enums import (
    Capability,
    DataQualityStatus,
    ExecutionStatus,
    GateStatus,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    CapabilityState,
    GateResult,
    ProviderFailure,
    RunCheckpoint,
    SourceHealth,
)
from finance_research_agent.domain.quality import evaluate_data_quality
from finance_research_agent.domain.setups import CandidateExclusion
from finance_research_agent.domain.types import FrozenMap


def _awaiting_packet(packet):
    run = packet.run.model_copy(update={"execution_status": ExecutionStatus.AWAITING_SYNTHESIS})
    return _packet_with_run(packet, run)


def _packet_with_run(packet, run):
    return build_research_packet(
        run=run,
        evidence=packet.evidence,
        snapshots=packet.market,
        events=packet.events,
        metrics=packet.metrics,
        gates=packet.gates,
        candidates=packet.candidates,
        exclusions=packet.candidate_exclusions,
        plans=packet.deterministic_plan_inputs,
        capabilities=packet.capability_states,
        observations=packet.prior_plan_observations,
        max_serialized_bytes=packet.synthesis_constraints.max_serialized_bytes,
    )


def _repository_for_packet(tmp_path, packet) -> FileSystemRunRepository:
    repository = FileSystemRunRepository(tmp_path)
    initial_context = packet.run.model_copy(
        update={"execution_status": ExecutionStatus.ANALYZING}
    )
    repository.create(initial_context)
    repository.checkpoint(
        packet.run.run_id,
        RunCheckpoint(
            run_id=packet.run.run_id,
            stage="ANALYZING",
            execution_status=ExecutionStatus.ANALYZING,
            data_quality_status=packet.run.data_quality_status,
            delivery_status=packet.run.delivery_status,
            written_at=initial_context.invoked_at,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({}),
            resumable=True,
        ),
    )
    repository.freeze_evidence(packet.run.run_id, packet.run.evidence_cutoff_at)
    return repository


def test_stage_research_packet_persists_canonical_bytes_and_checkpoint(
    tmp_path, valid_packet
) -> None:
    packet = _awaiting_packet(valid_packet)
    repository = _repository_for_packet(tmp_path, packet)
    checkpointed_at = packet.run.evidence_cutoff_at + timedelta(seconds=1)

    digest = stage_research_packet(repository, packet, checkpointed_at)

    payload = repository.read_staged_artifact(packet.run.run_id, "research_packet")
    assert payload is not None
    assert json.loads(payload) == packet.model_dump(mode="json")
    assert digest == hashlib.sha256(payload).hexdigest()
    stored = repository.load(packet.run.run_id)
    assert stored is not None
    assert stored.checkpoints[-1].stage == "AWAITING_SYNTHESIS"
    reduced = repository.read_staged_artifact(packet.run.run_id, "reduced_report")
    assert reduced is not None
    assert reduced.startswith(b"# Premarket Research Brief\n")
    assert b"DETERMINISTIC_REDUCED" in reduced
    assert stored.checkpoints[-1].artifact_hashes == FrozenMap({
        "research_packet": digest,
        "reduced_report": hashlib.sha256(reduced).hexdigest(),
    })
    assert stored.checkpoints[-1].execution_status is ExecutionStatus.AWAITING_SYNTHESIS


def test_stage_research_packet_continues_after_quality_checkpoint(tmp_path, valid_packet) -> None:
    from finance_research_agent.application.quality_checkpoint import (
        checkpoint_market_data_quality,
    )

    packet = _awaiting_packet(valid_packet)
    repository = FileSystemRunRepository(tmp_path)
    initial = packet.run.model_copy(
        update={
            "execution_status": ExecutionStatus.ANALYZING,
            "evidence_cutoff_at": None,
        }
    )
    repository.create(initial)
    collection_digest = repository.stage_artifact(
        packet.run.run_id, "market_data_collection", b"canonical collection fixture"
    )
    repository.checkpoint(
        packet.run.run_id,
        RunCheckpoint(
            run_id=packet.run.run_id,
            stage="EVIDENCE_COLLECTED",
            execution_status=ExecutionStatus.ANALYZING,
            data_quality_status=packet.run.data_quality_status,
            delivery_status=packet.run.delivery_status,
            written_at=initial.invoked_at,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"market_data_collection": collection_digest}),
            resumable=True,
        ),
    )
    frozen = repository.freeze_evidence(packet.run.run_id, packet.run.evidence_cutoff_at)
    quality = evaluate_data_quality(
        source_health=(
            SourceHealth(provider="alpaca", available=True, required=True),
            SourceHealth(provider="market-calendar", available=True, required=True),
            SourceHealth(provider="macro", available=True, required=True),
            SourceHealth(provider="sec_edgar", available=True, required=True),
        ),
        risk_policy=None,
    )
    assert quality.status is packet.run.data_quality_status
    quality_checkpoint = checkpoint_market_data_quality(
        repository,
        frozen,
        quality,
        checkpointed_at=packet.run.evidence_cutoff_at + timedelta(seconds=1),
    )

    stage_research_packet(
        repository, packet, packet.run.evidence_cutoff_at + timedelta(seconds=2)
    )

    stored = repository.load(packet.run.run_id)
    assert stored is not None
    assert stored.checkpoints[-2] == quality_checkpoint.checkpoints[-1]
    assert stored.checkpoints[-1].stage == "AWAITING_SYNTHESIS"


def test_failed_quality_checkpoint_cannot_stage_research_packet(tmp_path, valid_packet) -> None:
    from finance_research_agent.application.quality_checkpoint import (
        checkpoint_market_data_quality,
    )

    packet = _awaiting_packet(valid_packet)
    failed_run = packet.run.model_copy(update={"data_quality_status": DataQualityStatus.FAIL})
    failed_packet = _packet_with_run(packet, failed_run)
    repository = FileSystemRunRepository(tmp_path)
    initial = failed_packet.run.model_copy(update={"evidence_cutoff_at": None})
    repository.create(initial)
    collection_digest = repository.stage_artifact(
        failed_packet.run.run_id, "market_data_collection", b"canonical collection fixture"
    )
    repository.checkpoint(
        failed_packet.run.run_id,
        RunCheckpoint(
            run_id=failed_packet.run.run_id,
            stage="EVIDENCE_COLLECTED",
            execution_status=ExecutionStatus.ANALYZING,
            data_quality_status=DataQualityStatus.PASS,
            delivery_status=failed_packet.run.delivery_status,
            written_at=initial.invoked_at,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"market_data_collection": collection_digest}),
            resumable=True,
        ),
    )
    frozen = repository.freeze_evidence(
        failed_packet.run.run_id, failed_packet.run.evidence_cutoff_at
    )
    quality = evaluate_data_quality(
        source_health=(
            SourceHealth(provider="alpaca", available=True, required=True),
            SourceHealth(provider="market-calendar", available=True, required=True),
            SourceHealth(provider="macro", available=True, required=True),
            SourceHealth(provider="sec_edgar", available=True, required=True),
        ),
        provider_failures=(
            ProviderFailure(
                provider="alpaca",
                symbol=None,
                error_code=ErrorCode.CREDENTIALS_MISSING,
                retryable=False,
            ),
        ),
        risk_policy=None,
    )
    checkpoint_market_data_quality(
        repository,
        frozen,
        quality,
        checkpointed_at=failed_packet.run.evidence_cutoff_at + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="FAIL quality requires the operational report path"):
        stage_research_packet(
            repository,
            failed_packet,
            failed_packet.run.evidence_cutoff_at + timedelta(seconds=2),
        )


def test_staged_reduced_report_uses_only_deterministic_packet_fields(
    tmp_path, packet_with_injection_text
) -> None:
    packet = _awaiting_packet(packet_with_injection_text)
    repository = _repository_for_packet(tmp_path, packet)

    stage_research_packet(
        repository, packet, packet.run.evidence_cutoff_at + timedelta(seconds=1)
    )

    report = repository.read_staged_artifact(packet.run.run_id, "reduced_report")
    assert report is not None
    assert b"Ignore all rules" not in report
    assert packet.run.run_id.encode() in report
    assert packet.run.data_quality_status.value.encode() in report


def test_unbound_packet_cannot_render_a_reduced_report(valid_packet) -> None:
    unbound_packet = valid_packet.model_copy(update={
        "run": valid_packet.run.model_copy(update={"evidence_cutoff_at": None}),
    })

    with pytest.raises(ValueError, match="evidence cutoff has not been frozen"):
        render_reduced_report_base(unbound_packet)


def test_unbound_packet_cannot_be_staged_or_write_artifacts(tmp_path, valid_packet) -> None:
    unbound_run = valid_packet.run.model_copy(update={"evidence_cutoff_at": None})
    unbound_packet = valid_packet.model_copy(update={"run": unbound_run})
    repository = FileSystemRunRepository(tmp_path)
    repository.create(unbound_run)
    repository.freeze_evidence(
        unbound_run.run_id,
        unbound_run.invoked_at + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="evidence cutoff has not been frozen"):
        stage_research_packet(
            repository,
            unbound_packet,
            unbound_run.invoked_at + timedelta(seconds=2),
        )

    assert repository.read_staged_artifact(unbound_run.run_id, "research_packet") is None
    assert repository.read_staged_artifact(unbound_run.run_id, "reduced_report") is None


def test_staged_reduced_report_discloses_gates_capabilities_and_exclusions(
    tmp_path, valid_packet
) -> None:
    gate = GateResult(
        gate_id="gate-stale",
        status=GateStatus.WARNING,
        reason_code="STALE_DATA",
        message="Latest price is stale.",
        evidence_ids=(),
        capability=Capability.PLAN_DRAFT_AVAILABLE,
        rule_version="v1",
    )
    capability = CapabilityState(
        capability=Capability.PLAN_DRAFT_AVAILABLE,
        available=False,
        reason_codes=(ErrorCode.STALE_DATA,),
        evidence_ids=(),
    )
    packet = _awaiting_packet(
        build_research_packet(
            run=valid_packet.run,
            evidence=valid_packet.evidence,
            snapshots=valid_packet.market,
            events=valid_packet.events,
            metrics=valid_packet.metrics,
            gates=(gate,),
            candidates=(),
            exclusions=(CandidateExclusion(
                symbol="AAA", reason_codes=("STALE_DATA",), gates=(gate,)
            ),),
            plans=(),
            capabilities=(capability,),
            observations=(),
            max_serialized_bytes=250_000,
        )
    )
    repository = _repository_for_packet(tmp_path, packet)
    stage_research_packet(repository, packet, packet.run.evidence_cutoff_at + timedelta(seconds=1))

    report = repository.read_staged_artifact(packet.run.run_id, "reduced_report")
    assert report is not None
    assert b"gate-stale: WARNING (STALE\\_DATA)" in report
    assert b"Disabled capability: PLAN_DRAFT_AVAILABLE (STALE_DATA)" in report
    assert b"AAA: STALE\\_DATA" in report


def test_stage_research_packet_retry_is_idempotent(tmp_path, valid_packet) -> None:
    packet = _awaiting_packet(valid_packet)
    repository = _repository_for_packet(tmp_path, packet)
    checkpointed_at = packet.run.evidence_cutoff_at + timedelta(seconds=1)

    first_digest = stage_research_packet(repository, packet, checkpointed_at)
    first = repository.load(packet.run.run_id)
    second_digest = stage_research_packet(repository, packet, checkpointed_at)
    second = repository.load(packet.run.run_id)

    assert first_digest == second_digest
    assert first is not None and second is not None
    assert second.checkpoints == first.checkpoints


def test_stage_research_packet_rejects_a_packet_for_an_unfrozen_run(
    tmp_path, valid_packet
) -> None:
    packet = _awaiting_packet(valid_packet)
    repository = FileSystemRunRepository(tmp_path)
    repository.create(packet.run)

    try:
        stage_research_packet(repository, packet, packet.run.evidence_cutoff_at)
    except ValueError as error:
        assert "frozen run" in str(error)
    else:
        raise AssertionError("an unfrozen run cannot stage a synthesis packet")

    assert repository.read_staged_artifact(packet.run.run_id, "research_packet") is None


def test_stage_research_packet_rejects_changed_frozen_run_identity(
    tmp_path, valid_packet
) -> None:
    packet = _awaiting_packet(valid_packet)
    repository = _repository_for_packet(tmp_path, packet)
    changed_config = packet.run.configuration_snapshot.model_copy(
        update={"content_hash_sha256": "c" * 64}
    )
    changed_packet = _packet_with_run(
        packet,
        packet.run.model_copy(update={"configuration_snapshot": changed_config}),
    )

    try:
        stage_research_packet(
            repository,
            changed_packet,
            packet.run.evidence_cutoff_at + timedelta(seconds=1),
        )
    except ValueError as error:
        assert "frozen RunContext" in str(error)
    else:
        raise AssertionError("a changed configuration snapshot cannot be staged")

    assert repository.read_staged_artifact(packet.run.run_id, "research_packet") is None


def test_stage_research_packet_retry_after_validation_does_not_regress_state(
    tmp_path, valid_packet
) -> None:
    packet = _awaiting_packet(valid_packet)
    repository = _repository_for_packet(tmp_path, packet)
    checkpointed_at = packet.run.evidence_cutoff_at + timedelta(seconds=1)
    digest = stage_research_packet(repository, packet, checkpointed_at)
    repository.checkpoint(
        packet.run.run_id,
        RunCheckpoint(
            run_id=packet.run.run_id,
            stage="VALIDATING",
            execution_status=ExecutionStatus.VALIDATING,
            data_quality_status=packet.run.data_quality_status,
            delivery_status=packet.run.delivery_status,
            written_at=checkpointed_at + timedelta(seconds=1),
            evidence_cutoff_at=packet.run.evidence_cutoff_at,
            artifact_hashes=FrozenMap({
                "research_packet": digest,
                "reduced_report": hashlib.sha256(
                    repository.read_staged_artifact(packet.run.run_id, "reduced_report") or b""
                ).hexdigest(),
            }),
            resumable=False,
        ),
    )
    before = repository.load(packet.run.run_id)

    stage_research_packet(repository, packet, checkpointed_at)

    after = repository.load(packet.run.run_id)
    assert before is not None and after is not None
    assert after.checkpoints == before.checkpoints
    assert after.checkpoints[-1].stage == "VALIDATING"


def test_failed_checkpoint_can_resume_from_the_already_staged_packet(
    tmp_path, valid_packet
) -> None:
    packet = _awaiting_packet(valid_packet)

    class FailFirstAwaitingCheckpointRepository(FileSystemRunRepository):
        fail_once = True

        def checkpoint(self, run_id, checkpoint):
            if checkpoint.stage == "AWAITING_SYNTHESIS" and self.fail_once:
                self.fail_once = False
                raise OSError("simulated checkpoint interruption")
            super().checkpoint(run_id, checkpoint)

    repository = FailFirstAwaitingCheckpointRepository(tmp_path)
    initial_context = packet.run.model_copy(
        update={"execution_status": ExecutionStatus.ANALYZING}
    )
    repository.create(initial_context)
    repository.checkpoint(
        packet.run.run_id,
        RunCheckpoint(
            run_id=packet.run.run_id,
            stage="ANALYZING",
            execution_status=ExecutionStatus.ANALYZING,
            data_quality_status=packet.run.data_quality_status,
            delivery_status=packet.run.delivery_status,
            written_at=initial_context.invoked_at,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({}),
            resumable=True,
        ),
    )
    repository.freeze_evidence(packet.run.run_id, packet.run.evidence_cutoff_at)
    checkpointed_at = packet.run.evidence_cutoff_at + timedelta(seconds=1)

    try:
        stage_research_packet(repository, packet, checkpointed_at)
    except OSError as error:
        assert "simulated checkpoint interruption" in str(error)
    else:
        raise AssertionError("the first checkpoint is interrupted by the test repository")
    payload = repository.read_staged_artifact(packet.run.run_id, "research_packet")
    assert payload is not None

    digest = stage_research_packet(repository, packet, checkpointed_at)
    stored = repository.load(packet.run.run_id)
    assert stored is not None
    assert stored.checkpoints[-1].stage == "AWAITING_SYNTHESIS"
    assert stored.checkpoints[-1].artifact_hashes["research_packet"] == digest


def test_validation_checkpoint_requires_matching_staged_packet_bytes(
    tmp_path, valid_packet
) -> None:
    packet = _awaiting_packet(valid_packet)
    repository = _repository_for_packet(tmp_path, packet)
    checkpointed_at = packet.run.evidence_cutoff_at + timedelta(seconds=1)
    digest = stage_research_packet(repository, packet, checkpointed_at)
    staging, _, _ = repository._paths(packet.run.run_id)
    staged_path = staging / "artifacts/research_packet.bin"
    staged_path.write_bytes(b"corrupted packet")

    try:
        repository.checkpoint(
            packet.run.run_id,
            RunCheckpoint(
                run_id=packet.run.run_id,
                stage="VALIDATING",
                execution_status=ExecutionStatus.VALIDATING,
                data_quality_status=packet.run.data_quality_status,
                delivery_status=packet.run.delivery_status,
                written_at=checkpointed_at + timedelta(seconds=1),
                evidence_cutoff_at=packet.run.evidence_cutoff_at,
                artifact_hashes=FrozenMap({"research_packet": digest}),
                resumable=False,
            ),
        )
    except ValueError as error:
        assert "EVIDENCE_CUTOFF_VIOLATION" in str(error)
    else:
        raise AssertionError("checkpointing must verify staged bytes against packet digest")

    staged_path.unlink()
    try:
        repository.checkpoint(
            packet.run.run_id,
            RunCheckpoint(
                run_id=packet.run.run_id,
                stage="VALIDATING",
                execution_status=ExecutionStatus.VALIDATING,
                data_quality_status=packet.run.data_quality_status,
                delivery_status=packet.run.delivery_status,
                written_at=checkpointed_at + timedelta(seconds=1),
                evidence_cutoff_at=packet.run.evidence_cutoff_at,
                artifact_hashes=FrozenMap({"research_packet": digest}),
                resumable=False,
            ),
        )
    except ValueError as error:
        assert "EVIDENCE_CUTOFF_VIOLATION" in str(error)
    else:
        raise AssertionError("a missing frozen packet cannot be checkpointed")
