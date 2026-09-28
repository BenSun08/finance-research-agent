"""Collect typed Product A market-data results before binding collection completion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from finance_research_agent.application.ports import Clock, MarketDataProvider
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    InstrumentIdentity,
    PriceObservation,
    ProviderFailure,
)


@dataclass(frozen=True, slots=True)
class SymbolMarketCollection:
    """Provider-neutral results for one requested symbol."""

    symbol: str
    instrument: InstrumentIdentity | ProviderFailure
    daily_bars: tuple[CompletedDailyBar, ...] | ProviderFailure
    premarket_observation: PriceObservation | ProviderFailure


@dataclass(frozen=True, slots=True)
class MarketDataCollection:
    """Caller-ordered market reads and the instant at which collection completed."""

    symbols: tuple[SymbolMarketCollection, ...]
    completed_at: datetime


def _validate_result_keys(
    result: Mapping[str, object], symbols: tuple[str, ...], provider_result: str
) -> None:
    if set(result) != set(symbols):
        raise ValueError(f"market provider must return one {provider_result} per requested symbol")


def _validate_instruments(
    result: Mapping[str, InstrumentIdentity | ProviderFailure], symbols: tuple[str, ...]
) -> None:
    _validate_result_keys(result, symbols, "instrument result")
    for symbol, item in result.items():
        if not isinstance(item, (InstrumentIdentity, ProviderFailure)):
            raise TypeError("market provider returned an invalid instrument result")
        if isinstance(item, InstrumentIdentity) and item.symbol != symbol:
            raise ValueError("instrument result symbol does not match its request key")
        if isinstance(item, ProviderFailure) and item.symbol not in (None, symbol):
            raise ValueError("instrument failure symbol does not match its request key")


def _validate_daily_bars(
    result: Mapping[str, tuple[CompletedDailyBar, ...] | ProviderFailure],
    symbols: tuple[str, ...],
    instruments: Mapping[str, InstrumentIdentity | ProviderFailure],
) -> None:
    _validate_result_keys(result, symbols, "daily-bars result")
    for symbol, item in result.items():
        if isinstance(item, ProviderFailure):
            if item.symbol not in (None, symbol):
                raise ValueError("daily-bars failure symbol does not match its request key")
            continue
        if type(item) is not tuple or any(not isinstance(bar, CompletedDailyBar) for bar in item):
            raise TypeError("market provider returned invalid daily bars")
        instrument = instruments[symbol]
        if isinstance(instrument, InstrumentIdentity) and any(
            bar.instrument_id != instrument.instrument_id for bar in item
        ):
            raise ValueError("daily-bar instrument identity does not match its request symbol")


def _validate_premarket(
    result: Mapping[str, PriceObservation | ProviderFailure],
    symbols: tuple[str, ...],
    instruments: Mapping[str, InstrumentIdentity | ProviderFailure],
) -> None:
    _validate_result_keys(result, symbols, "premarket observation")
    for symbol, item in result.items():
        if not isinstance(item, (PriceObservation, ProviderFailure)):
            raise TypeError("market provider returned an invalid premarket result")
        if isinstance(item, ProviderFailure):
            if item.symbol not in (None, symbol):
                raise ValueError("premarket failure symbol does not match its request key")
        else:
            instrument = instruments[symbol]
            if (
                isinstance(instrument, InstrumentIdentity)
                and item.instrument_id != instrument.instrument_id
            ):
                raise ValueError("premarket instrument identity does not match its request symbol")


def _validate_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


def collect_market_data(
    provider: MarketDataProvider,
    clock: Clock,
    symbols: Sequence[str],
    *,
    start: date,
    end: date,
    expected_sessions: tuple[date, ...],
    completed_through_session: date,
) -> MarketDataCollection:
    """Read identity, completed bars, and premarket data before sampling completion.

    The returned collection preserves caller symbol order and each provider's
    per-symbol failure. A run-level evidence cutoff can be bound to
    ``completed_at`` only after this function returns.
    """
    requested = tuple(symbols)
    if not requested or any(not isinstance(symbol, str) or not symbol for symbol in requested):
        raise ValueError("market collection requires non-empty symbols")
    if len(requested) != len(set(requested)):
        raise ValueError("market collection symbols must be unique")
    if start > end:
        raise ValueError("market collection start must not follow end")
    if type(expected_sessions) is not tuple or not expected_sessions:
        raise ValueError("market collection requires expected sessions")
    if completed_through_session not in expected_sessions:
        raise ValueError("completed session must be one of the expected sessions")

    instruments = provider.fetch_instruments(requested)
    _validate_instruments(instruments, requested)
    resolved_identities = {
        symbol: item
        for symbol, item in instruments.items()
        if isinstance(item, InstrumentIdentity)
    }

    daily_bars = provider.fetch_daily_bars(
        requested,
        start,
        end,
        expected_sessions=expected_sessions,
        completed_through_session=completed_through_session,
        evidence_cutoff_at=None,
        instrument_identities=resolved_identities,
    )
    _validate_daily_bars(daily_bars, requested, instruments)

    premarket = provider.fetch_premarket_observations(
        requested,
        None,
        instrument_identities=resolved_identities,
    )
    _validate_premarket(premarket, requested, instruments)

    completed_at = clock.now_utc()
    _validate_utc(completed_at, "collection completion time")
    for daily_result in daily_bars.values():
        if isinstance(daily_result, tuple) and any(
            bar.retrieved_at > completed_at for bar in daily_result
        ):
            raise ValueError("daily-bar retrieval is after collection completion")
    for premarket_result in premarket.values():
        if (
            isinstance(premarket_result, PriceObservation)
            and premarket_result.retrieved_at > completed_at
        ):
            raise ValueError("premarket retrieval is after collection completion")

    return MarketDataCollection(
        symbols=tuple(
            SymbolMarketCollection(
                symbol=symbol,
                instrument=instruments[symbol],
                daily_bars=daily_bars[symbol],
                premarket_observation=premarket[symbol],
            )
            for symbol in requested
        ),
        completed_at=completed_at,
    )
