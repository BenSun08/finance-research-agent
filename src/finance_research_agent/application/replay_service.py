"""Read-only replay of an immutable published Product A artifact."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from finance_research_agent.application.ports import PublishedArtifactReader
from finance_research_agent.domain.models import (
    ComponentVersions,
    PublishedRunBundle,
)


class ArtifactNotFoundError(LookupError):
    """Raised when a run has no complete indexed publication."""


class ArtifactVersionMismatchError(ValueError):
    """Raised when current component versions cannot replay a frozen artifact."""


@dataclass(frozen=True, slots=True)
class ArtifactReplayResult:
    """Exact frozen JSON bundle and Markdown returned after verification."""

    run_id: str
    bundle: PublishedRunBundle
    report_markdown: str


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


def replay_published_artifact(
    repository: PublishedArtifactReader,
    run_id: str,
    current_versions: ComponentVersions,
) -> ArtifactReplayResult:
    """Load one indexed frozen artifact without collection, configuration, or synthesis."""
    bundle = repository.load_published_bundle(run_id)
    report = repository.get_report(run_id)
    if bundle is None or report is None:
        raise ArtifactNotFoundError("no complete published artifact exists for this run")
    if bundle.run.run_id != run_id:
        raise ValueError("published bundle run ID does not match the requested artifact")
    if bundle.markdown_sha256 is None or sha256(report.encode("utf-8")).hexdigest() != (
        bundle.markdown_sha256
    ):
        raise ValueError("report bytes do not match the frozen bundle")

    mismatches = _version_mismatches(_recorded_versions(bundle), current_versions)
    if mismatches:
        raise ArtifactVersionMismatchError(
            "component versions differ from the frozen run: " + ", ".join(mismatches)
        )
    return ArtifactReplayResult(run_id=run_id, bundle=bundle, report_markdown=report)
