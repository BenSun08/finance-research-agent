"""Provider-independent orchestration for the deterministic regime calculation."""

from datetime import datetime

from finance_research_agent.application.market_bridge import (
    canonical_to_regime_input,
    historical_instrument_identity,
    historical_to_canonical_snapshot,
)
from finance_research_agent.domain.market import RegimeMarketSnapshot
from finance_research_agent.domain.regime import RegimePolicy, RegimeResult, calculate_regime
from finance_research_agent.market_data.historical import (
    HistoricalBarsOutcome,
    HistoricalDailyBars,
)

__all__ = ["run_regime_workflow"]


def to_market_snapshot(history: HistoricalDailyBars) -> RegimeMarketSnapshot:
    """Compatibility seam that now delegates through both explicit R2 bridges."""

    canonical = historical_to_canonical_snapshot(
        history,
        instrument=historical_instrument_identity(history),
    )
    return canonical_to_regime_input(canonical)


def run_regime_workflow(
    outcomes: tuple[HistoricalBarsOutcome, ...],
    policy: RegimePolicy,
    cutoff_at: datetime,
) -> RegimeResult:
    """Project available histories and run the existing regime calculation once."""

    seen_symbols: set[str] = set()
    for outcome in outcomes:
        if outcome.symbol in seen_symbols:
            raise ValueError(
                f"duplicate HistoricalBarsOutcome for symbol {outcome.symbol!r}"
            )
        seen_symbols.add(outcome.symbol)

    snapshots: dict[str, RegimeMarketSnapshot] = {}
    for outcome in outcomes:
        if isinstance(outcome, HistoricalDailyBars):
            snapshots[outcome.symbol] = to_market_snapshot(outcome)

    return calculate_regime(snapshots, policy, cutoff_at)
