"""Outer application use case for historical-data-backed regime research."""

from datetime import datetime

from finance_research_agent.application.ports import HistoricalBarsFetcher
from finance_research_agent.application.regime_workflow import run_regime_workflow
from finance_research_agent.domain.regime import RegimePolicy, RegimeResult
from finance_research_agent.market_data.historical import (
    HistoricalBarsRequestFailure,
    HistoricalDailyBarsRequest,
)

__all__ = ["RegimeResearchResult", "run_regime_research"]

type RegimeResearchResult = RegimeResult | HistoricalBarsRequestFailure


def run_regime_research(
    fetcher: HistoricalBarsFetcher,
    request: HistoricalDailyBarsRequest,
    policy: RegimePolicy,
    cutoff_at: datetime,
) -> RegimeResearchResult:
    """Fetch once, preserve request failures, and run the inner workflow once."""

    result = fetcher.fetch_daily_bars(request)
    if isinstance(result, HistoricalBarsRequestFailure):
        return result
    return run_regime_workflow(result, policy, cutoff_at)
