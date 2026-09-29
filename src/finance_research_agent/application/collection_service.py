"""Run-scoped market collection driven only by the frozen configuration snapshot."""

import json
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256

from pydantic import TypeAdapter, ValidationError

from finance_research_agent.application.config_service import configuration_from_snapshot
from finance_research_agent.application.market_collection import (
    MarketDataCollection,
    collect_market_data_for_market_date,
)
from finance_research_agent.application.ports import (
    Clock,
    MarketDataProvider,
    ProviderRequestObserver,
    RunRepository,
    TradingCalendar,
)
from finance_research_agent.domain.models import RunCheckpoint, RunContext, StoredRun
from finance_research_agent.domain.types import FrozenMap

_COLLECTION_ARTIFACT = "market_data_collection"
_COLLECTION_CHECKPOINT = "EVIDENCE_COLLECTED"
_COLLECTION_ADAPTER = TypeAdapter(MarketDataCollection)


@dataclass(frozen=True, slots=True)
class CollectedRunMarketData:
    """Successful run-scoped collection paired with its persisted evidence freeze."""

    frozen_run: StoredRun
    collection: MarketDataCollection


def _canonical_collection_bytes(collection: MarketDataCollection) -> bytes:
    payload = _COLLECTION_ADAPTER.dump_python(collection, mode="json")
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _stored_collection(stored: StoredRun, repository: RunRepository) -> MarketDataCollection:
    checkpoint = next(
        (item for item in reversed(stored.checkpoints) if item.stage == _COLLECTION_CHECKPOINT),
        None,
    )
    if checkpoint is None:
        raise ValueError("run evidence is already frozen without a collected artifact")
    if set(checkpoint.artifact_hashes) != {_COLLECTION_ARTIFACT}:
        raise ValueError("collected checkpoint is missing its collection artifact hash")
    if checkpoint.evidence_cutoff_at is not None or not checkpoint.resumable:
        raise ValueError("collected checkpoint has invalid cutoff or resumability")
    digest = checkpoint.artifact_hashes[_COLLECTION_ARTIFACT]
    payload = repository.read_staged_artifact(stored.run_id, _COLLECTION_ARTIFACT)
    if payload is None or sha256(payload).hexdigest() != digest:
        raise ValueError("staged market-data collection does not match its checkpoint hash")
    try:
        collection = _COLLECTION_ADAPTER.validate_json(payload, strict=True)
    except ValidationError:
        raise ValueError("staged market-data collection is invalid") from None
    if not isinstance(collection, MarketDataCollection):
        raise ValueError("staged market-data collection is invalid")
    if (
        collection.completed_at.tzinfo is None
        or collection.completed_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("staged market-data collection completion must be timezone-aware UTC")
    if checkpoint.written_at != collection.completed_at:
        raise ValueError("collected checkpoint time differs from collection completion")
    return collection


def _collection_symbols_match(run: RunContext, collection: MarketDataCollection) -> bool:
    expected_symbols, _ = _frozen_collection_inputs(run)
    return tuple(item.symbol for item in collection.symbols) == expected_symbols


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
    *,
    telemetry: ProviderRequestObserver | None = None,
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
        telemetry_observer=telemetry,
    )


def load_frozen_market_data_for_run(
    run: RunContext,
    repository: RunRepository,
) -> CollectedRunMarketData:
    """Reload the exact stored collection after evidence has been frozen."""
    stored = repository.load(run.run_id)
    if stored is None or stored.published:
        raise ValueError("frozen collection requires the current unpublished run")
    if stored.run.model_copy(update={"evidence_cutoff_at": None}) != run.model_copy(
        update={"evidence_cutoff_at": None}
    ):
        raise ValueError("frozen collection requires the current stored run context")
    if (
        stored.evidence_cutoff_at is None
        or run.evidence_cutoff_at not in (None, stored.evidence_cutoff_at)
    ):
        raise ValueError("frozen collection requires a matching evidence cutoff")
    collection = _stored_collection(stored, repository)
    if stored.evidence_cutoff_at != collection.completed_at:
        raise ValueError("frozen evidence cutoff differs from collected completion time")
    if not _collection_symbols_match(run, collection):
        raise ValueError("staged market-data collection symbols differ from frozen configuration")
    return CollectedRunMarketData(frozen_run=stored, collection=collection)


def collect_and_freeze_market_data_for_run(
    run: RunContext,
    repository: RunRepository,
    provider: MarketDataProvider,
    clock: Clock,
    calendar: TradingCalendar,
) -> CollectedRunMarketData:
    """Persist collection before freezing, then resume without refreshing evidence."""
    stored = repository.load(run.run_id)
    if stored is None:
        raise ValueError("collection requires a stored run context")
    if stored.published:
        raise ValueError("published run cannot collect market data")
    if stored.run.model_copy(update={"evidence_cutoff_at": None}) != run.model_copy(
        update={"evidence_cutoff_at": None}
    ):
        raise ValueError("collection requires the current stored run context")
    if run.evidence_cutoff_at is not None and run.evidence_cutoff_at != stored.evidence_cutoff_at:
        raise ValueError("run evidence cutoff differs from the stored run")

    if stored.evidence_cutoff_at is not None:
        return load_frozen_market_data_for_run(run, repository)
    if stored.run.evidence_cutoff_at is not None or run.evidence_cutoff_at is not None:
        raise ValueError("run evidence cutoff is inconsistent")

    collected_checkpoint = next(
        (item for item in reversed(stored.checkpoints) if item.stage == _COLLECTION_CHECKPOINT),
        None,
    )
    if collected_checkpoint is not None:
        collection = _stored_collection(stored, repository)
        if not _collection_symbols_match(run, collection):
            raise ValueError(
                "staged market-data collection symbols differ from frozen configuration"
            )
    else:
        payload = repository.read_staged_artifact(run.run_id, _COLLECTION_ARTIFACT)
        if payload is None:
            collection = collect_market_data_for_run(run, provider, clock, calendar)
            payload = _canonical_collection_bytes(collection)
            digest = repository.stage_artifact(run.run_id, _COLLECTION_ARTIFACT, payload)
        else:
            try:
                collection = _COLLECTION_ADAPTER.validate_json(payload, strict=True)
            except ValidationError:
                raise ValueError(
                    "uncheckpointed market-data collection artifact is invalid"
                ) from None
            if not isinstance(collection, MarketDataCollection):
                raise ValueError("uncheckpointed market-data collection artifact is invalid")
            if _canonical_collection_bytes(collection) != payload:
                raise ValueError("uncheckpointed market-data collection artifact is not canonical")
            digest = sha256(payload).hexdigest()
        if not _collection_symbols_match(run, collection):
            raise ValueError("market-data collection symbols differ from frozen configuration")
        if stored.checkpoints:
            latest = stored.checkpoints[-1]
            execution_status = latest.execution_status
            data_quality_status = latest.data_quality_status
            delivery_status = latest.delivery_status
        else:
            execution_status = stored.run.execution_status
            data_quality_status = stored.run.data_quality_status
            delivery_status = stored.run.delivery_status
        repository.checkpoint_if_current(
            run.run_id,
            RunCheckpoint(
                run_id=run.run_id,
                stage=_COLLECTION_CHECKPOINT,
                execution_status=execution_status,
                data_quality_status=data_quality_status,
                delivery_status=delivery_status,
                written_at=collection.completed_at,
                evidence_cutoff_at=None,
                artifact_hashes=FrozenMap({_COLLECTION_ARTIFACT: digest}),
                resumable=True,
            ),
            expected_count=len(stored.checkpoints),
        )

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
