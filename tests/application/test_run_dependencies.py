from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from finance_research_agent.application.ports import (
    Clock,
    ConfigurationRepository,
    EventProvider,
    MarketDataProvider,
    RunRepository,
)
from finance_research_agent.application.run_service import RunDependencies
from finance_research_agent.domain.market_calendar import TradingCalendar


def test_run_dependencies_preserve_injected_ports_and_event_provider_order() -> None:
    clock = object()
    calendar = object()
    config_repository = object()
    run_repository = object()
    market_data = object()
    first_event_provider = object()
    second_event_provider = object()

    dependencies = RunDependencies(
        clock=cast(Clock, clock),
        calendar=cast(TradingCalendar, calendar),
        config_repository=cast(ConfigurationRepository, config_repository),
        run_repository=cast(RunRepository, run_repository),
        market_data=cast(MarketDataProvider, market_data),
        event_providers=(
            cast(EventProvider, first_event_provider),
            cast(EventProvider, second_event_provider),
        ),
    )

    assert dependencies.clock is clock
    assert dependencies.calendar is calendar
    assert dependencies.config_repository is config_repository
    assert dependencies.run_repository is run_repository
    assert dependencies.market_data is market_data
    assert dependencies.event_providers == (first_event_provider, second_event_provider)

    with pytest.raises(FrozenInstanceError):
        dependencies.market_data = cast(MarketDataProvider, object())


def test_run_dependencies_reject_mutable_event_provider_collection() -> None:
    with pytest.raises(TypeError, match="event_providers must be a tuple"):
        RunDependencies(
            clock=cast(Clock, object()),
            calendar=cast(TradingCalendar, object()),
            config_repository=cast(ConfigurationRepository, object()),
            run_repository=cast(RunRepository, object()),
            market_data=cast(MarketDataProvider, object()),
            event_providers=cast(tuple[EventProvider, ...], [object()]),
        )
