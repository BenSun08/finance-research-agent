"""Run-scoped market collection driven only by the frozen configuration snapshot."""

from dataclasses import dataclass

from finance_research_agent.application.config_service import configuration_from_snapshot
from finance_research_agent.application.market_collection import (
    MarketDataCollection,
    collect_market_data_for_market_date,
)
from finance_research_agent.application.ports import (
    Clock,
    MarketDataProvider,
    RunRepository,
    TradingCalendar,
)
from finance_research_agent.domain.models import RunContext, StoredRun


@dataclass(frozen=True, slots=True)
class CollectedRunMarketData:
    """Successful run-scoped collection paired with its persisted evidence freeze."""

    frozen_run: StoredRun
    collection: MarketDataCollection


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


def collect_and_freeze_market_data_for_run(
    run: RunContext,
    repository: RunRepository,
    provider: MarketDataProvider,
    clock: Clock,
    calendar: TradingCalendar,
) -> CollectedRunMarketData:
    """Collect from a persisted, unfrozen run and bind the cutoff on success only."""
    if run.evidence_cutoff_at is not None:
        raise ValueError("run evidence is already frozen")

    stored = repository.load(run.run_id)
    if stored is None:
        raise ValueError("collection requires a stored run context")
    if stored.published:
        raise ValueError("published run cannot collect market data")
    if stored.evidence_cutoff_at is not None or stored.run.evidence_cutoff_at is not None:
        raise ValueError("run evidence is already frozen")
    if stored.run != run:
        raise ValueError("collection requires the current stored run context")

    collection = collect_market_data_for_run(run, provider, clock, calendar)
    frozen_run = StoredRun.model_validate(
        repository.freeze_evidence(run.run_id, collection.completed_at), strict=True
    )
    if (
        frozen_run.run_id != run.run_id
        or frozen_run.evidence_cutoff_at != collection.completed_at
        or frozen_run.run.evidence_cutoff_at != collection.completed_at
        or not frozen_run.checkpoints
        or frozen_run.checkpoints[-1].stage != "EVIDENCE_FROZEN"
        or frozen_run.checkpoints[-1].written_at != collection.completed_at
        or frozen_run.checkpoints[-1].evidence_cutoff_at != collection.completed_at
        or frozen_run.checkpoints[-1].resumable
    ):
        raise RuntimeError("repository returned an invalid evidence freeze")
    if frozen_run.run.model_copy(update={"evidence_cutoff_at": None}) != run:
        raise RuntimeError("evidence freeze changed the stored run context")
    return CollectedRunMarketData(frozen_run=frozen_run, collection=collection)
