"""Provider-neutral outbound capabilities required by application use cases."""

from typing import Protocol

from finance_research_agent.market_data.historical import (
    HistoricalBarsFetchResult,
    HistoricalDailyBarsRequest,
)

__all__ = ["HistoricalBarsFetcher"]


class HistoricalBarsFetcher(Protocol):
    """Fetch completed historical daily bars for one provider-neutral request."""

    def fetch_daily_bars(
        self,
        request: HistoricalDailyBarsRequest,
    ) -> HistoricalBarsFetchResult: ...
