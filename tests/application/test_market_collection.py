from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from finance_research_agent.domain.enums import Coverage, Session
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.market_calendar import TradingCalendar
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    InstrumentIdentity,
    PriceObservation,
    ProviderFailure,
)


class _TradingCalendar:
    def __init__(self, trading_days: set[date]) -> None:
        self.trading_days = trading_days

    def is_trading_day(self, market_date: date) -> bool:
        return market_date in self.trading_days

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        raise AssertionError("session_open_close is not needed for session selection")

COLLECTED_AT = datetime(2026, 9, 28, 12, 46, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 9, 28, 12, 45, tzinfo=UTC)
SESSIONS = (date(2026, 9, 25),)


def _collect_market_data(*args, **kwargs):
    from finance_research_agent.application.market_collection import collect_market_data

    return collect_market_data(*args, **kwargs)


def _identity(symbol: str) -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id=f"instrument-{symbol.lower()}",
        symbol=symbol,
        name=symbol,
        instrument_type="COMMON_STOCK",
        primary_exchange="NASDAQ",
        listing_country="US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )


def _bar(symbol: str) -> CompletedDailyBar:
    return CompletedDailyBar(
        instrument_id=f"instrument-{symbol.lower()}",
        session_date=SESSIONS[0],
        source_timestamp=datetime(2026, 9, 25, 20, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=1000,
        session=Session.COMPLETED_SESSION,
        provider="alpaca",
        feed="iex",
        coverage=Coverage.SINGLE_EXCHANGE,
        adjustment="all",
        retrieved_at=RETRIEVED_AT,
        evidence_cutoff_at=RETRIEVED_AT,
        evidence_id=f"evidence-{symbol.lower()}",
        quality_flags=(),
    )


def _price(symbol: str) -> PriceObservation:
    return PriceObservation(
        instrument_id=f"instrument-{symbol.lower()}",
        value=Decimal("101"),
        currency="USD",
        session=Session.PRE_MARKET,
        provider="alpaca",
        feed="iex",
        coverage=Coverage.SINGLE_EXCHANGE,
        observed_at=RETRIEVED_AT,
        retrieved_at=RETRIEVED_AT,
        evidence_id=f"price-{symbol.lower()}",
        quality_flags=(),
    )


class _Clock:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def now_utc(self) -> datetime:
        self.calls.append("clock")
        return COLLECTED_AT


class _MarketData:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.daily_bar_cutoff: datetime | None = RETRIEVED_AT
        self.premarket_as_of: datetime | None = RETRIEVED_AT

    def fetch_instruments(
        self, symbols: Sequence[str]
    ) -> Mapping[str, InstrumentIdentity | ProviderFailure]:
        self.calls.append("instruments")
        return {symbol: _identity(symbol) for symbol in symbols}

    def fetch_daily_bars(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        expected_sessions: tuple[date, ...] | None = None,
        completed_through_session: date | None = None,
        evidence_cutoff_at: datetime | None = None,
        instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
    ) -> Mapping[str, tuple[CompletedDailyBar, ...] | ProviderFailure]:
        del start, end, expected_sessions, completed_through_session, instrument_identities
        self.calls.append("daily_bars")
        self.daily_bar_cutoff = evidence_cutoff_at
        return {
            "AAPL": ProviderFailure(
                provider="alpaca",
                symbol="AAPL",
                error_code=ErrorCode.PROVIDER_NO_DATA,
                retryable=False,
                message="fixture symbol failure",
            ),
            "MSFT": (_bar("MSFT"),),
        }

    def fetch_premarket_observations(
        self,
        symbols: Sequence[str],
        as_of: datetime | None,
        *,
        instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
    ) -> Mapping[str, PriceObservation | ProviderFailure]:
        del symbols, instrument_identities
        self.calls.append("premarket")
        self.premarket_as_of = as_of
        return {
            "AAPL": ProviderFailure(
                provider="alpaca",
                symbol="AAPL",
                error_code=ErrorCode.PROVIDER_NO_DATA,
                retryable=False,
                message="fixture symbol failure",
            ),
            "MSFT": _price("MSFT"),
        }


def test_collect_market_data_reads_before_binding_collection_completion() -> None:
    calls: list[str] = []
    market_data = _MarketData(calls)

    result = _collect_market_data(
        market_data,
        _Clock(calls),
        ("AAPL", "MSFT"),
        start=date(2026, 9, 25),
        end=date(2026, 9, 25),
        expected_sessions=SESSIONS,
        completed_through_session=SESSIONS[-1],
    )

    assert calls == ["instruments", "daily_bars", "premarket", "clock"]
    assert market_data.daily_bar_cutoff is None
    assert market_data.premarket_as_of is None
    assert result.completed_at == COLLECTED_AT
    assert tuple(item.symbol for item in result.symbols) == ("AAPL", "MSFT")
    assert isinstance(result.symbols[0].daily_bars, ProviderFailure)
    assert isinstance(result.symbols[0].premarket_observation, ProviderFailure)
    assert result.symbols[1].daily_bars == (_bar("MSFT"),)
    assert result.symbols[1].premarket_observation == _price("MSFT")


def test_collect_market_data_rejects_missing_symbol_results() -> None:
    class IncompleteMarketData(_MarketData):
        def fetch_instruments(
            self, symbols: Sequence[str]
        ) -> Mapping[str, InstrumentIdentity | ProviderFailure]:
            self.calls.append("instruments")
            return {symbol: _identity(symbol) for symbol in symbols[:1]}

    calls: list[str] = []

    with pytest.raises(ValueError, match="one instrument result per requested symbol"):
        _collect_market_data(
            IncompleteMarketData(calls),
            _Clock(calls),
            ("AAPL", "MSFT"),
            start=date(2026, 9, 25),
            end=date(2026, 9, 25),
            expected_sessions=SESSIONS,
            completed_through_session=SESSIONS[-1],
        )


def test_collect_market_data_rejects_provider_reads_after_completion_clock() -> None:
    class LateMarketData(_MarketData):
        def fetch_premarket_observations(
            self,
            symbols: Sequence[str],
            as_of: datetime | None,
            *,
            instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
        ) -> Mapping[str, PriceObservation | ProviderFailure]:
            result = dict(
                super().fetch_premarket_observations(
                    symbols, as_of, instrument_identities=instrument_identities
                )
            )
            result["MSFT"] = _price("MSFT").model_copy(
                update={"retrieved_at": COLLECTED_AT + timedelta(seconds=1)}
            )
            return result

    calls: list[str] = []

    with pytest.raises(ValueError, match="premarket retrieval is after collection completion"):
        _collect_market_data(
            LateMarketData(calls),
            _Clock(calls),
            ("AAPL", "MSFT"),
            start=date(2026, 9, 25),
            end=date(2026, 9, 25),
            expected_sessions=SESSIONS,
            completed_through_session=SESSIONS[-1],
        )


def test_collect_market_data_rejects_bars_for_a_different_instrument() -> None:
    class MisalignedMarketData(_MarketData):
        def fetch_daily_bars(
            self,
            symbols: Sequence[str],
            start: date,
            end: date,
            *,
            expected_sessions: tuple[date, ...] | None = None,
            completed_through_session: date | None = None,
            evidence_cutoff_at: datetime | None = None,
            instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
        ) -> Mapping[str, tuple[CompletedDailyBar, ...] | ProviderFailure]:
            result = dict(
                super().fetch_daily_bars(
                    symbols,
                    start,
                    end,
                    expected_sessions=expected_sessions,
                    completed_through_session=completed_through_session,
                    evidence_cutoff_at=evidence_cutoff_at,
                    instrument_identities=instrument_identities,
                )
            )
            result["MSFT"] = (
                _bar("MSFT").model_copy(update={"instrument_id": "instrument-aapl"}),
            )
            return result

    calls: list[str] = []

    with pytest.raises(ValueError, match="daily-bar instrument identity does not match"):
        _collect_market_data(
            MisalignedMarketData(calls),
            _Clock(calls),
            ("AAPL", "MSFT"),
            start=date(2026, 9, 25),
            end=date(2026, 9, 25),
            expected_sessions=SESSIONS,
            completed_through_session=SESSIONS[-1],
        )


def test_collect_market_data_rejects_non_premarket_observation() -> None:
    class RegularSessionMarketData(_MarketData):
        def fetch_premarket_observations(
            self,
            symbols: Sequence[str],
            as_of: datetime | None,
            *,
            instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
        ) -> Mapping[str, PriceObservation | ProviderFailure]:
            result = dict(
                super().fetch_premarket_observations(
                    symbols, as_of, instrument_identities=instrument_identities
                )
            )
            result["MSFT"] = _price("MSFT").model_copy(
                update={"session": Session.REGULAR}
            )
            return result

    calls: list[str] = []

    with pytest.raises(ValueError, match="premarket observation must use PRE_MARKET session"):
        _collect_market_data(
            RegularSessionMarketData(calls),
            _Clock(calls),
            ("AAPL", "MSFT"),
            start=date(2026, 9, 25),
            end=date(2026, 9, 25),
            expected_sessions=SESSIONS,
            completed_through_session=SESSIONS[-1],
        )


def test_collect_market_data_rejects_noncanonical_ticker_before_provider_call() -> None:
    calls: list[str] = []

    with pytest.raises(ValueError, match="ticker symbol must match the canonical format"):
        _collect_market_data(
            _MarketData(calls),
            _Clock(calls),
            ("aapl",),
            start=date(2026, 9, 25),
            end=date(2026, 9, 25),
            expected_sessions=SESSIONS,
            completed_through_session=SESSIONS[-1],
        )

    assert calls == []


def test_resolve_completed_session_window_skips_weekends_and_holidays() -> None:
    from finance_research_agent.application import market_collection

    calendar = _TradingCalendar(
        {
            date(2026, 9, 28),  # Requested market date must remain exclusive
            date(2026, 9, 25),  # Friday
            date(2026, 9, 24),  # Thursday
            date(2026, 9, 23),  # Wednesday
            date(2026, 9, 21),  # Monday; Tuesday is a holiday
        }
    )

    sessions = market_collection.resolve_completed_session_window(
        cast(TradingCalendar, calendar),
        before=date(2026, 9, 28),  # Monday
        session_count=4,
    )

    assert sessions == (
        date(2026, 9, 21),
        date(2026, 9, 23),
        date(2026, 9, 24),
        date(2026, 9, 25),
    )


def test_resolve_completed_session_window_rejects_invalid_count() -> None:
    from finance_research_agent.application import market_collection

    calendar = _TradingCalendar({date(2026, 9, 25)})

    with pytest.raises(ValueError, match="positive integer"):
        market_collection.resolve_completed_session_window(
            cast(TradingCalendar, calendar),
            before=date(2026, 9, 28),
            session_count=0,
        )


def test_resolve_completed_session_window_rejects_datetime_as_exclusive_date() -> None:
    from finance_research_agent.application import market_collection

    with pytest.raises(TypeError, match="before must be a date"):
        market_collection.resolve_completed_session_window(
            cast(TradingCalendar, _TradingCalendar(set())),
            before=datetime(2026, 9, 28, tzinfo=UTC),
            session_count=1,
        )


def test_resolve_completed_session_window_rejects_minimum_date() -> None:
    from finance_research_agent.application import market_collection

    with pytest.raises(ValueError, match="before must be after the minimum date"):
        market_collection.resolve_completed_session_window(
            cast(TradingCalendar, _TradingCalendar(set())),
            before=date.min,
            session_count=1,
        )


def test_resolve_completed_session_window_wraps_calendar_failure() -> None:
    from finance_research_agent.application import market_collection

    class FailingCalendar(_TradingCalendar):
        def is_trading_day(self, market_date: date) -> bool:
            raise OSError("calendar unavailable")

    with pytest.raises(RuntimeError, match="MARKET_CALENDAR_UNAVAILABLE"):
        market_collection.resolve_completed_session_window(
            cast(TradingCalendar, FailingCalendar(set())),
            before=date(2026, 9, 28),
            session_count=1,
        )


def test_resolve_completed_session_window_rejects_non_boolean_calendar_result() -> None:
    from finance_research_agent.application import market_collection

    class MalformedCalendar(_TradingCalendar):
        def is_trading_day(self, market_date: date) -> bool:
            return cast(bool, None)

    with pytest.raises(RuntimeError, match="invalid trading-day result"):
        market_collection.resolve_completed_session_window(
            cast(TradingCalendar, MalformedCalendar(set())),
            before=date(2026, 9, 28),
            session_count=1,
        )


def test_resolve_completed_session_window_bounds_empty_calendar_search() -> None:
    from finance_research_agent.application import market_collection

    with pytest.raises(RuntimeError, match="enough sessions within bounded search"):
        market_collection.resolve_completed_session_window(
            cast(TradingCalendar, _TradingCalendar(set())),
            before=date(2026, 9, 28),
            session_count=1,
        )


def test_collect_market_data_requires_final_expected_session_before_provider_call() -> None:
    calls: list[str] = []
    sessions = (date(2026, 9, 24), date(2026, 9, 25))

    with pytest.raises(ValueError, match="completed session must be the final expected session"):
        _collect_market_data(
            _MarketData(calls),
            _Clock(calls),
            ("AAPL",),
            start=sessions[0],
            end=sessions[-1],
            expected_sessions=sessions,
            completed_through_session=sessions[0],
        )

    assert calls == []


def test_collect_market_data_for_market_date_resolves_completed_session_bounds() -> None:
    from finance_research_agent.application.market_collection import (
        collect_market_data_for_market_date,
    )

    calls: list[str] = []
    requests: list[tuple[date, date, tuple[date, ...], date]] = []

    class RecordingMarketData(_MarketData):
        def fetch_daily_bars(
            self,
            symbols: Sequence[str],
            start: date,
            end: date,
            *,
            expected_sessions: tuple[date, ...] | None = None,
            completed_through_session: date | None = None,
            evidence_cutoff_at: datetime | None = None,
            instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
        ) -> Mapping[str, tuple[CompletedDailyBar, ...] | ProviderFailure]:
            assert expected_sessions is not None
            assert completed_through_session is not None
            requests.append((start, end, expected_sessions, completed_through_session))
            return super().fetch_daily_bars(
                symbols,
                start,
                end,
                expected_sessions=expected_sessions,
                completed_through_session=completed_through_session,
                evidence_cutoff_at=evidence_cutoff_at,
                instrument_identities=instrument_identities,
            )

    calendar = _TradingCalendar(
        {
            date(2026, 9, 24),
            date(2026, 9, 25),
            date(2026, 9, 28),  # The requested market date is exclusive.
        }
    )
    market_data = RecordingMarketData(calls)

    result = collect_market_data_for_market_date(
        market_data,
        _Clock(calls),
        cast(TradingCalendar, calendar),
        ("AAPL", "MSFT"),
        market_date=date(2026, 9, 28),
        session_count=2,
    )

    assert requests == [
        (
            date(2026, 9, 24),
            date(2026, 9, 25),
            (date(2026, 9, 24), date(2026, 9, 25)),
            date(2026, 9, 25),
        )
    ]
    assert calls == ["instruments", "daily_bars", "premarket", "clock"]
    assert result.completed_at == COLLECTED_AT


def test_collect_market_data_for_market_date_rejects_invalid_window_before_provider_call() -> None:
    from finance_research_agent.application.market_collection import (
        collect_market_data_for_market_date,
    )

    calls: list[str] = []
    with pytest.raises(ValueError, match="session_count"):
        collect_market_data_for_market_date(
            _MarketData(calls),
            _Clock(calls),
            cast(TradingCalendar, _TradingCalendar({date(2026, 9, 25)})),
            ("AAPL", "MSFT"),
            market_date=date(2026, 9, 28),
            session_count=0,
        )

    assert calls == []
