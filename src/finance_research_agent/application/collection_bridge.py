"""Project persisted market collection outcomes into canonical packet inputs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256

from finance_research_agent.application.market_collection import (
    MarketDataCollection,
    SymbolMarketCollection,
)
from finance_research_agent.domain.market import (
    DailyBar,
    MarketDataSource,
    RegimeMarketSnapshot,
)
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    EvidenceItem,
    InstrumentIdentity,
    MarketSnapshot,
    PriceObservation,
    ProviderFailure,
    SourceObservation,
)
from finance_research_agent.domain.types import FrozenMap, JsonValue


@dataclass(frozen=True, slots=True)
class CollectionPacketInputs:
    """Canonical snapshots and citable evidence projected from one collection."""

    market: FrozenMap[str, MarketSnapshot]
    evidence: tuple[EvidenceItem, ...]


def collected_snapshot_to_regime_input(
    snapshot: MarketSnapshot, cutoff_at: datetime
) -> RegimeMarketSnapshot:
    """Project frozen collection bars without requiring a legacy history ID.

    The full canonical snapshot and cutoff bind identity. Numeric values and
    linked evidence IDs pass to the existing regime calculator unchanged.
    """
    snapshot = MarketSnapshot.model_validate(snapshot, strict=True)
    if cutoff_at.utcoffset() != timedelta(0):
        raise ValueError("regime cutoff must be timezone-aware UTC")
    bars = snapshot.completed_daily_bars
    if not bars:
        raise ValueError("regime projection requires completed daily bars")
    if any(
        bar.source_timestamp > cutoff_at
        or bar.retrieved_at > cutoff_at
        or bar.evidence_cutoff_at > cutoff_at
        for bar in bars
    ) or any(
        source.observed_at > cutoff_at or source.retrieved_at > cutoff_at
        for source in snapshot.source_observations
    ):
        raise ValueError("regime input is after evidence cutoff")
    digest = sha256(_canonical_bytes({
        "snapshot": snapshot.model_dump(mode="json"),
        "cutoff_at": cutoff_at.isoformat(),
    })).hexdigest()
    return RegimeMarketSnapshot(
        schema_version="market-snapshot-v1",
        snapshot_id=f"normalized-{digest}",
        symbol=snapshot.instrument.symbol,
        as_of=cutoff_at,
        currency=snapshot.instrument.currency,
        source=MarketDataSource.NORMALIZED_PROVIDER,
        completed_daily_bars=tuple(
            DailyBar(bar.session_date, bar.open, bar.high, bar.low, bar.close, bar.volume)
            for bar in bars
        ),
        quality_flags=snapshot.quality_flags,
        input_evidence_ids=tuple(dict.fromkeys(bar.evidence_id for bar in bars)),
    )


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        _plain_json(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def _quality_flags(*values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(flag for flags in values for flag in flags))


def _json_value(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return FrozenMap({str(key): _json_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_json_value(item) for item in value)
    if value is None or type(value) in (str, int, float, bool):
        return value  # type: ignore[return-value]
    raise TypeError("provider outcome did not serialize to JSON values")


def _source_and_evidence(
    *,
    payload: dict[str, JsonValue],
    provider: str,
    observed_at: datetime,
    retrieved_at: datetime,
    content_type: str,
    quality_flags: tuple[str, ...],
    authority_tier: int,
    instrument_id: str | None,
    event_time: datetime | None,
    citation_label: str,
) -> tuple[SourceObservation, EvidenceItem]:
    digest = sha256(_canonical_bytes(payload)).hexdigest()
    observation = SourceObservation(
        observation_id=f"obs-market-{digest[:24]}",
        provider=provider,
        source_url=None,
        source_hash_sha256=digest,
        observed_at=observed_at,
        retrieved_at=retrieved_at,
        content_type=content_type,
        excerpt="",
        persistence_allowed=True,
        quality_flags=quality_flags,
    )
    evidence = EvidenceItem(
        evidence_id=f"ev-market-{digest[:24]}",
        source=observation,
        authority_tier=authority_tier,
        instrument_id=instrument_id,
        event_time=event_time,
        published_time=None,
        structured_fields=FrozenMap(payload),
        citation_label=citation_label,
    )
    return observation, evidence


def _failure_input(
    failure: ProviderFailure,
    *,
    completed_at: datetime,
    authority_tier: int,
    requested_symbol: str,
    outcome: str,
) -> tuple[SourceObservation, EvidenceItem]:
    if failure.symbol not in (None, requested_symbol):
        raise ValueError("provider failure symbol differs from collection symbol")
    payload: dict[str, JsonValue] = {
        "outcome": outcome,
        "provider": failure.provider,
        "requested_symbol": requested_symbol,
        "scope": failure.scope,
        "error_code": failure.error_code.value,
        "retryable": failure.retryable,
    }
    if failure.symbol is not None:
        payload["symbol"] = failure.symbol
    return _source_and_evidence(
        payload=payload,
        provider=failure.provider,
        observed_at=completed_at,
        retrieved_at=completed_at,
        content_type="application/vnd.finance-research-agent.provider-failure+json",
        quality_flags=(),
        authority_tier=authority_tier,
        instrument_id=failure.symbol,
        event_time=None,
        citation_label=f"{requested_symbol} {outcome.lower().replace('_', ' ')} failure",
    )


def _project_symbol(
    item: SymbolMarketCollection,
    *,
    completed_at: datetime,
    authority_tier: int,
) -> tuple[MarketSnapshot | None, tuple[EvidenceItem, ...]]:
    evidence: list[EvidenceItem] = []
    sources: list[SourceObservation] = []
    outcome_instrument_ids: set[str] = set()
    instrument_result = item.instrument
    if isinstance(instrument_result, ProviderFailure):
        source, failure_evidence = _failure_input(
            instrument_result,
            completed_at=completed_at,
            authority_tier=authority_tier,
            requested_symbol=item.symbol,
            outcome="INSTRUMENT",
        )
        sources.append(source)
        evidence.append(failure_evidence)
        instrument: InstrumentIdentity | None = None
    elif isinstance(instrument_result, InstrumentIdentity):
        instrument = instrument_result
    else:
        raise TypeError("collection instrument result is invalid")
    if instrument is not None and instrument.symbol != item.symbol:
        raise ValueError("collection instrument identity differs from its symbol")

    daily_bars = item.daily_bars
    if isinstance(daily_bars, ProviderFailure):
        source, failure_evidence = _failure_input(
            daily_bars,
            completed_at=completed_at,
            authority_tier=authority_tier,
            requested_symbol=item.symbol,
            outcome="DAILY_BARS",
        )
        sources.append(source)
        evidence.append(failure_evidence)
        bars: tuple[CompletedDailyBar, ...] = ()
    else:
        if type(daily_bars) is not tuple or any(
            not isinstance(bar, CompletedDailyBar) for bar in daily_bars
        ):
            raise TypeError("collection daily-bars result is invalid")
        bar_instrument_ids = {bar.instrument_id for bar in daily_bars}
        outcome_instrument_ids.update(bar_instrument_ids)
        if instrument is not None and bar_instrument_ids != {instrument.instrument_id}:
            raise ValueError("collection daily-bar identity differs from its instrument")
        if len(bar_instrument_ids) > 1:
            raise ValueError("collection daily bars contain multiple instrument identities")
        if any(bar.retrieved_at > completed_at for bar in daily_bars):
            raise ValueError("collection daily-bar retrieval is after collection completion")
        if daily_bars:
            ordered_bars = tuple(sorted(daily_bars, key=lambda bar: bar.session_date))
            providers = {bar.provider for bar in ordered_bars}
            if len(providers) != 1:
                raise ValueError("collection daily bars contain multiple providers")
            projected_bars: list[CompletedDailyBar] = []
            chunk_count = (len(ordered_bars) + 127) // 128
            for chunk_index in range(chunk_count):
                chunk = ordered_bars[chunk_index * 128 : (chunk_index + 1) * 128]
                payload: dict[str, JsonValue] = {
                    "outcome": "DAILY_BARS",
                    "symbol": item.symbol,
                    "source_evidence_ids": tuple(bar.evidence_id for bar in chunk),
                    "bars": tuple(
                        _json_value(bar.model_dump(mode="json", exclude={"evidence_id"}))
                        for bar in chunk
                    ),
                }
                # Retain short-history identities and bound every evidence array.
                if chunk_count > 1:
                    payload.update(chunk_index=chunk_index, chunk_count=chunk_count)
                observed_at = max(bar.source_timestamp for bar in chunk)
                retrieved_at = max(bar.retrieved_at for bar in chunk)
                flags = _quality_flags(*(bar.quality_flags for bar in chunk))
                source, bars_evidence = _source_and_evidence(
                    payload=payload,
                    provider=chunk[0].provider,
                    observed_at=observed_at,
                    retrieved_at=retrieved_at,
                    content_type="application/vnd.finance-research-agent.daily-bars+json",
                    quality_flags=flags,
                    authority_tier=authority_tier,
                    instrument_id=chunk[0].instrument_id,
                    event_time=observed_at,
                    citation_label=f"{item.symbol} completed daily bars",
                )
                sources.append(source)
                evidence.append(bars_evidence)
                projected_bars.extend(
                    bar.model_copy(update={"evidence_id": bars_evidence.evidence_id})
                    for bar in chunk
                )
            bars = tuple(projected_bars)
        else:
            bars = ()

    price_result = item.premarket_observation
    latest_price: PriceObservation | None
    if isinstance(price_result, ProviderFailure):
        source, failure_evidence = _failure_input(
            price_result,
            completed_at=completed_at,
            authority_tier=authority_tier,
            requested_symbol=item.symbol,
            outcome="PREMARKET_PRICE",
        )
        sources.append(source)
        evidence.append(failure_evidence)
        latest_price = None
    else:
        if not isinstance(price_result, PriceObservation):
            raise TypeError("collection premarket result is invalid")
        if instrument is not None and price_result.instrument_id != instrument.instrument_id:
            raise ValueError("collection price identity differs from its instrument")
        if price_result.retrieved_at > completed_at:
            raise ValueError("collection price retrieval is after collection completion")
        outcome_instrument_ids.add(price_result.instrument_id)
        price_payload: dict[str, JsonValue] = {
            "outcome": "PREMARKET_PRICE",
            "symbol": item.symbol,
            "source_evidence_id": price_result.evidence_id,
            "price": _json_value(
                price_result.model_dump(mode="json", exclude={"evidence_id"})
            ),
        }
        source, price_evidence = _source_and_evidence(
            payload=price_payload,
            provider=price_result.provider,
            observed_at=price_result.observed_at,
            retrieved_at=price_result.retrieved_at,
            content_type="application/vnd.finance-research-agent.premarket-price+json",
            quality_flags=price_result.quality_flags,
            authority_tier=authority_tier,
            instrument_id=price_result.instrument_id,
            event_time=price_result.observed_at,
            citation_label=f"{item.symbol} premarket price observation",
        )
        sources.append(source)
        evidence.append(price_evidence)
        latest_price = price_result.model_copy(update={"evidence_id": price_evidence.evidence_id})

    if len(outcome_instrument_ids) > 1:
        raise ValueError("collection market outcomes contain multiple instrument identities")

    if instrument is None:
        return None, tuple(evidence)

    snapshot = MarketSnapshot(
        instrument=instrument,
        latest_price=latest_price,
        completed_daily_bars=bars,
        current_session_bars=(),
        source_observations=tuple(sources),
        quality_flags=_quality_flags(
            *(bar.quality_flags for bar in bars),
            *((latest_price.quality_flags,) if latest_price is not None else ()),
        ),
    )
    return snapshot, tuple(evidence)


def collection_to_packet_inputs(
    collection: MarketDataCollection,
    *,
    authority_tier: int,
) -> CollectionPacketInputs:
    """Project provider-neutral collection data without making policy decisions.

    Authority is explicit because this bridge has no source-policy ownership. Provider
    failure messages are intentionally excluded from persisted evidence.
    """
    if not isinstance(collection, MarketDataCollection):
        raise TypeError("collection must be MarketDataCollection")
    if type(authority_tier) is not int or not 1 <= authority_tier <= 255:
        raise ValueError("authority_tier must be an integer from 1 through 255")
    completed_at = collection.completed_at
    if completed_at.tzinfo is None or completed_at.utcoffset() != timedelta(0):
        raise ValueError("collection completion time must be timezone-aware UTC")
    symbols = tuple(item.symbol for item in collection.symbols)
    if len(set(symbols)) != len(symbols):
        raise ValueError("collection symbols must be unique")

    market: dict[str, MarketSnapshot] = {}
    evidence: list[EvidenceItem] = []
    for item in collection.symbols:
        snapshot, symbol_evidence = _project_symbol(
            item,
            completed_at=completed_at,
            authority_tier=authority_tier,
        )
        if snapshot is not None:
            market[item.symbol] = snapshot
        evidence.extend(symbol_evidence)
    evidence.sort(key=lambda item: item.evidence_id)
    evidence_ids = tuple(item.evidence_id for item in evidence)
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("collection projection produced duplicate evidence identifiers")
    return CollectionPacketInputs(market=FrozenMap(market), evidence=tuple(evidence))
