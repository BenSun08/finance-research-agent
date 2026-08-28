"""Offline normalization of Alpaca-shaped completed daily-bar records."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from math import isfinite

from finance_research_agent.market_data.historical import (
    FAILURE_SCHEMA_VERSION,
    BarAdjustment,
    DailyBarObservation,
    HistoricalBarsFailure,
    HistoricalBarsOutcome,
    HistoricalBarsProvenance,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
    HistoricalDailyBarsRequest,
    InvalidMarketDataError,
    MarketDataFeed,
    coverage_for_feed,
    create_daily_bar_observation,
)

ADAPTER_VERSION = "alpaca-daily-bars-v1"


@dataclass(frozen=True, slots=True)
class AlpacaDailyBarRecord:
    """Minimum attributes mirrored from one future Alpaca SDK daily Bar."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def _price(value: float) -> Decimal:
    if not isinstance(value, float) or not isfinite(value):
        raise InvalidMarketDataError("Alpaca bar prices must be finite floats")
    return Decimal(str(value))


def _volume(value: float) -> int:
    if (
        not isinstance(value, float)
        or not isfinite(value)
        or value < 0
        or not value.is_integer()
    ):
        raise InvalidMarketDataError(
            "Alpaca bar volume must be a finite non-negative integer value"
        )
    return int(value)


def _provenance(
    request: HistoricalDailyBarsRequest,
    retrieved_at: datetime,
) -> HistoricalBarsProvenance:
    return HistoricalBarsProvenance(
        provider="alpaca",
        feed=request.feed,
        coverage=coverage_for_feed(request.feed),
        adjustment=request.adjustment,
        requested_start_at=request.start_at,
        requested_end_at=request.end_at,
        retrieved_at=retrieved_at,
        evidence_cutoff_at=request.evidence_cutoff_at,
        completed_through_session=request.completed_through_session,
        adapter_version=ADAPTER_VERSION,
    )


def _base_quality_flags(
    feed: MarketDataFeed,
    adjustment: BarAdjustment,
) -> tuple[str, ...]:
    feed_flag = (
        "FEED_IEX_SINGLE_EXCHANGE"
        if feed is MarketDataFeed.IEX
        else "FEED_SIP_CONSOLIDATED_US"
    )
    return tuple(
        sorted(
            (
                f"ADJUSTMENT_{adjustment.value.upper()}",
                feed_flag,
                "SOURCE_ORDER_NORMALIZED",
            )
        )
    )


def _failure(
    *,
    symbol: str,
    reason: HistoricalBarsUnavailableReason,
    provenance: HistoricalBarsProvenance,
    missing_sessions: tuple[date, ...],
    quality_flags: tuple[str, ...],
) -> HistoricalBarsFailure:
    return HistoricalBarsFailure(
        schema_version=FAILURE_SCHEMA_VERSION,
        symbol=symbol,
        reason=reason,
        provenance=provenance,
        missing_sessions=missing_sessions,
        quality_flags=quality_flags,
    )


def _normalize_record(
    record: AlpacaDailyBarRecord,
    symbol: str,
) -> DailyBarObservation:
    if not isinstance(record, AlpacaDailyBarRecord) or record.symbol != symbol:
        raise InvalidMarketDataError("Alpaca bar symbol does not match the requested symbol")
    if record.timestamp.tzinfo is None:
        raise InvalidMarketDataError("Alpaca bar timestamp must be timezone-aware")
    source_timestamp = record.timestamp.astimezone(UTC)
    return create_daily_bar_observation(
        source_timestamp=source_timestamp,
        open_price=_price(record.open),
        high=_price(record.high),
        low=_price(record.low),
        close=_price(record.close),
        volume=_volume(record.volume),
    )


def normalize_alpaca_daily_bars(
    records_by_symbol: Mapping[str, Sequence[AlpacaDailyBarRecord]],
    *,
    request: HistoricalDailyBarsRequest,
    retrieved_at: datetime,
) -> tuple[HistoricalBarsOutcome, ...]:
    """Normalize one already-materialized Alpaca-shaped response without I/O."""

    if not isinstance(records_by_symbol, Mapping):
        raise InvalidMarketDataError("Alpaca records must be keyed by symbol")
    if not isinstance(request, HistoricalDailyBarsRequest):
        raise InvalidMarketDataError("request must be HistoricalDailyBarsRequest")
    unexpected_symbols = set(records_by_symbol) - set(request.symbols)
    if unexpected_symbols:
        raise InvalidMarketDataError("Alpaca response contains unrequested symbols")

    provenance = _provenance(request, retrieved_at)
    base_flags = _base_quality_flags(request.feed, request.adjustment)
    expected_dates = set(request.expected_sessions)
    outcomes: list[HistoricalBarsOutcome] = []

    for symbol in request.symbols:
        provider_records = tuple(records_by_symbol.get(symbol, ()))
        if not provider_records:
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.NO_DATA,
                    provenance=provenance,
                    missing_sessions=request.expected_sessions,
                    quality_flags=base_flags,
                )
            )
            continue

        try:
            normalized = tuple(
                _normalize_record(record, symbol) for record in provider_records
            )
        except (AttributeError, InvalidMarketDataError, TypeError, ValueError):
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.MALFORMED_BAR,
                    provenance=provenance,
                    missing_sessions=(),
                    quality_flags=base_flags,
                )
            )
            continue

        by_session: dict[date, DailyBarObservation] = {}
        duplicate_removed = False
        duplicate_conflict = False
        for observation in normalized:
            session = observation.bar.session_date
            existing = by_session.get(session)
            if existing is None:
                by_session[session] = observation
            elif existing == observation:
                duplicate_removed = True
            else:
                duplicate_conflict = True
                break

        if duplicate_conflict:
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.DUPLICATE_CONFLICT,
                    provenance=provenance,
                    missing_sessions=(),
                    quality_flags=base_flags,
                )
            )
            continue

        observations = tuple(by_session[session] for session in sorted(by_session))
        dates = tuple(observation.bar.session_date for observation in observations)
        flags = set(base_flags)
        if duplicate_removed:
            flags.add("EXACT_DUPLICATE_REMOVED")
        quality_flags = tuple(sorted(flags))

        if any(session > request.completed_through_session for session in dates):
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.FUTURE_OR_INCOMPLETE_BAR,
                    provenance=provenance,
                    missing_sessions=(),
                    quality_flags=quality_flags,
                )
            )
            continue
        if any(session not in expected_dates for session in dates):
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.MALFORMED_BAR,
                    provenance=provenance,
                    missing_sessions=(),
                    quality_flags=quality_flags,
                )
            )
            continue

        missing_sessions = tuple(
            session for session in request.expected_sessions if session not in set(dates)
        )
        if dates[-1] < request.completed_through_session:
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.STALE,
                    provenance=provenance,
                    missing_sessions=missing_sessions,
                    quality_flags=quality_flags,
                )
            )
            continue
        if missing_sessions:
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.MISSING_EXPECTED_SESSION,
                    provenance=provenance,
                    missing_sessions=missing_sessions,
                    quality_flags=quality_flags,
                )
            )
            continue

        try:
            outcomes.append(
                HistoricalDailyBars.create(
                    symbol=symbol,
                    observations=observations,
                    provenance=provenance,
                    quality_flags=quality_flags,
                )
            )
        except InvalidMarketDataError:
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.MALFORMED_BAR,
                    provenance=provenance,
                    missing_sessions=(),
                    quality_flags=quality_flags,
                )
            )

    return tuple(outcomes)
