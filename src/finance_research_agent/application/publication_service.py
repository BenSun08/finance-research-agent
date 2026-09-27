"""Bounded validation and publication of one staged Product A packet."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from typing import Protocol

from finance_research_agent.application.ports import PublishedArtifactReader, RunRepository
from finance_research_agent.application.preparation_service import _canonical_packet_bytes
from finance_research_agent.application.report_renderer import render_markdown_report
from finance_research_agent.application.run_state import compose_publication_context
from finance_research_agent.domain.enums import ExecutionStatus
from finance_research_agent.domain.models import (
    PublishedArtifact,
    PublishedRunBundle,
    RunCheckpoint,
)
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.domain.validation import (
    RepairContext,
    ResearchBriefDraft,
    ValidationReport,
    create_repair_context,
    validate_research_brief,
)


class PublicationRepository(RunRepository, PublishedArtifactReader, Protocol):
    """Staged-write and published-read ports required for idempotent publication."""

    def get_published_artifact(self, run_id: str) -> PublishedArtifact | None: ...


def _staged_packet_hash(
    repository: RunRepository,
    packet: ResearchPacket,
    allowed_stages: frozenset[str] = frozenset({"AWAITING_SYNTHESIS", "VALIDATING"}),
) -> str:
    run_id = packet.run.run_id
    stored = repository.load(run_id)
    if stored is None or stored.published or not stored.checkpoints:
        raise ValueError("validation requires an unpublished staged run")
    latest = stored.checkpoints[-1]
    if latest.stage not in allowed_stages:
        raise ValueError("validation requires a staged synthesis packet")
    payload = repository.read_staged_artifact(run_id, "research_packet")
    digest = sha256(_canonical_packet_bytes(packet)).hexdigest()
    if (
        payload != _canonical_packet_bytes(packet)
        or latest.artifact_hashes.get("research_packet") != digest
        or stored.evidence_cutoff_at != packet.run.evidence_cutoff_at
        or latest.data_quality_status is not packet.run.data_quality_status
        or latest.delivery_status is not packet.run.delivery_status
    ):
        raise ValueError("staged research packet differs from frozen packet")
    return digest


def _checkpoint_artifact(
    repository: RunRepository, checkpoint: RunCheckpoint, prefix: str
) -> bytes:
    names = [name for name in checkpoint.artifact_hashes if name.startswith(prefix)]
    if len(names) != 1:
        raise ValueError("validation checkpoint has no unique recorded artifact")
    name = names[0]
    payload = repository.read_staged_artifact(checkpoint.run_id, name)
    if payload is None or sha256(payload).hexdigest() != checkpoint.artifact_hashes[name]:
        raise ValueError("recorded validation artifact differs from staged bytes")
    return payload


def _recorded_attempt(
    repository: RunRepository, checkpoint: RunCheckpoint
) -> tuple[ResearchBriefDraft, ValidationReport]:
    draft = ResearchBriefDraft.model_validate_json(
        _checkpoint_artifact(repository, checkpoint, "brief_draft_")
    )
    report = ValidationReport.model_validate_json(
        _checkpoint_artifact(repository, checkpoint, "validation_report_")
    )
    return draft, report


def validate_staged_brief(
    repository: RunRepository,
    packet: ResearchPacket,
    draft: ResearchBriefDraft,
    checkpointed_at: datetime,
) -> tuple[ValidationReport, RepairContext | None]:
    """Record one deterministic attempt; retries of its exact draft are idempotent."""
    packet = ResearchPacket.model_validate(packet, strict=True)
    draft = ResearchBriefDraft.model_validate(draft, strict=True)
    if checkpointed_at < packet.run.evidence_cutoff_at:
        raise ValueError("validation checkpoint cannot precede the evidence cutoff")
    packet_hash = _staged_packet_hash(repository, packet)
    stored = repository.load(packet.run.run_id)
    assert stored is not None
    attempts = tuple(c for c in stored.checkpoints if c.stage == "VALIDATING")
    if attempts:
        previous_draft, previous_report = _recorded_attempt(repository, attempts[-1])
        if (
            previous_report.packet_sha256 != packet.canonical_sha256
            or previous_report.validation_attempt != len(attempts)
            or previous_report != validate_research_brief(
                packet, previous_draft, previous_report.validation_attempt
            )
        ):
            raise ValueError("previous validation differs from frozen packet")
        if previous_draft == draft:
            repair = (
                create_repair_context(packet, previous_report)
                if previous_report.repairable else None
            )
            return previous_report, repair
        repair_context = create_repair_context(packet, previous_report)
        attempt = repair_context.validation_attempt
    else:
        attempt = 1
    if checkpointed_at < stored.checkpoints[-1].written_at:
        raise ValueError("validation checkpoint cannot precede current checkpoint")
    report = validate_research_brief(packet, draft, validation_attempt=attempt)
    draft_payload = canonical_bytes(draft)
    report_payload = canonical_bytes(report)
    draft_name = "brief_draft_" + sha256(draft_payload).hexdigest()
    report_name = "validation_report_" + sha256(report_payload).hexdigest()
    draft_hash = repository.stage_artifact(packet.run.run_id, draft_name, draft_payload)
    report_hash = repository.stage_artifact(packet.run.run_id, report_name, report_payload)
    reduced_hash = stored.checkpoints[-1].artifact_hashes.get("reduced_report")
    checkpoint_hashes = {
        "research_packet": packet_hash,
        draft_name: draft_hash,
        report_name: report_hash,
    }
    if reduced_hash is not None:
        checkpoint_hashes["reduced_report"] = reduced_hash
    repository.checkpoint_if_current(
        packet.run.run_id,
        RunCheckpoint(
            run_id=packet.run.run_id,
            stage="VALIDATING",
            execution_status=ExecutionStatus.VALIDATING,
            data_quality_status=packet.run.data_quality_status,
            delivery_status=packet.run.delivery_status,
            written_at=checkpointed_at,
            evidence_cutoff_at=packet.run.evidence_cutoff_at,
            artifact_hashes=FrozenMap(checkpoint_hashes),
            resumable=False,
        ),
        expected_count=len(stored.checkpoints),
    )
    repair = create_repair_context(packet, report) if report.repairable else None
    return report, repair


def publish_validated_brief(
    repository: PublicationRepository,
    packet: ResearchPacket,
    checkpointed_at: datetime,
) -> PublishedArtifact:
    """Render and atomically publish only the latest valid recorded attempt."""
    packet = ResearchPacket.model_validate(packet, strict=True)
    run_id = packet.run.run_id
    stored = repository.load(run_id)
    if stored is None or not stored.checkpoints:
        raise ValueError("publication requires a valid recorded attempt")
    if stored.published:
        bundle = repository.load_published_bundle(run_id)
        report = repository.get_report(run_id)
        artifact = repository.get_published_artifact(run_id)
        if (
            bundle is None
            or report is None
            or artifact is None
            or bundle.model_dump(mode="json")["bundle"].get("research_packet")
            != packet.model_dump(mode="json")
            or bundle.markdown_sha256 != sha256(report.encode("utf-8")).hexdigest()
            or stored.checkpoints[-1].stage != "PUBLISHED"
        ):
            raise ValueError("published artifact differs from the supplied frozen packet")
        return artifact
    if checkpointed_at < packet.run.evidence_cutoff_at:
        raise ValueError("publication checkpoint cannot precede the evidence cutoff")
    _staged_packet_hash(repository, packet, frozenset({"VALIDATING", "PUBLISHED"}))
    latest = stored.checkpoints[-1]
    attempts = tuple(c for c in stored.checkpoints if c.stage == "VALIDATING")
    if not attempts or latest.stage not in {"VALIDATING", "PUBLISHED"}:
        raise ValueError("publication requires a valid recorded attempt")
    draft, validation = _recorded_attempt(repository, attempts[-1])
    if (
        not validation.is_valid
        or validation.validation_attempt != len(attempts)
        or validation.run_id != run_id
        or checkpointed_at < latest.written_at
    ):
        raise ValueError("publication requires a valid recorded attempt")
    markdown = render_markdown_report(packet, draft, validation)
    if latest.stage == "VALIDATING":
        latest = RunCheckpoint(
            run_id=run_id,
            stage="PUBLISHED",
            execution_status=ExecutionStatus.PUBLISHED,
            data_quality_status=packet.run.data_quality_status,
            delivery_status=packet.run.delivery_status,
            written_at=checkpointed_at,
            evidence_cutoff_at=packet.run.evidence_cutoff_at,
            artifact_hashes=latest.artifact_hashes,
            resumable=False,
        )
        repository.checkpoint_if_current(
            run_id, latest, expected_count=len(stored.checkpoints)
        )
    bundle = PublishedRunBundle(
        run=compose_publication_context(stored.run, latest),
        bundle=FrozenMap({
            "research_packet": json.loads(_canonical_packet_bytes(packet)),
            "brief_draft": json.loads(canonical_bytes(draft)),
            "validation_report": json.loads(canonical_bytes(validation)),
        }),
        report_markdown=markdown,
        markdown_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
    )
    return repository.publish_atomically(bundle)
