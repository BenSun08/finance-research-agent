"""Provider-independent orchestration for the deterministic regime calculation."""

from datetime import datetime

from finance_research_agent.domain.market import MarketSnapshot
from finance_research_agent.domain.regime import RegimePolicy, RegimeResult, calculate_regime
from finance_research_agent.market_data.historical import (
    HistoricalBarsOutcome,
    HistoricalDailyBars,
    to_market_snapshot,
)

__all__ = ["run_regime_workflow"]


def run_regime_workflow(
    outcomes: tuple[HistoricalBarsOutcome, ...],
    policy: RegimePolicy,
    cutoff_at: datetime,
) -> RegimeResult:
    """Project available histories and run the existing regime calculation once."""

    snapshots: dict[str, MarketSnapshot] = {}
    for outcome in outcomes:
        if isinstance(outcome, HistoricalDailyBars):
            snapshots[outcome.symbol] = to_market_snapshot(outcome)

    return calculate_regime(snapshots, policy, cutoff_at)
