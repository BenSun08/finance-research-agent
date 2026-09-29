"""Read-only replay of an immutable published Product A artifact."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from finance_research_agent.application.operational_report import render_operational_report
from finance_research_agent.application.ports import PublishedArtifactReader
from finance_research_agent.application.reduced_report import (
    render_reduced_report,
    render_reduced_report_base,
)
from finance_research_agent.application.report_renderer import render_markdown_report
from finance_research_agent.domain.enums import (
    BriefOrigin,
    DataQualityStatus,
    ExecutionStatus,
    ReducedReportReason,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    ComponentVersions,
    PerformanceTelemetry,
    PublishedRunBundle,
)
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.domain.validation import ResearchBriefDraft, ValidationReport


class ArtifactNotFoundError(LookupError):
    """Raised when a run has no complete indexed publication."""


@dataclass(frozen=True, slots=True)
class ArtifactReplayResult:
    """Indexed hashes and replay result.

    On version drift, ``markdown_matches=False`` means reconstruction was skipped;
    ``replayed_markdown_sha256`` is absent rather than a measured mismatch.
    """

    run_id: str
    bundle: PublishedRunBundle
    report_markdown: str
    json_matches: bool
    markdown_matches: bool
    stored_json_sha256: str
    replayed_json_sha256: str
    stored_markdown_sha256: str
    replayed_markdown_sha256: str | None
    component_version_mismatches: tuple[str, ...]
    telemetry: PerformanceTelemetry | None = None


def _recorded_versions(bundle: PublishedRunBundle) -> ComponentVersions:
    run = bundle.run
    configuration = run.configuration_snapshot
    return ComponentVersions(
        core_version=run.core_version,
        mcp_contract_version=run.mcp_contract_version,
        plugin_version=run.plugin_version,
        skill_version=run.skill_version,
        prompt_version=run.prompt_version,
        report_template_version=run.report_template_version,
        schema_versions=run.schema_versions,
        watchlist_version=configuration.watchlist_version,
        regime_policy_version=configuration.regime_policy_version,
        setup_policy_version=configuration.setup_policy_version,
        risk_policy_version=configuration.risk_policy_version,
        source_policy_version=configuration.source_policy_version,
    )


def _version_mismatches(
    recorded: ComponentVersions,
    current: ComponentVersions,
) -> tuple[str, ...]:
    return tuple(
        name
        for name in (
            "core_version",
            "mcp_contract_version",
            "plugin_version",
            "skill_version",
            "prompt_version",
            "report_template_version",
            "schema_versions",
            "watchlist_version",
            "regime_policy_version",
            "setup_policy_version",
            "risk_policy_version",
            "source_policy_version",
        )
        if getattr(recorded, name) != getattr(current, name)
    )


def _frozen_model_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _reconstructed_report(bundle: PublishedRunBundle) -> str:
    contents = dict(bundle.model_dump(mode="json")["bundle"])
    contents.pop("performance_telemetry", None)
    contents.pop("performance_telemetry_sha256", None)
    if bundle.run.execution_status is not ExecutionStatus.PUBLISHED:
        raise ValueError("frozen bundle is not a published run")
    if "brief_draft" in contents:
        if set(contents) != {"research_packet", "brief_draft", "validation_report"}:
            raise ValueError("invalid synthesized report origin")
        packet = ResearchPacket.model_validate_json(
            _frozen_model_bytes(contents["research_packet"])
        )
        draft = ResearchBriefDraft.model_validate_json(
            _frozen_model_bytes(contents["brief_draft"])
        )
        validation = ValidationReport.model_validate_json(
            _frozen_model_bytes(contents["validation_report"])
        )
        if draft.origin is not BriefOrigin.SYNTHESIZED:
            raise ValueError("invalid synthesized report origin")
        report = render_markdown_report(packet, draft, validation)
    elif contents.get("brief_origin") == BriefOrigin.DETERMINISTIC_REDUCED.value:
        if set(contents) != {
            "research_packet", "brief_origin", "reduced_report_reason",
            "reduced_report_staged_sha256", "reduced_plans", "validation_reports",
        }:
            raise ValueError("invalid reduced report origin")
        packet = ResearchPacket.model_validate_json(
            _frozen_model_bytes(contents["research_packet"])
        )
        reason = ReducedReportReason(contents["reduced_report_reason"])
        if contents["reduced_report_staged_sha256"] != sha256(
            render_reduced_report_base(packet)
        ).hexdigest():
            raise ValueError("reduced report base hash differs from frozen packet")
        expected_plans = [
            {"plan_id": plan.plan_id, "status": "BLOCKED", "reason": reason.value}
            for plan in packet.deterministic_plan_inputs
        ]
        if contents["reduced_plans"] != expected_plans:
            raise ValueError("reduced plan status differs from frozen packet")
        reports = contents["validation_reports"]
        if not isinstance(reports, list) or len(reports) > 3:
            raise ValueError("reduced validation reports are not bounded")
        for attempt, value in enumerate(reports, start=1):
            validation = ValidationReport.model_validate_json(_frozen_model_bytes(value))
            if (
                validation.run_id != packet.run.run_id
                or validation.packet_id != packet.packet_id
                or validation.packet_sha256 != packet.canonical_sha256
                or validation.validation_attempt != attempt
                or validation.is_valid
            ):
                raise ValueError("reduced validation reports differ from frozen packet")
        if (len(reports) == 3) != (
            reason is ReducedReportReason.VALIDATION_REPAIR_EXHAUSTED
        ):
            raise ValueError("reduced validation reports do not match fallback reason")
        report = render_reduced_report(packet, reason)
    elif contents.get("brief_origin") == BriefOrigin.OPERATIONAL.value:
        if set(contents) != {"brief_origin", "failure_code"}:
            raise ValueError("invalid operational report origin")
        if bundle.run.data_quality_status is not DataQualityStatus.FAIL:
            raise ValueError("operational report requires FAIL quality")
        return render_operational_report(bundle.run, ErrorCode(contents["failure_code"]))
    else:
        raise ValueError("unknown frozen report origin")
    if packet.run != bundle.run.model_copy(
        update={"execution_status": packet.run.execution_status}
    ):
        raise ValueError("frozen packet run differs from published run")
    return report


def _frozen_telemetry(bundle: PublishedRunBundle) -> PerformanceTelemetry | None:
    contents = bundle.model_dump(mode="json")["bundle"]
    telemetry_data = contents.get("performance_telemetry")
    digest = contents.get("performance_telemetry_sha256")
    if telemetry_data is None and digest is None:
        return None
    if telemetry_data is None or not isinstance(digest, str):
        raise ValueError("published telemetry value and hash must appear together")
    try:
        telemetry = PerformanceTelemetry.model_validate(telemetry_data, strict=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("published telemetry is malformed") from exc
    if sha256(canonical_bytes(telemetry)).hexdigest() != digest:
        raise ValueError("published telemetry hash does not match its canonical value")
    if _frozen_model_bytes(telemetry_data) != canonical_bytes(telemetry):
        raise ValueError("published telemetry is not canonical")
    return telemetry


def replay_published_artifact(
    repository: PublishedArtifactReader,
    run_id: str,
    current_versions: ComponentVersions,
) -> ArtifactReplayResult:
    """Load one indexed frozen artifact without collection, configuration, or synthesis."""
    bundle = repository.load_published_bundle(run_id)
    report = repository.get_report(run_id)
    artifact = repository.get_published_artifact(run_id)
    if bundle is None or report is None or artifact is None:
        raise ArtifactNotFoundError("no complete published artifact exists for this run")
    if bundle.run.run_id != run_id or artifact.run_id != run_id:
        raise ValueError("published bundle run ID does not match the requested artifact")
    json_digest = sha256(canonical_bytes(bundle)).hexdigest()
    if json_digest != artifact.bundle_sha256:
        raise ValueError("bundle JSON bytes do not match the indexed publication")
    markdown_digest = sha256(report.encode("utf-8")).hexdigest()
    if (
        bundle.markdown_sha256 is None
        or markdown_digest != bundle.markdown_sha256
        or markdown_digest != artifact.markdown_sha256
    ):
        raise ValueError("report bytes do not match the frozen bundle")
    telemetry = _frozen_telemetry(bundle)
    mismatches = _version_mismatches(_recorded_versions(bundle), current_versions)
    if mismatches:
        return ArtifactReplayResult(
            run_id=run_id,
            bundle=bundle,
            report_markdown=report,
            json_matches=True,
            markdown_matches=False,
            stored_json_sha256=artifact.bundle_sha256,
            replayed_json_sha256=json_digest,
            stored_markdown_sha256=artifact.markdown_sha256,
            replayed_markdown_sha256=None,
            component_version_mismatches=mismatches,
            telemetry=telemetry,
        )
    reconstructed = _reconstructed_report(bundle)
    replayed_markdown_digest = sha256(reconstructed.encode("utf-8")).hexdigest()
    if bundle.report_markdown != report or reconstructed != report:
        raise ValueError("reconstructed report differs from frozen report bytes")
    return ArtifactReplayResult(
        run_id=run_id,
        bundle=bundle,
        report_markdown=report,
        json_matches=True,
        markdown_matches=True,
        stored_json_sha256=artifact.bundle_sha256,
        replayed_json_sha256=json_digest,
        stored_markdown_sha256=artifact.markdown_sha256,
        replayed_markdown_sha256=replayed_markdown_digest,
        component_version_mismatches=(),
        telemetry=telemetry,
    )
