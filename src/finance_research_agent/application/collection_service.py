"""Run-scoped market collection driven only by the frozen configuration snapshot."""

from finance_research_agent.application.config_service import configuration_from_snapshot
from finance_research_agent.application.market_collection import (
    MarketDataCollection,
    collect_market_data_for_market_date,
)
from finance_research_agent.application.ports import Clock, MarketDataProvider, TradingCalendar
from finance_research_agent.domain.models import RunContext


def _frozen_collection_inputs(run: RunContext) -> tuple[tuple[str, ...], int]:
    snapshot = run.configuration_snapshot
    configuration = configuration_from_snapshot(snapshot)

    symbols: dict[str, None] = {}
    for item in configuration.watchlist.items:
        symbols.setdefault(item.symbol, None)
        if item.benchmark_symbol is not None:
            symbols.setdefault(item.benchmark_symbol, None)
        if item.sector_proxy_symbol is not None:
            symbols.setdefault(item.sector_proxy_symbol, None)
    for symbol in configuration.regime.radar_universe:
        symbols.setdefault(symbol, None)
    return tuple(symbols), configuration.setup.required_historical_sessions


def collect_market_data_for_run(
    run: RunContext,
    provider: MarketDataProvider,
    clock: Clock,
    calendar: TradingCalendar,
) -> MarketDataCollection:
    """Collect the run's frozen watchlist and radar data within one bounded window."""
    if run.evidence_cutoff_at is not None:
        raise ValueError("market collection cannot run after evidence cutoff")
    symbols, session_count = _frozen_collection_inputs(run)
    return collect_market_data_for_market_date(
        provider,
        clock,
        calendar,
        symbols,
        market_date=run.market_date,
        session_count=session_count,
    )
