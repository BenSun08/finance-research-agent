"""Current deterministic component-version metadata for Product A runs."""

from finance_research_agent import __version__
from finance_research_agent.domain.models import ComponentVersions, ConfigurationSnapshot
from finance_research_agent.domain.types import FrozenMap


def current_component_versions(snapshot: ConfigurationSnapshot) -> ComponentVersions:
    """Build current versions from package contracts and validated local policies."""
    return ComponentVersions(
        core_version=__version__,
        mcp_contract_version="0.1",
        plugin_version="0.1",
        skill_version="0.1",
        prompt_version="0.1",
        report_template_version="0.1",
        schema_versions=FrozenMap({"run-context": "0.1"}),
        watchlist_version=snapshot.watchlist_version,
        regime_policy_version=snapshot.regime_policy_version,
        setup_policy_version=snapshot.setup_policy_version,
        risk_policy_version=snapshot.risk_policy_version,
        source_policy_version=snapshot.source_policy_version,
    )


__all__ = ["current_component_versions"]
