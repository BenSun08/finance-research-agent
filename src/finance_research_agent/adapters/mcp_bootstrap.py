"""Lazy executable composition for the stdio-only Product A server."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from time import sleep
from typing import Any, Protocol

from mcp.server import Server

from finance_research_agent.adapters.alpaca import AlpacaMarketDataProvider
from finance_research_agent.adapters.exchange_calendar import ExchangeCalendarAdapter
from finance_research_agent.adapters.feedback import FileSystemFeedbackRepository
from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.adapters.http_client import RequestDeadlineExceeded, SafeHttpClient
from finance_research_agent.adapters.mcp_stdio import create_mcp_server, run_stdio
from finance_research_agent.adapters.yaml_config import (
    YamlConfigurationRepository,
    YamlWatchlistRepository,
)
from finance_research_agent.application.component_versions import current_component_versions
from finance_research_agent.application.config_service import (
    ConfigService,
    configuration_from_snapshot,
)
from finance_research_agent.application.operations import OperationError, validate_operation_request
from finance_research_agent.application.ports import (
    Clock,
    MarketCalendarReadinessProvider,
    MarketDataProvider,
    ProviderRequestObserver,
)
from finance_research_agent.application.services import ApplicationServices
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    ComponentVersions,
    ConfigurationSnapshot,
    InstrumentIdentity,
    PriceObservation,
    ProviderFailure,
    ProviderReadiness,
    RunContext,
)
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.settings import Settings


class Dispatcher(Protocol):
    """Only the application dispatch interface is visible to transport."""

    def dispatch(self, operation: str, arguments_json: str) -> object: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)


class LazyApplicationDispatcher:
    """Validate before composing; cache successful construction only."""

    def __init__(self, factory: Callable[[], Dispatcher]) -> None:
        self._factory = factory
        self._services: Dispatcher | None = None

    def dispatch(self, operation: str, arguments_json: str) -> object:
        validate_operation_request(operation, arguments_json)
        if self._services is None:
            self._services = self._factory()
        return self._services.dispatch(operation, arguments_json)


class LazyMarketDataProvider:
    """Forward the complete market-data port to a lazily constructed provider."""

    def __init__(self, factory: Callable[[], MarketDataProvider]) -> None:
        self._factory = factory
        self._provider: MarketDataProvider | None = None

    def _get(self) -> MarketDataProvider:
        if self._provider is None:
            self._provider = self._factory()
        return self._provider

    def readiness(self) -> ProviderReadiness:
        return self._get().readiness()

    def fetch_instruments(
        self, symbols: Sequence[str], *, telemetry_observer: ProviderRequestObserver | None = None,
    ) -> Mapping[str, InstrumentIdentity | ProviderFailure]:
        return self._get().fetch_instruments(symbols, telemetry_observer=telemetry_observer)

    def fetch_daily_bars(
        self, symbols: Sequence[str], start: date, end: date, *,
        expected_sessions: tuple[date, ...] | None = None,
        completed_through_session: date | None = None,
        evidence_cutoff_at: datetime | None = None,
        instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
        telemetry_observer: ProviderRequestObserver | None = None,
    ) -> Mapping[str, tuple[CompletedDailyBar, ...] | ProviderFailure]:
        return self._get().fetch_daily_bars(
            symbols, start, end, expected_sessions=expected_sessions,
            completed_through_session=completed_through_session,
            evidence_cutoff_at=evidence_cutoff_at, instrument_identities=instrument_identities,
            telemetry_observer=telemetry_observer,
        )

    def fetch_premarket_observations(
        self, symbols: Sequence[str], as_of: datetime | None, *,
        instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
        telemetry_observer: ProviderRequestObserver | None = None,
    ) -> Mapping[str, PriceObservation | ProviderFailure]:
        return self._get().fetch_premarket_observations(
            symbols, as_of, instrument_identities=instrument_identities,
            telemetry_observer=telemetry_observer,
        )


class LazyMarketCalendar:
    """Keep stored-artifact operations independent of calendar initialization."""

    def __init__(self, factory: Callable[[], MarketCalendarReadinessProvider]) -> None:
        self._factory = factory
        self._calendar: MarketCalendarReadinessProvider | None = None

    def _get(self) -> MarketCalendarReadinessProvider:
        if self._calendar is None:
            self._calendar = self._factory()
        return self._calendar

    def readiness(self) -> ProviderReadiness:
        return self._get().readiness()

    def is_trading_day(self, market_date: date) -> bool:
        return self._get().is_trading_day(market_date)

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        return self._get().session_open_close(market_date)


def create_http_client(
    policy: SourcePolicy, deadline: datetime | None, clock: Clock,
) -> SafeHttpClient:
    """Requests own and close their HTTPX clients, streams, and transports."""
    return SafeHttpClient(
        policy, clock=clock.now_utc, run_deadline=deadline,
        sleeper=lambda delay: sleep(float(delay)),
    )


def create_default_services(
    *,
    data_root: Path | None = None,
    clock: Clock | None = None,
    calendar_factory: Callable[[], MarketCalendarReadinessProvider] = ExchangeCalendarAdapter,
    settings_factory: Callable[[], Settings] = Settings,
    http_client_factory: Callable[[SourcePolicy, datetime | None, Clock], SafeHttpClient] = (
        create_http_client
    ),
    component_versions: Callable[[ConfigurationSnapshot], ComponentVersions] = (
        current_component_versions
    ),
) -> ApplicationServices:
    """Compose real local adapters without reading policies or credentials yet.

    The process locator is environment-only, matching frozen CLI replay. Dotenv
    remains an optional credential source when a provider is actually needed.
    """
    root = data_root if data_root is not None else Path(
        os.environ.get("AI_MARKET_RESEARCH_DATA_DIR") or "data"
    )
    trusted_clock = clock if clock is not None else SystemClock()
    configuration = YamlConfigurationRepository(root / "config")
    repository = FileSystemRunRepository(root, create_layout=False)
    settings: Settings | None = None

    def get_settings() -> Settings:
        nonlocal settings
        if settings is None:
            settings = settings_factory()
        return settings

    def provider(policy: SourcePolicy, deadline: datetime | None) -> AlpacaMarketDataProvider:
        return AlpacaMarketDataProvider(
            get_settings(), http_client_factory(policy, deadline, trusted_clock),
            clock=trusted_clock.now_utc,
        )

    def current_provider() -> MarketDataProvider:
        snapshot = ConfigService(configuration).validate_and_snapshot()
        return provider(configuration_from_snapshot(snapshot).source, None)

    def provider_for_run(run: RunContext, deadline: datetime) -> MarketDataProvider:
        try:
            policy = configuration_from_snapshot(run.configuration_snapshot).source
            return provider(policy, deadline)
        except RequestDeadlineExceeded:
            raise TimeoutError("provider run deadline exceeded") from None

    return ApplicationServices(
        clock=trusted_clock,
        calendar=LazyMarketCalendar(calendar_factory),
        configuration_repository=configuration,
        market_data=LazyMarketDataProvider(current_provider),
        run_repository=repository,
        published_artifact_reader=repository,
        watchlist_repository=YamlWatchlistRepository(root / "config"),
        feedback_repository=FileSystemFeedbackRepository(root / "feedback"),
        component_versions=component_versions,
        market_data_for_run=provider_for_run,
    )


def create_stdio_server(*, services_factory: Callable[[], Dispatcher] | None = None) -> Server[Any]:
    """Create protocol metadata without constructing any environment dependencies."""
    return create_mcp_server(LazyApplicationDispatcher(
        create_default_services if services_factory is None else services_factory
    ))


def main(*, services_factory: Callable[[], Dispatcher] | None = None) -> int:
    """Serve stdio; keep startup failures redacted and stdout protocol-only."""
    try:
        run_stdio(create_stdio_server(services_factory=services_factory))
    except Exception:
        print(OperationError(code=ErrorCode.INTERNAL_ERROR).model_dump_json(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
