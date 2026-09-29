"""Bounded validation and publication of one staged Product A packet."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from typing import Protocol

from finance_research_agent.application.operational_report import render_operational_report
from finance_research_agent.application.performance_telemetry import (
    RunTelemetryRecorder,
    checkpoint_with_telemetry,
    load_checkpoint_telemetry,
    load_latest_checkpoint_telemetry,
)
from finance_research_agent.application.ports import PublishedArtifactReader, RunRepository
from finance_research_agent.application.preparation_service import _canonical_packet_bytes
from finance_research_agent.application.reduced_report import (
    render_reduced_report,
    render_reduced_report_base,
)
from finance_research_agent.application.report_renderer import render_markdown_report
from finance_research_agent.application.run_state import compose_publication_context
from finance_research_agent.domain.enums import (
    BriefOrigin,
    DataQualityStatus,
    ExecutionStatus,
    ReducedReportReason,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    PerformanceTelemetry,
    PublishedArtifact,
    PublishedRunBundle,
    RunCheckpoint,
    RunContext,
    StoredRun,
)
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.types import FrozenMap, JsonValue, canonical_bytes
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


def _published_telemetry(
    bundle: PublishedRunBundle, checkpoint: RunCheckpoint
) -> PerformanceTelemetry | None:
    """Validate frozen telemetry against its final checkpoint hash."""
    contents = bundle.model_dump(mode="json")["bundle"]
    value = contents.get("performance_telemetry")
    digest = contents.get("performance_telemetry_sha256")
    names = [
        name for name in checkpoint.artifact_hashes
        if name.startswith("performance_telemetry_")
    ]
    if value is None and digest is None and not names:
        return None
    if value is None or not isinstance(digest, str) or len(names) != 1:
        raise ValueError("published telemetry value and checkpoint hash must appear together")
    if (
        checkpoint.artifact_hashes[names[0]] != digest
        or names[0] != "performance_telemetry_" + digest
    ):
        raise ValueError("published telemetry differs from its checkpoint hash")
    try:
        telemetry = PerformanceTelemetry.model_validate(value, strict=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("published telemetry is malformed") from exc
    if sha256(canonical_bytes(telemetry)).hexdigest() != digest:
        raise ValueError("published telemetry does not match its checkpoint hash")
    return telemetry


def _publication_recorder(
    repository: PublicationRepository, stored: StoredRun
) -> RunTelemetryRecorder:
    return RunTelemetryRecorder(initial=load_latest_checkpoint_telemetry(repository, stored))


def _bind_publication_telemetry(
    repository: PublicationRepository,
    checkpoint: RunCheckpoint,
    recorder: RunTelemetryRecorder,
) -> tuple[RunCheckpoint, PerformanceTelemetry, str]:
    telemetry = recorder.snapshot()
    bound = checkpoint_with_telemetry(repository, checkpoint, telemetry)
    name = next(
        name for name in bound.artifact_hashes if name.startswith("performance_telemetry_")
    )
    return bound, telemetry, bound.artifact_hashes[name]


def _telemetry_bundle_fields(
    telemetry: PerformanceTelemetry, digest: str
) -> dict[str, JsonValue]:
    return {
        "performance_telemetry": FrozenMap[str, JsonValue](
            telemetry.model_dump(mode="json")
        ),
        "performance_telemetry_sha256": digest,
    }


def _staged_packet_hash(
    repository: RunRepository,
    packet: ResearchPacket,
    allowed_stages: frozenset[str] = frozenset({"AWAITING_SYNTHESIS", "VALIDATING"}),
) -> str:
    run_id = packet.run.run_id
    stored = repository.load(run_id)
    if stored is None or stored.published or not stored.checkpoints:
        raise ValueError("validation requires an unpublished staged run")
    evidence_cutoff_at = packet.run.require_evidence_cutoff()
    latest = stored.checkpoints[-1]
    if latest.stage not in allowed_stages:
        raise ValueError("validation requires a staged synthesis packet")
    payload = repository.read_staged_artifact(run_id, "research_packet")
    digest = sha256(_canonical_packet_bytes(packet)).hexdigest()
    if (
        payload != _canonical_packet_bytes(packet)
        or latest.artifact_hashes.get("research_packet") != digest
        or stored.evidence_cutoff_at != evidence_cutoff_at
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
    *,
    telemetry: RunTelemetryRecorder | None = None,
) -> tuple[ValidationReport, RepairContext | None]:
    """Record one deterministic attempt; retries of its exact draft are idempotent."""
    packet = ResearchPacket.model_validate(packet, strict=True)
    draft = ResearchBriefDraft.model_validate(draft, strict=True)
    if draft.origin is not BriefOrigin.SYNTHESIZED:
        raise ValueError("caller supplied brief origin must be SYNTHESIZED")
    evidence_cutoff_at = packet.run.require_evidence_cutoff()
    if checkpointed_at < evidence_cutoff_at:
        raise ValueError("validation checkpoint cannot precede the evidence cutoff")
    packet_hash = _staged_packet_hash(repository, packet)
    stored = repository.load(packet.run.run_id)
    assert stored is not None
    if telemetry is None:
        telemetry = RunTelemetryRecorder()
    previous_telemetry = load_latest_checkpoint_telemetry(repository, stored)
    if previous_telemetry is not None:
        telemetry.restore(previous_telemetry)
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
    with telemetry.measure_stage("VALIDATION"):
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
    checkpoint = RunCheckpoint(
        run_id=packet.run.run_id,
        stage="VALIDATING",
        execution_status=ExecutionStatus.VALIDATING,
        data_quality_status=packet.run.data_quality_status,
        delivery_status=packet.run.delivery_status,
        written_at=checkpointed_at,
        evidence_cutoff_at=evidence_cutoff_at,
        artifact_hashes=FrozenMap(checkpoint_hashes),
        resumable=False,
    )
    telemetry_snapshot = telemetry.snapshot(
        research_packet_bytes=len(_canonical_packet_bytes(packet)),
        validation_attempts=attempt,
    )
    checkpoint = checkpoint_with_telemetry(repository, checkpoint, telemetry_snapshot)
    repository.checkpoint_if_current(
        packet.run.run_id,
        checkpoint,
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
        contents = bundle.model_dump(mode="json")["bundle"] if bundle is not None else None
        if (
            bundle is None
            or contents is None
            or report is None
            or artifact is None
            or contents.get("research_packet") != packet.model_dump(mode="json")
            or contents.get("brief_draft", {}).get("origin")
            != BriefOrigin.SYNTHESIZED.value
            or bundle.markdown_sha256 != sha256(report.encode("utf-8")).hexdigest()
            or stored.checkpoints[-1].stage != "PUBLISHED"
        ):
            raise ValueError("published artifact origin or frozen packet differs")
        if bundle is not None:
            _published_telemetry(bundle, stored.checkpoints[-1])
        return artifact
    evidence_cutoff_at = packet.run.require_evidence_cutoff()
    if checkpointed_at < evidence_cutoff_at:
        raise ValueError("publication checkpoint cannot precede the evidence cutoff")
    _staged_packet_hash(repository, packet, frozenset({"VALIDATING", "PUBLISHED"}))
    latest = stored.checkpoints[-1]
    attempts = tuple(c for c in stored.checkpoints if c.stage == "VALIDATING")
    if not attempts or latest.stage not in {"VALIDATING", "PUBLISHED"}:
        raise ValueError("publication requires a valid recorded attempt")
    draft, validation = _recorded_attempt(repository, attempts[-1])
    if (
        not validation.is_valid
        or draft.origin is not BriefOrigin.SYNTHESIZED
        or validation.validation_attempt != len(attempts)
        or validation.run_id != run_id
        or checkpointed_at < latest.written_at
    ):
        raise ValueError("publication requires a valid recorded attempt")
    telemetry: PerformanceTelemetry
    telemetry_digest: str
    if latest.stage == "VALIDATING":
        recorder = _publication_recorder(repository, stored)
        with recorder.measure_stage("PUBLICATION"):
            markdown = render_markdown_report(packet, draft, validation)
        latest = RunCheckpoint(
            run_id=run_id,
            stage="PUBLISHED",
            execution_status=ExecutionStatus.PUBLISHED,
            data_quality_status=packet.run.data_quality_status,
            delivery_status=packet.run.delivery_status,
            written_at=checkpointed_at,
            evidence_cutoff_at=evidence_cutoff_at,
            artifact_hashes=latest.artifact_hashes,
            resumable=False,
        )
        latest, telemetry, telemetry_digest = _bind_publication_telemetry(
            repository, latest, recorder
        )
        repository.checkpoint_if_current(
            run_id, latest, expected_count=len(stored.checkpoints)
        )
    else:
        frozen_telemetry = load_checkpoint_telemetry(repository, latest)
        if frozen_telemetry is None:
            raise ValueError("published checkpoint is missing staged telemetry")
        telemetry = frozen_telemetry
        telemetry_name = next(
            name for name in latest.artifact_hashes
            if name.startswith("performance_telemetry_")
        )
        telemetry_digest = latest.artifact_hashes[telemetry_name]
        markdown = render_markdown_report(packet, draft, validation)
    bundle_contents = {
        "research_packet": json.loads(_canonical_packet_bytes(packet)),
        "brief_draft": json.loads(canonical_bytes(draft)),
        "validation_report": json.loads(canonical_bytes(validation)),
        **_telemetry_bundle_fields(telemetry, telemetry_digest),
    }
    bundle = PublishedRunBundle(
        run=compose_publication_context(stored.run, latest),
        bundle=FrozenMap(bundle_contents),
        report_markdown=markdown,
        markdown_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
    )
    return repository.publish_atomically(bundle)


def publish_reduced_report(
    repository: PublicationRepository,
    packet: ResearchPacket,
    reason: ReducedReportReason | str,
    checkpointed_at: datetime,
) -> PublishedArtifact:
    """Publish only a frozen deterministic fallback with a closed reason."""
    packet = ResearchPacket.model_validate(packet, strict=True)
    if packet.run.data_quality_status is DataQualityStatus.FAIL:
        raise ValueError("FAIL quality requires the operational report path")
    reason = ReducedReportReason(reason)
    run_id = packet.run.run_id
    stored = repository.load(run_id)
    if stored is None or not stored.checkpoints:
        raise ValueError("reduced publication requires a frozen staged run")
    if stored.published:
        bundle = repository.load_published_bundle(run_id)
        report = repository.get_report(run_id)
        artifact = repository.get_published_artifact(run_id)
        contents = bundle.model_dump(mode="json")["bundle"] if bundle is not None else None
        if (
            contents is None
            or report is None
            or artifact is None
            or contents["brief_origin"] != BriefOrigin.DETERMINISTIC_REDUCED.value
            or contents["reduced_report_reason"] != reason.value
            or contents["research_packet"] != packet.model_dump(mode="json")
            or report != render_reduced_report(packet, reason)
            or stored.checkpoints[-1].stage != "PUBLISHED"
        ):
            raise ValueError("published reduced report differs from packet or reason")
        if bundle is not None:
            _published_telemetry(bundle, stored.checkpoints[-1])
        return artifact
    evidence_cutoff_at = packet.run.require_evidence_cutoff()
    if checkpointed_at < evidence_cutoff_at:
        raise ValueError("reduced publication cannot precede the evidence cutoff")
    _staged_packet_hash(
        repository, packet, frozenset({"AWAITING_SYNTHESIS", "VALIDATING", "PUBLISHED"})
    )
    latest = stored.checkpoints[-1]
    if checkpointed_at < latest.written_at:
        raise ValueError("reduced publication cannot precede current checkpoint")
    reduced_bytes = repository.read_staged_artifact(run_id, "reduced_report")
    reduced_hash = sha256(render_reduced_report_base(packet)).hexdigest()
    if (
        reduced_bytes != render_reduced_report_base(packet)
        or latest.artifact_hashes.get("reduced_report") != reduced_hash
    ):
        raise ValueError("staged reduced report differs from frozen packet")
    attempts = tuple(c for c in stored.checkpoints if c.stage == "VALIDATING")
    reports: list[ValidationReport] = []
    for index, checkpoint in enumerate(attempts, start=1):
        draft, validation = _recorded_attempt(repository, checkpoint)
        if (
            draft.origin is not BriefOrigin.SYNTHESIZED
            or validation.validation_attempt != index
            or validation != validate_research_brief(packet, draft, index)
        ):
            raise ValueError("recorded validation differs from frozen packet")
        reports.append(validation)
    if any(report.is_valid for report in reports):
        raise ValueError("a valid recorded brief cannot use reduced publication")
    if len(reports) == 3 and reason is not ReducedReportReason.VALIDATION_REPAIR_EXHAUSTED:
        raise ValueError("exhausted validation requires the matching reduced reason")
    if reason is ReducedReportReason.VALIDATION_REPAIR_EXHAUSTED and len(reports) != 3:
        raise ValueError("validation repairs are not exhausted")
    reason_bytes = reason.value.encode("ascii")
    if latest.stage == "PUBLISHED":
        if (
            repository.read_staged_artifact(run_id, "reduced_reason") != reason_bytes
            or latest.artifact_hashes.get("reduced_reason")
            != sha256(reason_bytes).hexdigest()
        ):
            raise ValueError("published checkpoint has another reduced reason")
        telemetry = load_checkpoint_telemetry(repository, latest)
        if telemetry is None:
            raise ValueError("published checkpoint is missing staged telemetry")
        telemetry_name = next(
            name for name in latest.artifact_hashes
            if name.startswith("performance_telemetry_")
        )
        telemetry_digest = latest.artifact_hashes[telemetry_name]
        markdown = render_reduced_report(packet, reason)
    else:
        recorder = _publication_recorder(repository, stored)
        with recorder.measure_stage("PUBLICATION"):
            markdown = render_reduced_report(packet, reason)
        reason_hash = repository.stage_artifact(run_id, "reduced_reason", reason_bytes)
        latest = RunCheckpoint(
            run_id=run_id,
            stage="PUBLISHED",
            execution_status=ExecutionStatus.PUBLISHED,
            data_quality_status=packet.run.data_quality_status,
            delivery_status=packet.run.delivery_status,
            written_at=checkpointed_at,
            evidence_cutoff_at=evidence_cutoff_at,
            artifact_hashes=FrozenMap({
                **dict(latest.artifact_hashes),
                "reduced_reason": reason_hash,
            }),
            resumable=False,
        )
        latest, telemetry, telemetry_digest = _bind_publication_telemetry(
            repository, latest, recorder
        )
        repository.checkpoint_if_current(
            run_id, latest, expected_count=len(stored.checkpoints)
        )
    bundle_contents: dict[str, JsonValue] = {
        "research_packet": json.loads(_canonical_packet_bytes(packet)),
        "brief_origin": BriefOrigin.DETERMINISTIC_REDUCED.value,
        "reduced_report_reason": reason.value,
        "reduced_report_staged_sha256": reduced_hash,
        "reduced_plans": tuple(
            FrozenMap[str, JsonValue]({
                "plan_id": plan.plan_id,
                "status": "BLOCKED",
                "reason": reason.value,
            })
            for plan in packet.deterministic_plan_inputs
        ),
        "validation_reports": tuple(
            FrozenMap[str, JsonValue](json.loads(canonical_bytes(report)))
            for report in reports
        ),
    }
    bundle_contents.update(_telemetry_bundle_fields(telemetry, telemetry_digest))
    bundle = PublishedRunBundle(
        run=compose_publication_context(stored.run, latest),
        bundle=FrozenMap[str, JsonValue](bundle_contents),
        report_markdown=markdown,
        markdown_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
    )
    return repository.publish_atomically(bundle)


def publish_operational_report(
    repository: PublicationRepository,
    run: RunContext,
    reason: ErrorCode | str,
    checkpointed_at: datetime,
    *,
    telemetry_recorder: RunTelemetryRecorder | None = None,
) -> PublishedArtifact:
    """Publish a closed operational failure without a research packet.

    Pass the recorder used by a failed stage so its in-memory measurements are
    frozen into this failure checkpoint. Without one, the latest persisted
    snapshot is restored as usual.
    """
    run = RunContext.model_validate(run, strict=True)
    reason = ErrorCode(reason)
    run_id = run.run_id
    stored = repository.load(run_id)
    if stored is None or not stored.checkpoints:
        raise ValueError("operational publication requires a checkpointed run")
    if run.data_quality_status is not DataQualityStatus.FAIL:
        raise ValueError("operational publication requires FAIL quality")
    expected_run = stored.run.model_copy(update={
        "execution_status": run.execution_status,
        "data_quality_status": run.data_quality_status,
        "delivery_status": run.delivery_status,
    })
    if expected_run != run:
        raise ValueError("operational RunContext differs from staged identity")
    if stored.published:
        bundle = repository.load_published_bundle(run_id)
        report = repository.get_report(run_id)
        artifact = repository.get_published_artifact(run_id)
        contents = bundle.model_dump(mode="json")["bundle"] if bundle is not None else None
        if bundle is not None:
            _published_telemetry(bundle, stored.checkpoints[-1])
        if (
            contents is None
            or {
                key: value for key, value in contents.items()
                if key not in {"performance_telemetry", "performance_telemetry_sha256"}
            } != {"brief_origin": BriefOrigin.OPERATIONAL.value,
                 "failure_code": reason.value}
            or report != render_operational_report(run, reason)
            or artifact is None
            or stored.checkpoints[-1].stage != "PUBLISHED"
        ):
            raise ValueError("published operational report differs from run or reason")
        return artifact
    if stored.evidence_cutoff_at is not None and (
        stored.evidence_cutoff_at != run.evidence_cutoff_at
        or checkpointed_at < stored.evidence_cutoff_at
    ):
        raise ValueError("operational report differs from frozen cutoff")
    latest = stored.checkpoints[-1]
    if (
        latest.data_quality_status is not DataQualityStatus.FAIL
        or latest.delivery_status is not run.delivery_status
        or checkpointed_at < latest.written_at
        or any("research_packet" in item.artifact_hashes for item in stored.checkpoints)
        or repository.read_staged_artifact(run_id, "research_packet") is not None
    ):
        raise ValueError("operational publication requires matching FAIL state without packet")
    reason_bytes = reason.value.encode("ascii")
    if latest.stage == "PUBLISHED":
        if (
            latest.artifact_hashes.get("operational_reason")
            != sha256(reason_bytes).hexdigest()
            or repository.read_staged_artifact(run_id, "operational_reason")
            != reason_bytes
        ):
            raise ValueError("published checkpoint has another operational reason")
        telemetry = load_checkpoint_telemetry(repository, latest)
        if telemetry is None:
            raise ValueError("published checkpoint is missing staged telemetry")
        telemetry_name = next(
            name for name in latest.artifact_hashes
            if name.startswith("performance_telemetry_")
        )
        telemetry_digest = latest.artifact_hashes[telemetry_name]
        markdown = render_operational_report(run, reason)
    else:
        if latest.stage not in {
            "CREATED", "CONFIG_FROZEN", "PRIOR_PLANS_OBSERVED",
            "EVIDENCE_COLLECTED", "EVIDENCE_FROZEN", "NORMALIZED",
            "QUALITY_EVALUATED", "ANALYZED", "PACKET_FROZEN",
            "COLLECTING", "NORMALIZING", "ANALYZING",
        } or latest.execution_status is not run.execution_status:
            raise ValueError("operational publication requires pre-synthesis state")
        reason_hash = repository.stage_artifact(run_id, "operational_reason", reason_bytes)
        recorder = telemetry_recorder or _publication_recorder(repository, stored)
        with recorder.measure_stage("PUBLICATION"):
            markdown = render_operational_report(run, reason)
        latest = RunCheckpoint(
            run_id=run_id,
            stage="PUBLISHED",
            execution_status=ExecutionStatus.PUBLISHED,
            data_quality_status=DataQualityStatus.FAIL,
            delivery_status=run.delivery_status,
            written_at=checkpointed_at,
            evidence_cutoff_at=stored.evidence_cutoff_at,
            artifact_hashes=FrozenMap({
                **dict(latest.artifact_hashes), "operational_reason": reason_hash,
            }),
            resumable=False,
        )
        latest, telemetry, telemetry_digest = _bind_publication_telemetry(
            repository, latest, recorder
        )
        repository.checkpoint_if_current(
            run_id, latest, expected_count=len(stored.checkpoints)
        )
    operational_contents: dict[str, JsonValue] = {
        "brief_origin": BriefOrigin.OPERATIONAL.value,
        "failure_code": reason.value,
    }
    operational_contents.update(_telemetry_bundle_fields(telemetry, telemetry_digest))
    bundle = PublishedRunBundle(
        run=compose_publication_context(stored.run, latest),
        bundle=FrozenMap[str, JsonValue](operational_contents),
        report_markdown=markdown,
        markdown_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
    )
    return repository.publish_atomically(bundle)
