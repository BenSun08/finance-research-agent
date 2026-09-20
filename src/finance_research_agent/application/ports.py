"""Provider-neutral outbound capabilities required by application use cases."""

from typing import Protocol

from finance_research_agent.domain.policies import AppConfiguration, WatchlistConfig
from finance_research_agent.market_data.historical import (
    HistoricalBarsFetchResult,
    HistoricalDailyBarsRequest,
)

__all__ = ["ConfigurationRepository", "HistoricalBarsFetcher", "WatchlistRepository"]


class HistoricalBarsFetcher(Protocol):
    """Fetch completed historical daily bars for one provider-neutral request."""

    def fetch_daily_bars(
        self,
        request: HistoricalDailyBarsRequest,
    ) -> HistoricalBarsFetchResult: ...


class ConfigurationRepository(Protocol):
    """Provider-neutral source of the five validated local policies."""

    def load(self) -> AppConfiguration: ...


class WatchlistRepository(Protocol):
    """Provider-neutral read/replace boundary for the watchlist file."""

    def load(self) -> WatchlistConfig: ...

    def replace(self, configuration: WatchlistConfig) -> None: ...
