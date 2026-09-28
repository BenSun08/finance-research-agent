from pathlib import Path
from typing import cast

import pytest

import finance_research_agent.application as application
from finance_research_agent.adapters.yaml_config import load_configuration
from finance_research_agent.application.ports import (
    MarketCalendarReadinessProvider,
    MarketDataProvider,
)
from finance_research_agent.domain.enums import SourceRole
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import ProviderReadiness, SourceHealth


class _MarketDataReadiness:
    def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(
            provider="alpaca",
            configured=False,
            available=False,
            error_code=ErrorCode.CREDENTIALS_MISSING,
        )


class _CalendarReadiness:
    def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(
            provider="market-calendar",
            configured=True,
            available=True,
        )


def _source_policy():
    examples = Path(__file__).parents[2] / "config" / "examples"
    return load_configuration(examples).source


def test_configured_source_health_uses_frozen_roles_and_port_readiness() -> None:
    collect = getattr(application, "read_configured_source_health", None)
    assert callable(collect), "application must expose configured source health collection"

    health = collect(
        _source_policy(),
        cast(MarketDataProvider, _MarketDataReadiness()),
        cast(MarketCalendarReadinessProvider, _CalendarReadiness()),
    )

    assert health == (
        SourceHealth(
            provider="alpaca",
            available=False,
            required=True,
            error_code=ErrorCode.CREDENTIALS_MISSING,
        ),
        SourceHealth(provider="market-calendar", available=True, required=True),
    )


def test_unavailable_source_without_provider_error_is_rejected() -> None:
    class UnreportedMarketDataReadiness:
        def readiness(self) -> ProviderReadiness:
            return ProviderReadiness(
                provider="alpaca",
                configured=False,
                available=False,
            )

    collect = getattr(application, "read_configured_source_health", None)
    assert callable(collect), "application must expose configured source health collection"

    with pytest.raises(ValueError, match="unavailable source readiness requires an error code"):
        collect(
            _source_policy(),
            cast(MarketDataProvider, UnreportedMarketDataReadiness()),
            cast(MarketCalendarReadinessProvider, _CalendarReadiness()),
        )


def test_health_collection_rejects_unsupported_frozen_source_roles() -> None:
    collect = getattr(application, "read_configured_source_health", None)
    assert callable(collect), "application must expose configured source health collection"
    policy = _source_policy().model_copy(
        update={
            "quality_source_roles": (
                SourceRole.MARKET_DATA,
                SourceRole.MARKET_CALENDAR,
                SourceRole.MACRO_CALENDAR,
            )
        }
    )

    with pytest.raises(ValueError, match="unsupported for configured roles"):
        collect(
            policy,
            cast(MarketDataProvider, object()),
            cast(MarketCalendarReadinessProvider, object()),
        )


def test_health_collection_rejects_readiness_from_another_provider() -> None:
    class WrongMarketDataReadiness:
        def readiness(self) -> ProviderReadiness:
            return ProviderReadiness(
                provider="market-calendar",
                configured=True,
                available=True,
            )

    collect = getattr(application, "read_configured_source_health", None)
    assert callable(collect), "application must expose configured source health collection"

    with pytest.raises(ValueError, match="does not match its configured role"):
        collect(
            _source_policy(),
            cast(MarketDataProvider, WrongMarketDataReadiness()),
            cast(MarketCalendarReadinessProvider, _CalendarReadiness()),
        )


def test_health_collection_rejects_non_readiness_provider_result() -> None:
    class MalformedMarketDataReadiness:
        def readiness(self) -> object:
            return object()

    collect = getattr(application, "read_configured_source_health", None)
    assert callable(collect), "application must expose configured source health collection"

    with pytest.raises(TypeError, match="must be ProviderReadiness"):
        collect(
            _source_policy(),
            cast(MarketDataProvider, MalformedMarketDataReadiness()),
            cast(MarketCalendarReadinessProvider, _CalendarReadiness()),
        )


def test_health_collection_rejects_a_non_policy_input() -> None:
    collect = getattr(application, "read_configured_source_health", None)
    assert callable(collect), "application must expose configured source health collection"

    with pytest.raises(TypeError, match="source_policy must be SourcePolicy"):
        collect(
            object(),
            cast(MarketDataProvider, _MarketDataReadiness()),
            cast(MarketCalendarReadinessProvider, _CalendarReadiness()),
        )
