"""Tests for trusted Product A application composition and dispatch."""

from datetime import UTC, datetime
from typing import cast

import pytest

from finance_research_agent.application.operations import (
    ProductAOperation,
    SystemDiagnosticCode,
)
from finance_research_agent.application.ports import (
    Clock,
    ConfigurationRepository,
    FeedbackRepository,
    MarketDataProvider,
    ProviderReadiness,
    PublishedArtifactReader,
    RunRepository,
    TradingCalendar,
    WatchlistRepository,
)
from finance_research_agent.application.services import ApplicationServices
from finance_research_agent.domain.policies import WatchlistConfig


class _Dependencies:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def now_utc(self) -> datetime:
        self.calls.append("clock")
        return datetime(2026, 9, 29, 13, tzinfo=UTC)

    def is_trading_day(self, market_date) -> bool:
        self.calls.append("calendar")
        return True

    def session_open_close(self, market_date):
        self.calls.append("calendar")
        return (
            datetime(2026, 9, 29, 13, tzinfo=UTC),
            datetime(2026, 9, 29, 20, tzinfo=UTC),
        )

    def readiness(self):
        self.calls.append("readiness")
        raise AssertionError("readiness is not used by list_watchlist")

    def load(self):
        self.calls.append("load")
        return WatchlistConfig(version="1", items=())

    def replace(self, configuration) -> None:
        self.calls.append("replace")

    def replace_if_version(self, expected_version, configuration) -> bool:
        self.calls.append("replace_if_version")
        return True


def _services(dependencies: _Dependencies) -> ApplicationServices:
    return ApplicationServices(
        clock=cast(Clock, dependencies),
        calendar=cast(TradingCalendar, dependencies),
        configuration_repository=cast(ConfigurationRepository, dependencies),
        market_data=cast(MarketDataProvider, dependencies),
        run_repository=cast(RunRepository, dependencies),
        published_artifact_reader=cast(PublishedArtifactReader, dependencies),
        watchlist_repository=cast(WatchlistRepository, dependencies),
        feedback_repository=cast(FeedbackRepository, dependencies),
    )


def test_composition_owns_service_wrappers_without_touching_ports_at_construction() -> None:
    dependencies = _Dependencies()

    services = _services(dependencies)

    assert services is not None
    assert dependencies.calls == []


def test_typed_dispatch_routes_only_to_the_requested_application_service() -> None:
    dependencies = _Dependencies()
    services = _services(dependencies)

    result = services.dispatch(ProductAOperation.LIST_WATCHLIST, "{}")

    assert isinstance(result, WatchlistConfig)
    assert result.version == "1"
    assert dependencies.calls == ["load"]


def test_unknown_operation_is_rejected_before_touching_any_dependency() -> None:
    dependencies = _Dependencies()
    services = _services(dependencies)

    with pytest.raises(ValueError, match="unknown Product A operation"):
        services.dispatch("invoke_arbitrary_method", "{}")

    assert dependencies.calls == []


def test_invalid_configuration_propagates_without_reading_market_or_run_services() -> None:
    class InvalidConfiguration(_Dependencies):
        def load(self):
            self.calls.append("load")
            raise ValueError("invalid configuration")

    dependencies = InvalidConfiguration()
    services = _services(dependencies)

    with pytest.raises(ValueError, match="invalid configuration"):
        services.dispatch(ProductAOperation.VALIDATE_CONFIGURATION, "{}")

    assert dependencies.calls == ["load"]


def test_system_status_reports_unavailable_capabilities_without_exception_details() -> None:
    class UnavailableDependencies(_Dependencies):
        def load(self):
            self.calls.append("load")
            raise RuntimeError("secret-config-path")

        def readiness(self):
            self.calls.append("readiness")
            return ProviderReadiness(
                provider="test-provider", configured=True, available=False
            )

    dependencies = UnavailableDependencies()
    services = _services(dependencies)
    result = services.dispatch(ProductAOperation.GET_SYSTEM_STATUS, "{}")

    assert result.configuration_ready is False
    assert result.market_data_ready is False
    assert result.market_calendar_ready is False
    assert result.diagnostics == (
        SystemDiagnosticCode.CONFIGURATION_UNAVAILABLE,
        SystemDiagnosticCode.MARKET_DATA_UNAVAILABLE,
        SystemDiagnosticCode.MARKET_CALENDAR_UNAVAILABLE,
    )
    assert "secret-config-path" not in repr(result)


def test_system_status_masks_readiness_exceptions_as_closed_diagnostics() -> None:
    class FailingDependencies(_Dependencies):
        def load(self):
            self.calls.append("load")
            raise RuntimeError("secret-config-path")

        def readiness(self):
            self.calls.append("readiness")
            raise RuntimeError("secret-provider-response")

    dependencies = FailingDependencies()
    services = _services(dependencies)
    result = services.dispatch(ProductAOperation.GET_SYSTEM_STATUS, "{}")

    assert result.diagnostics == (
        SystemDiagnosticCode.CONFIGURATION_UNAVAILABLE,
        SystemDiagnosticCode.MARKET_DATA_UNAVAILABLE,
        SystemDiagnosticCode.MARKET_CALENDAR_UNAVAILABLE,
    )
    assert "secret-" not in repr(result)


def test_system_status_uses_us_market_calendar_date() -> None:
    class OvernightDependencies(_Dependencies):
        def now_utc(self) -> datetime:
            self.calls.append("clock")
            return datetime(2026, 9, 30, 0, 30, tzinfo=UTC)

    dependencies = OvernightDependencies()
    services = _services(dependencies)

    result = services.dispatch(ProductAOperation.GET_SYSTEM_STATUS, "{}")

    assert result.current_market_date.isoformat() == "2026-09-29"
