"""Current deterministic component-version metadata for Product A runs."""

from finance_research_agent import __version__
from finance_research_agent.application.prompt_source import canonical_prompt_sha256
from finance_research_agent.application.skill_bundle import load_installed_skill_versions
from finance_research_agent.domain.models import ComponentVersions, ConfigurationSnapshot
from finance_research_agent.domain.types import FrozenMap


def component_versions_for_skill(
    snapshot: ConfigurationSnapshot,
    skill_version: str,
    *,
    plugin_version: str = "0.1.0",
) -> ComponentVersions:
    """Assemble versions from explicitly trusted contracts and local policies."""
    return ComponentVersions(
        core_version=__version__,
        mcp_contract_version="0.1",
        plugin_version=plugin_version,
        skill_version=skill_version,
        prompt_version=canonical_prompt_sha256(),
        report_template_version="0.1",
        schema_versions=FrozenMap(
            {
                "citation-entailment-review": "0.2",
                "record-run-feedback-request": "0.2",
                "recorded-feedback": "0.2",
                "run-context": "0.1",
            }
        ),
        watchlist_version=snapshot.watchlist_version,
        regime_policy_version=snapshot.regime_policy_version,
        setup_policy_version=snapshot.setup_policy_version,
        risk_policy_version=snapshot.risk_policy_version,
        source_policy_version=snapshot.source_policy_version,
    )


def current_component_versions(snapshot: ConfigurationSnapshot) -> ComponentVersions:
    """Build current versions from the fixed installed Product A plugin bytes."""
    skill_version, plugin_version = load_installed_skill_versions()
    return component_versions_for_skill(snapshot, skill_version, plugin_version=plugin_version)


__all__ = ["component_versions_for_skill", "current_component_versions"]
