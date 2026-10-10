"""Reusable, loader-verifiable shadow publication fixtures."""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from pathlib import Path

from finance_research_agent.application.config_service import (
    ConfigService,
    DirectoryConfigurationRepository,
)
from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.performance_telemetry import RunTelemetryRecorder
from finance_research_agent.application.reduced_report import (
    render_reduced_report,
    render_reduced_report_base,
)
from finance_research_agent.domain.enums import BriefOrigin, ExecutionStatus, ReducedReportReason
from finance_research_agent.domain.models import (
    ComponentVersions,
    PublishedArtifact,
    PublishedRunBundle,
    RunContext,
)
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.types import FrozenMap, canonical_bytes


def recorded_component_versions(run: RunContext) -> ComponentVersions:
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


def personal_reduced_publication(
    valid_packet: ResearchPacket, staging_root: Path
) -> tuple[PublishedRunBundle, PublishedArtifact]:
    """Build a reduced research publication with frozen personal source policies."""
    configuration_snapshot = ConfigService(
        DirectoryConfigurationRepository(
            Path(__file__).parents[2] / "config" / "examples",
            staging_root / "config-staging",
        )
    ).validate_and_snapshot()
    run = valid_packet.run.model_copy(update={"configuration_snapshot": configuration_snapshot})
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
    recorder = RunTelemetryRecorder(monotonic_ns=lambda: 0)
    recorder.record_http_exchange("alpaca", 1, 256)
    telemetry = recorder.snapshot(
        research_packet_bytes=packet.synthesis_constraints.serialized_bytes
    )
    reason = ReducedReportReason.SYNTHESIS_UNAVAILABLE
    report = render_reduced_report(packet, reason)
    bundle = PublishedRunBundle(
        run=packet.run.model_copy(update={"execution_status": ExecutionStatus.PUBLISHED}),
        bundle=FrozenMap(
            {
                "research_packet": packet.model_dump(mode="json"),
                "brief_origin": BriefOrigin.DETERMINISTIC_REDUCED.value,
                "reduced_report_reason": reason.value,
                "reduced_report_staged_sha256": sha256(
                    render_reduced_report_base(packet)
                ).hexdigest(),
                "reduced_plans": (),
                "validation_reports": (),
                "performance_telemetry": telemetry.model_dump(mode="json"),
                "performance_telemetry_sha256": sha256(canonical_bytes(telemetry)).hexdigest(),
            }
        ),
        report_markdown=report,
        markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
    )
    artifact = PublishedArtifact(
        run_id=bundle.run.run_id,
        bundle_sha256=sha256(canonical_bytes(bundle)).hexdigest(),
        markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
        published_at=bundle.run.invoked_at + timedelta(minutes=2),
    )
    return bundle, artifact
