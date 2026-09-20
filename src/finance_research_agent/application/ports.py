"""Provider-neutral outbound capabilities required by application use cases."""

from pathlib import Path
from typing import Protocol

from finance_research_agent.domain.market_calendar import TradingCalendar
from finance_research_agent.domain.policies import AppConfiguration, WatchlistConfig
from finance_research_agent.market_data.historical import (
    HistoricalBarsFetchResult,
    HistoricalDailyBarsRequest,
)

__all__ = [
    "ConfigurationRepository",
    "ConfigurationRepositoryFactory",
    "HistoricalBarsFetcher",
    "WatchlistRepository",
    "TradingCalendar",
]


class HistoricalBarsFetcher(Protocol):
    """Fetch completed historical daily bars for one provider-neutral request."""

    def fetch_daily_bars(
        self,
        request: HistoricalDailyBarsRequest,
    ) -> HistoricalBarsFetchResult: ...


class ConfigurationRepository(Protocol):
    """Provider-neutral source of the five validated local policies."""

    def load(self) -> AppConfiguration: ...


class ConfigurationRepositoryFactory(Protocol):
    """Injected composition boundary for a trusted configuration directory."""

    def __call__(self, source: Path, staging_root: Path) -> ConfigurationRepository: ...


class WatchlistRepository(Protocol):
    """Provider-neutral read/replace boundary for the watchlist file."""

    def load(self) -> WatchlistConfig: ...

    def replace(self, configuration: WatchlistConfig) -> None: ...

    def replace_if_version(self, expected_version: str, configuration: WatchlistConfig) -> bool: ...
