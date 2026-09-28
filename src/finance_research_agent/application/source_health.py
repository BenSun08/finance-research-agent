"""Collect source availability from the providers named by frozen policy."""

from finance_research_agent.application.ports import (
    MarketCalendarReadinessProvider,
    MarketDataProvider,
)
from finance_research_agent.domain.enums import SourceRole
from finance_research_agent.domain.models import ProviderReadiness, SourceHealth
from finance_research_agent.domain.policies import SourcePolicy

_ROLE_PROVIDERS = {
    SourceRole.MARKET_DATA: "alpaca",
    SourceRole.MARKET_CALENDAR: "market-calendar",
}


def read_configured_source_health(
    source_policy: SourcePolicy,
    market_data: MarketDataProvider,
    calendar: MarketCalendarReadinessProvider,
) -> tuple[SourceHealth, ...]:
    """Read health for supported roles in the frozen source-policy order."""
    if not isinstance(source_policy, SourcePolicy):
        raise TypeError("source_policy must be SourcePolicy")
    unsupported = tuple(
        role for role in source_policy.quality_source_roles if role not in _ROLE_PROVIDERS
    )
    if unsupported:
        raise ValueError("source health is unsupported for configured roles")

    readiness_ports: dict[SourceRole, MarketDataProvider | MarketCalendarReadinessProvider] = {
        SourceRole.MARKET_DATA: market_data,
        SourceRole.MARKET_CALENDAR: calendar,
    }
    health: list[SourceHealth] = []
    for role in source_policy.quality_source_roles:
        readiness = readiness_ports[role].readiness()
        if not isinstance(readiness, ProviderReadiness):
            raise TypeError("source readiness must be ProviderReadiness")
        expected_provider = _ROLE_PROVIDERS[role]
        if readiness.provider != expected_provider:
            raise ValueError("source readiness provider does not match its configured role")
        if not readiness.available and readiness.error_code is None:
            raise ValueError("unavailable source readiness requires an error code")
        health.append(
            SourceHealth(
                provider=readiness.provider,
                available=readiness.available,
                required=True,
                error_code=readiness.error_code,
            )
        )
    return tuple(health)
