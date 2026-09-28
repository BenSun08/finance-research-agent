from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from finance_research_agent.domain.enums import Coverage, Session
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    InstrumentIdentity,
    PriceObservation,
    ProviderFailure,
)

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
