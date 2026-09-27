"""Freeze deterministic preparation output as an immutable run artifact."""

import json
from datetime import datetime

from finance_research_agent.application.ports import RunRepository
from finance_research_agent.domain.enums import ExecutionStatus
from finance_research_agent.domain.models import RunCheckpoint
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.types import FrozenMap


def _canonical_packet_bytes(packet: ResearchPacket) -> bytes:
    return json.dumps(
        packet.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def stage_research_packet(
    repository: RunRepository,
    packet: ResearchPacket,
    checkpointed_at: datetime,
) -> str:
    """Persist the validated frozen packet and bind its digest to run state."""
    packet = ResearchPacket.model_validate(packet, strict=True)
    if packet.run.execution_status is not ExecutionStatus.AWAITING_SYNTHESIS:
        raise ValueError("research packet run must be AWAITING_SYNTHESIS")
    if packet.run.evidence_cutoff_at is None:
        raise ValueError("research packet requires a frozen evidence cutoff")
    if checkpointed_at < packet.run.evidence_cutoff_at:
        raise ValueError("packet checkpoint cannot precede the evidence cutoff")

    stored = repository.load(packet.run.run_id)
    if (
        stored is None
        or stored.published
        or stored.evidence_cutoff_at != packet.run.evidence_cutoff_at
    ):
        raise ValueError("research packet requires a matching frozen run")

    packet_context = packet.run
    frozen_context = stored.run.model_copy(
        update={
            "execution_status": packet_context.execution_status,
            "data_quality_status": packet_context.data_quality_status,
            "delivery_status": packet_context.delivery_status,
        }
    )
    if frozen_context != packet_context:
        raise ValueError("research packet RunContext differs from frozen RunContext")
    if not stored.checkpoints:
        raise ValueError("research packet requires an EVIDENCE_FROZEN checkpoint")
    latest = stored.checkpoints[-1]
    if latest.stage not in {
        "EVIDENCE_FROZEN",
        ExecutionStatus.AWAITING_SYNTHESIS.value,
        ExecutionStatus.VALIDATING.value,
    }:
        raise ValueError("research packet cannot be staged from the current checkpoint")
    if (
        latest.data_quality_status is not packet_context.data_quality_status
        or latest.delivery_status is not packet_context.delivery_status
    ):
        raise ValueError("research packet status snapshot differs from frozen checkpoint")

    payload = _canonical_packet_bytes(packet)
    digest = repository.stage_artifact(packet.run.run_id, "research_packet", payload)
    artifact_hashes = FrozenMap({"research_packet": digest})
    if latest.stage in {
        ExecutionStatus.AWAITING_SYNTHESIS.value,
        ExecutionStatus.VALIDATING.value,
    }:
        if (
            latest.execution_status is not ExecutionStatus(latest.stage)
            or latest.data_quality_status is not packet_context.data_quality_status
            or latest.delivery_status is not packet_context.delivery_status
            or latest.evidence_cutoff_at != packet_context.evidence_cutoff_at
            or latest.artifact_hashes != artifact_hashes
            or latest.resumable
        ):
            raise ValueError("staged research packet differs from frozen checkpoint")
        return digest

    repository.checkpoint(
        packet.run.run_id,
        RunCheckpoint(
            run_id=packet.run.run_id,
            stage=ExecutionStatus.AWAITING_SYNTHESIS.value,
            execution_status=ExecutionStatus.AWAITING_SYNTHESIS,
            data_quality_status=packet.run.data_quality_status,
            delivery_status=packet.run.delivery_status,
            written_at=checkpointed_at,
            evidence_cutoff_at=packet.run.evidence_cutoff_at,
            artifact_hashes=artifact_hashes,
            resumable=False,
        ),
    )
    return digest
