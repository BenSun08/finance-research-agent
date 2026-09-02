"""Provider-independent historical market-data boundaries."""

from finance_research_agent.market_data.historical import (
    BarAdjustment,
    DailyBarObservation,
    HistoricalBarsFailure,
    HistoricalBarsFetchResult,
    HistoricalBarsOutcome,
    HistoricalBarsProvenance,
    HistoricalBarsRequestFailure,
    HistoricalBarsRequestFailureReason,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
    HistoricalDailyBarsRequest,
    MarketDataCoverage,
    MarketDataFeed,
    coverage_for_feed,
    create_daily_bar_observation,
    to_market_snapshot,
)

__all__ = [
    "BarAdjustment",
    "DailyBarObservation",
    "HistoricalBarsFailure",
    "HistoricalBarsFetchResult",
    "HistoricalBarsOutcome",
    "HistoricalBarsProvenance",
    "HistoricalBarsRequestFailure",
    "HistoricalBarsRequestFailureReason",
    "HistoricalBarsUnavailableReason",
    "HistoricalDailyBars",
    "HistoricalDailyBarsRequest",
    "MarketDataCoverage",
    "MarketDataFeed",
    "coverage_for_feed",
    "create_daily_bar_observation",
    "to_market_snapshot",
]
