from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from finance_research_agent.adapters.alpaca import (
    AlpacaDailyBarRecord,
    normalize_alpaca_daily_bars,
)
from finance_research_agent.domain.indicators import sma
from finance_research_agent.market_data.historical import (
    BarAdjustment,
    HistoricalBarsFailure,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
    HistoricalDailyBarsRequest,
    MarketDataCoverage,
    MarketDataFeed,
    to_market_snapshot,
)

CUTOFF = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 8, 25, 12, 40, tzinfo=UTC)


def _request(
    *,
    symbols: tuple[str, ...] = ("SPY",),
    start_at: datetime = datetime(2026, 8, 21, 4, tzinfo=UTC),
    expected_sessions: tuple[date, ...] = (date(2026, 8, 21), date(2026, 8, 24)),
    completed_through_session: date = date(2026, 8, 24),
    feed: MarketDataFeed = MarketDataFeed.IEX,
) -> HistoricalDailyBarsRequest:
    return HistoricalDailyBarsRequest(
        symbols=symbols,
        start_at=start_at,
        end_at=datetime(2026, 8, 25, 3, 59, 59, tzinfo=UTC),
        expected_sessions=expected_sessions,
        completed_through_session=completed_through_session,
        feed=feed,
        adjustment=BarAdjustment.SPLIT,
        evidence_cutoff_at=CUTOFF,
    )


def _record(
    session_date: date,
    *,
    symbol: str = "SPY",
    open_price: float = 100.0,
    high: float = 102.0,
    low: float = 99.0,
    close: float = 101.0,
    volume: float = 1_000_000.0,
    timestamp: datetime | None = None,
) -> AlpacaDailyBarRecord:
    source_timestamp = timestamp or datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        4,
        tzinfo=UTC,
    )
    return AlpacaDailyBarRecord(
        symbol=symbol,
        timestamp=source_timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _only_success(
    records: dict[str, list[AlpacaDailyBarRecord]],
    *,
    request: HistoricalDailyBarsRequest | None = None,
) -> HistoricalDailyBars:
    outcomes = normalize_alpaca_daily_bars(
        records,
        request=request or _request(),
        retrieved_at=RETRIEVED_AT,
    )
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], HistoricalDailyBars)
    return outcomes[0]


def _only_failure(
    records: dict[str, list[AlpacaDailyBarRecord]],
    *,
    request: HistoricalDailyBarsRequest | None = None,
) -> HistoricalBarsFailure:
    outcomes = normalize_alpaca_daily_bars(
        records,
        request=request or _request(),
        retrieved_at=RETRIEVED_AT,
    )
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], HistoricalBarsFailure)
    return outcomes[0]


def test_alpaca_records_normalize_to_exact_decimal_core_input() -> None:
    history = _only_success(
        {
            "SPY": [
                _record(date(2026, 8, 24), close=101.25),
                _record(date(2026, 8, 21), close=100.1),
            ]
        }
    )

    assert tuple(item.bar.session_date for item in history.observations) == (
        date(2026, 8, 21),
        date(2026, 8, 24),
    )
    assert history.observations[-1].bar.close == Decimal("101.25")
    assert history.provenance.provider == "alpaca"
    assert history.provenance.coverage is MarketDataCoverage.SINGLE_EXCHANGE
    assert history.quality_flags == (
        "ADJUSTMENT_SPLIT",
        "FEED_IEX_SINGLE_EXCHANGE",
        "SOURCE_ORDER_NORMALIZED",
    )

    metric = sma(to_market_snapshot(history), window=2, cutoff_at=CUTOFF)
    assert metric.value == Decimal("100.675")


def test_input_order_does_not_change_canonical_history_identity() -> None:
    first = _record(date(2026, 8, 21))
    second = _record(date(2026, 8, 24), close=101.5)

    ascending = _only_success({"SPY": [first, second]})
    descending = _only_success({"SPY": [second, first]})

    assert ascending == descending
    assert ascending.history_id == descending.history_id


@pytest.mark.parametrize("records", [{}, {"SPY": []}])
def test_missing_symbol_and_empty_response_are_structured_no_data(
    records: dict[str, list[AlpacaDailyBarRecord]],
) -> None:
    failure = _only_failure(records)

    assert failure.reason is HistoricalBarsUnavailableReason.NO_DATA
    assert failure.missing_sessions == (date(2026, 8, 21), date(2026, 8, 24))


def test_missing_internal_session_and_stale_tail_are_distinct() -> None:
    internal_request = _request(
        start_at=datetime(2026, 8, 20, 4, tzinfo=UTC),
        expected_sessions=(date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24)),
    )
    missing = _only_failure(
        {"SPY": [_record(date(2026, 8, 20)), _record(date(2026, 8, 24))]},
        request=internal_request,
    )
    stale = _only_failure({"SPY": [_record(date(2026, 8, 21))]})

    assert missing.reason is HistoricalBarsUnavailableReason.MISSING_EXPECTED_SESSION
    assert missing.missing_sessions == (date(2026, 8, 21),)
    assert stale.reason is HistoricalBarsUnavailableReason.STALE
    assert stale.missing_sessions == (date(2026, 8, 24),)


def test_current_session_bar_is_rejected_at_a_premarket_cutoff() -> None:
    failure = _only_failure(
        {
            "SPY": [
                _record(date(2026, 8, 21)),
                _record(date(2026, 8, 24)),
                _record(date(2026, 8, 25)),
            ]
        }
    )

    assert failure.reason is HistoricalBarsUnavailableReason.FUTURE_OR_INCOMPLETE_BAR


def test_exact_duplicates_collapse_but_conflicting_duplicates_fail_closed() -> None:
    first = _record(date(2026, 8, 21))
    latest = _record(date(2026, 8, 24))
    collapsed = _only_success({"SPY": [first, first, latest]})
    conflict = _only_failure(
        {
            "SPY": [
                first,
                _record(date(2026, 8, 21), close=101.5),
                latest,
            ]
        }
    )

    assert len(collapsed.observations) == 2
    assert "EXACT_DUPLICATE_REMOVED" in collapsed.quality_flags
    assert conflict.reason is HistoricalBarsUnavailableReason.DUPLICATE_CONFLICT


@pytest.mark.parametrize(
    "record",
    [
        _record(date(2026, 8, 21), open_price=float("nan")),
        _record(date(2026, 8, 21), high=98.0),
        _record(date(2026, 8, 21), volume=-1.0),
        _record(date(2026, 8, 21), volume=1.5),
        _record(
            date(2026, 8, 21),
            timestamp=datetime(2026, 8, 21, 4),
        ),
        _record(date(2026, 8, 21), symbol="QQQ"),
    ],
)
def test_malformed_provider_values_fail_only_the_affected_symbol(
    record: AlpacaDailyBarRecord,
) -> None:
    failure = _only_failure(
        {"SPY": [record, _record(date(2026, 8, 24))]}
    )

    assert failure.reason is HistoricalBarsUnavailableReason.MALFORMED_BAR


@pytest.mark.parametrize(
    ("timestamp", "expected_session"),
    [
        (datetime(2026, 1, 5, 5, tzinfo=UTC), date(2026, 1, 5)),
        (datetime(2026, 8, 24, 4, tzinfo=UTC), date(2026, 8, 24)),
    ],
)
def test_session_date_uses_new_york_time_across_est_and_edt(
    timestamp: datetime,
    expected_session: date,
) -> None:
    request = HistoricalDailyBarsRequest(
        symbols=("SPY",),
        start_at=timestamp,
        end_at=timestamp,
        expected_sessions=(expected_session,),
        completed_through_session=expected_session,
        feed=MarketDataFeed.IEX,
        adjustment=BarAdjustment.SPLIT,
        evidence_cutoff_at=datetime(2026, 8, 25, 12, 45, tzinfo=UTC),
    )
    history = _only_success(
        {"SPY": [_record(expected_session, timestamp=timestamp)]},
        request=request,
    )

    assert history.observations[0].bar.session_date == expected_session


def test_one_symbol_failure_does_not_discard_another_symbol() -> None:
    request = _request(symbols=("QQQ", "SPY"))
    outcomes = normalize_alpaca_daily_bars(
        {
            "QQQ": [],
            "SPY": [_record(date(2026, 8, 21)), _record(date(2026, 8, 24))],
        },
        request=request,
        retrieved_at=RETRIEVED_AT,
    )

    assert tuple(outcome.symbol for outcome in outcomes) == ("QQQ", "SPY")
    assert isinstance(outcomes[0], HistoricalBarsFailure)
    assert isinstance(outcomes[1], HistoricalDailyBars)
