"""Collect typed Product A market-data results before binding collection completion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pydantic import TypeAdapter, ValidationError

from finance_research_agent.application.ports import (
    Clock,
    MarketDataProvider,
    ProviderRequestObserver,
    TradingCalendar,
)
from finance_research_agent.domain.enums import Session
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    InstrumentIdentity,
    PriceObservation,
    ProviderFailure,
    Symbol,
)

_SYMBOL_ADAPTER = TypeAdapter(Symbol)


class MarketCalendarUnavailable(RuntimeError):
    """A historical calendar lookup cannot supply the bounded session window."""


def resolve_completed_session_window(
    calendar: TradingCalendar,
    *,
    before: date,
    session_count: int,
) -> tuple[date, ...]:
    """Return the latest bounded number of trading sessions before before.

    before is exclusive so a premarket collection for a market date cannot
    request that day's not-yet-completed regular session.
    """
    if type(before) is not date:
        raise TypeError("before must be a date")
    if before == date.min:
        raise ValueError("before must be after the minimum date")
    if type(session_count) is not int or not 1 <= session_count <= 252:
        raise ValueError("session_count must be a positive integer no greater than 252")

    sessions: list[date] = []
    candidate = before - timedelta(days=1)
    search_days = session_count * 4 + 14
    for _ in range(search_days):
        try:
            is_trading_day = calendar.is_trading_day(candidate)
        except Exception as error:
            raise MarketCalendarUnavailable(
                f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: market calendar unavailable"
            ) from error
        if type(is_trading_day) is not bool:
            raise MarketCalendarUnavailable(
                f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: invalid trading-day result"
            )
        if is_trading_day:
            sessions.append(candidate)
            if len(sessions) == session_count:
                return tuple(reversed(sessions))
        candidate -= timedelta(days=1)

    raise MarketCalendarUnavailable(
        "trading calendar did not return enough sessions within bounded search"
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
            if item.session is not Session.PRE_MARKET:
                raise ValueError("premarket observation must use PRE_MARKET session")
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
    telemetry_observer: ProviderRequestObserver | None = None,
) -> MarketDataCollection:
    """Read identity, completed bars, and premarket data before sampling completion.

    The returned collection preserves caller symbol order and each provider's
    per-symbol failure. A run-level evidence cutoff can be bound to
    ``completed_at`` only after this function returns.
    """
    requested = tuple(symbols)
    if not requested or any(not isinstance(symbol, str) or not symbol for symbol in requested):
        raise ValueError("market collection requires non-empty symbols")
    for symbol in requested:
        try:
            _SYMBOL_ADAPTER.validate_python(symbol, strict=True)
        except ValidationError:
            raise ValueError("ticker symbol must match the canonical format") from None
    if len(requested) != len(set(requested)):
        raise ValueError("market collection symbols must be unique")
    if start > end:
        raise ValueError("market collection start must not follow end")
    if type(expected_sessions) is not tuple or not expected_sessions:
        raise ValueError("market collection requires expected sessions")
    if completed_through_session != expected_sessions[-1]:
        raise ValueError("completed session must be the final expected session")

    if telemetry_observer is None:
        instruments = provider.fetch_instruments(requested)
    else:
        instruments = provider.fetch_instruments(
            requested, telemetry_observer=telemetry_observer
        )
    _validate_instruments(instruments, requested)
    resolved_identities = {
        symbol: item
        for symbol, item in instruments.items()
        if isinstance(item, InstrumentIdentity)
    }

    daily_bar_options = (
        expected_sessions,
        completed_through_session,
        None,
        resolved_identities,
    )
    if telemetry_observer is None:
        daily_bars = provider.fetch_daily_bars(
            requested,
            start,
            end,
            expected_sessions=daily_bar_options[0],
            completed_through_session=daily_bar_options[1],
            evidence_cutoff_at=daily_bar_options[2],
            instrument_identities=daily_bar_options[3],
        )
    else:
        daily_bars = provider.fetch_daily_bars(
            requested,
            start,
            end,
            expected_sessions=daily_bar_options[0],
            completed_through_session=daily_bar_options[1],
            evidence_cutoff_at=daily_bar_options[2],
            instrument_identities=daily_bar_options[3],
            telemetry_observer=telemetry_observer,
        )
    _validate_daily_bars(daily_bars, requested, instruments)

    if telemetry_observer is None:
        premarket = provider.fetch_premarket_observations(
            requested, None, instrument_identities=resolved_identities
        )
    else:
        premarket = provider.fetch_premarket_observations(
            requested,
            None,
            instrument_identities=resolved_identities,
            telemetry_observer=telemetry_observer,
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


def collect_market_data_for_market_date(
    provider: MarketDataProvider,
    clock: Clock,
    calendar: TradingCalendar,
    symbols: Sequence[str],
    *,
    market_date: date,
    session_count: int,
    telemetry_observer: ProviderRequestObserver | None = None,
) -> MarketDataCollection:
    """Collect market evidence for completed sessions before a run market date."""
    expected_sessions = resolve_completed_session_window(
        calendar, before=market_date, session_count=session_count
    )
    return collect_market_data(
        provider,
        clock,
        symbols,
        start=expected_sessions[0],
        end=expected_sessions[-1],
        expected_sessions=expected_sessions,
        completed_through_session=expected_sessions[-1],
        telemetry_observer=telemetry_observer,
    )
