"""Deterministic bridges from historical outcomes to Product A market evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256

from finance_research_agent.domain.enums import Coverage, Session
from finance_research_agent.domain.market import (
    DailyBar,
    InvalidMarketDataError,
    MarketDataSource,
    RegimeMarketSnapshot,
)
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    EvidenceItem,
    InstrumentIdentity,
    MarketSnapshot,
    SourceObservation,
)
from finance_research_agent.domain.types import FrozenMap, JsonValue
from finance_research_agent.market_data.historical import (
    HistoricalBarsFailure,
    HistoricalBarsOutcome,
    HistoricalBarsRequestFailure,
    HistoricalDailyBars,
    MarketDataCoverage,
)

__all__ = [
    "canonical_to_regime_input",
    "historical_instrument_identity",
    "historical_outcome_to_evidence",
    "historical_request_failure_to_evidence",
    "historical_to_canonical_snapshot",
]


def _coverage(value: MarketDataCoverage) -> Coverage:
    if value is MarketDataCoverage.SINGLE_EXCHANGE:
        return Coverage.SINGLE_EXCHANGE
    if value is MarketDataCoverage.CONSOLIDATED_US:
        return Coverage.CONSOLIDATED
    raise InvalidMarketDataError("unsupported historical market-data coverage")


def _structured_fields(outcome: HistoricalBarsOutcome) -> FrozenMap[str, JsonValue]:
    provenance = outcome.provenance
    fields: dict[str, JsonValue] = {
        "adapter_version": provenance.adapter_version,
        "adjustment": provenance.adjustment.value,
        "completed_through_session": provenance.completed_through_session.isoformat(),
        "coverage": provenance.coverage.value,
        "evidence_cutoff_at": provenance.evidence_cutoff_at.isoformat(),
        "feed": provenance.feed.value,
        "outcome_kind": (
            "AVAILABLE" if isinstance(outcome, HistoricalDailyBars) else "SYMBOL_FAILURE"
        ),
        "provider": provenance.provider,
        "requested_end_at": provenance.requested_end_at.isoformat(),
        "requested_start_at": provenance.requested_start_at.isoformat(),
        "retrieved_at": provenance.retrieved_at.isoformat(),
        "symbol": outcome.symbol,
    }
    if isinstance(outcome, HistoricalDailyBars):
        fields["history_id"] = outcome.history_id
    else:
        fields["reason"] = outcome.reason.value
        fields["missing_sessions"] = tuple(
            session.isoformat() for session in outcome.missing_sessions
        )
    return FrozenMap(fields)


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _identity_payload(outcome: HistoricalBarsOutcome) -> Mapping[str, object]:
    payload: dict[str, object] = dict(_structured_fields(outcome))
    payload["quality_flags"] = outcome.quality_flags
    payload["schema_version"] = outcome.schema_version
    if isinstance(outcome, HistoricalDailyBars):
        payload["currency"] = outcome.currency
        payload["observations"] = tuple(
            {
                "close": _decimal_text(observation.bar.close),
                "high": _decimal_text(observation.bar.high),
                "low": _decimal_text(observation.bar.low),
                "open": _decimal_text(observation.bar.open),
                "session_date": observation.bar.session_date.isoformat(),
                "source_timestamp": observation.source_timestamp.isoformat(),
                "volume": observation.bar.volume,
            }
            for observation in outcome.observations
        )
    return payload


def _digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _source_observation(outcome: HistoricalBarsOutcome) -> SourceObservation:
    digest = _digest(_identity_payload(outcome))
    if isinstance(outcome, HistoricalDailyBars):
        observation_id = outcome.history_id
        observed_at = outcome.observations[-1].source_timestamp
    else:
        observation_id = f"history-failure-{digest[:24]}"
        observed_at = outcome.provenance.retrieved_at
    return SourceObservation(
        observation_id=observation_id,
        provider=outcome.provenance.provider,
        source_url=None,
        source_hash_sha256=digest,
        observed_at=observed_at,
        retrieved_at=outcome.provenance.retrieved_at,
        content_type="application/vnd.finance-research-agent.historical-bars+json",
        excerpt="",
        persistence_allowed=True,
        quality_flags=outcome.quality_flags,
    )


def _evidence_id(outcome: HistoricalBarsOutcome) -> str:
    digest = _digest(_identity_payload(outcome))
    prefix = "ev-history" if isinstance(outcome, HistoricalDailyBars) else "ev-history-failure"
    return f"{prefix}-{digest[:24]}"


def historical_outcome_to_evidence(
    outcome: HistoricalBarsOutcome,
    *,
    authority_tier: int,
) -> EvidenceItem:
    """Map one available/per-symbol historical outcome to one citable item.

    Authority remains an explicit caller-owned input; this bridge does not rank
    providers or decide source policy.
    """

    if not isinstance(outcome, (HistoricalDailyBars, HistoricalBarsFailure)):
        raise TypeError("historical evidence requires a per-symbol outcome")
    return EvidenceItem(
        evidence_id=_evidence_id(outcome),
        source=_source_observation(outcome),
        authority_tier=authority_tier,
        instrument_id=outcome.symbol,
        event_time=(
            outcome.observations[-1].source_timestamp
            if isinstance(outcome, HistoricalDailyBars)
            else None
        ),
        published_time=None,
        structured_fields=_structured_fields(outcome),
        citation_label=(
            f"{outcome.symbol} completed historical daily bars"
            if isinstance(outcome, HistoricalDailyBars)
            else f"{outcome.symbol} historical daily-bars failure"
        ),
    )


def historical_request_failure_to_evidence(
    failure: HistoricalBarsRequestFailure,
    *,
    source: SourceObservation,
    authority_tier: int,
) -> EvidenceItem:
    """Map request-global unavailability without fabricating a symbol or provenance."""

    if not isinstance(failure, HistoricalBarsRequestFailure):
        raise TypeError("request failure evidence requires HistoricalBarsRequestFailure")
    digest = _digest(
        {
            "observation_id": source.observation_id,
            "reason": failure.reason.value,
            "source_hash_sha256": source.source_hash_sha256,
        }
    )
    return EvidenceItem(
        evidence_id=f"ev-history-request-failure-{digest[:24]}",
        source=source,
        authority_tier=authority_tier,
        instrument_id=None,
        event_time=None,
        published_time=None,
        structured_fields=FrozenMap(
            {"outcome_kind": "REQUEST_FAILURE", "reason": failure.reason.value}
        ),
        citation_label="Historical daily-bars request failure",
    )


def historical_instrument_identity(history: HistoricalDailyBars) -> InstrumentIdentity:
    """Return the compatibility identity possible from history alone.

    Unknown classification remains explicit; later identity collection replaces
    this compatibility value rather than treating it as eligibility evidence.
    """

    if not isinstance(history, HistoricalDailyBars):
        raise TypeError("historical identity requires available historical bars")
    return InstrumentIdentity(
        instrument_id=history.symbol,
        symbol=history.symbol,
        name=history.symbol,
        instrument_type="UNKNOWN",
        primary_exchange=None,
        listing_country=None,
        currency=history.currency,
        is_active=None,
        is_leveraged=None,
        is_inverse=None,
        is_otc=None,
    )


def historical_to_canonical_snapshot(
    history: HistoricalDailyBars,
    *,
    instrument: InstrumentIdentity,
) -> MarketSnapshot:
    """Bridge complete history to a canonical Product A market snapshot."""

    if not isinstance(history, HistoricalDailyBars):
        raise InvalidMarketDataError("only available historical bars can form a snapshot")
    if instrument.symbol != history.symbol or instrument.instrument_id != history.symbol:
        raise InvalidMarketDataError("instrument identity must match historical symbol")
    if instrument.currency != history.currency:
        raise InvalidMarketDataError("instrument currency must match historical currency")

    source = _source_observation(history)
    evidence_id = _evidence_id(history)
    provenance = history.provenance
    bars = tuple(
        CompletedDailyBar(
            instrument_id=instrument.instrument_id,
            session_date=observation.bar.session_date,
            source_timestamp=observation.source_timestamp,
            open=observation.bar.open,
            high=observation.bar.high,
            low=observation.bar.low,
            close=observation.bar.close,
            volume=observation.bar.volume,
            session=Session.COMPLETED_SESSION,
            provider=provenance.provider,
            feed=provenance.feed.value,
            coverage=_coverage(provenance.coverage),
            adjustment=provenance.adjustment.value,
            retrieved_at=provenance.retrieved_at,
            evidence_cutoff_at=provenance.evidence_cutoff_at,
            evidence_id=evidence_id,
            quality_flags=history.quality_flags,
        )
        for observation in history.observations
    )
    return MarketSnapshot(
        instrument=instrument,
        latest_price=None,
        completed_daily_bars=bars,
        current_session_bars=(),
        source_observations=(source,),
        quality_flags=history.quality_flags,
    )


def canonical_to_regime_input(snapshot: MarketSnapshot) -> RegimeMarketSnapshot:
    """Project a history-backed canonical snapshot into the unchanged numeric core."""

    if not isinstance(snapshot, MarketSnapshot):
        raise TypeError("regime projection requires canonical MarketSnapshot")
    history_ids = tuple(
        source.observation_id
        for source in snapshot.source_observations
        if source.observation_id.startswith("history-")
    )
    if len(history_ids) != 1:
        raise InvalidMarketDataError(
            "regime projection requires exactly one historical source observation"
        )
    if not snapshot.completed_daily_bars:
        raise InvalidMarketDataError("regime projection requires completed daily bars")
    evidence_cutoffs = {
        bar.evidence_cutoff_at for bar in snapshot.completed_daily_bars
    }
    if len(evidence_cutoffs) != 1:
        raise InvalidMarketDataError("completed daily bars must share an evidence cutoff")
    evidence_ids = tuple(
        dict.fromkeys(bar.evidence_id for bar in snapshot.completed_daily_bars)
    )
    digest = history_ids[0].removeprefix("history-")
    return RegimeMarketSnapshot(
        schema_version="market-snapshot-v1",
        snapshot_id=f"normalized-{digest}",
        symbol=snapshot.instrument.symbol,
        as_of=next(iter(evidence_cutoffs)),
        currency=snapshot.instrument.currency,
        source=MarketDataSource.NORMALIZED_PROVIDER,
        completed_daily_bars=tuple(
            DailyBar(
                session_date=bar.session_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in snapshot.completed_daily_bars
        ),
        quality_flags=snapshot.quality_flags,
        input_evidence_ids=evidence_ids,
    )
