from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext

import pytest

from finance_research_agent.domain.indicators import sma
from finance_research_agent.domain.market import (
    DailyBar,
    InvalidMarketDataError,
    MarketDataSource,
)
from finance_research_agent.market_data.historical import (
    BarAdjustment,
    DailyBarObservation,
    HistoricalBarsFailure,
    HistoricalBarsProvenance,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
    HistoricalDailyBarsRequest,
    MarketDataCoverage,
    MarketDataFeed,
    coverage_for_feed,
    to_market_snapshot,
)

CUTOFF = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 8, 25, 12, 40, tzinfo=UTC)


def _bar(session_date: date = date(2026, 8, 24), close: str = "101") -> DailyBar:
    return DailyBar(
        session_date=session_date,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal(close),
        volume=1_000_000,
    )


def _provenance(
    *,
    feed: MarketDataFeed = MarketDataFeed.IEX,
    retrieved_at: datetime = RETRIEVED_AT,
) -> HistoricalBarsProvenance:
    return HistoricalBarsProvenance(
        provider="alpaca",
        feed=feed,
        coverage=coverage_for_feed(feed),
        adjustment=BarAdjustment.SPLIT,
        requested_start_at=datetime(2026, 8, 24, 4, tzinfo=UTC),
        requested_end_at=datetime(2026, 8, 25, 3, 59, 59, tzinfo=UTC),
        retrieved_at=retrieved_at,
        evidence_cutoff_at=CUTOFF,
        completed_through_session=date(2026, 8, 24),
        adapter_version="alpaca-daily-bars-v1",
    )


def _observation() -> DailyBarObservation:
    return DailyBarObservation(
        source_timestamp=datetime(2026, 8, 24, 4, tzinfo=UTC),
        bar=_bar(),
    )


def test_provider_neutral_history_projects_into_the_existing_core() -> None:
    history = HistoricalDailyBars.create(
        symbol="SPY",
        observations=(_observation(),),
        provenance=_provenance(),
        quality_flags=("ADJUSTMENT_SPLIT", "FEED_IEX_SINGLE_EXCHANGE"),
    )

    snapshot = to_market_snapshot(history)
    metric = sma(snapshot, window=1, cutoff_at=CUTOFF)

    assert snapshot.source is MarketDataSource.NORMALIZED_PROVIDER
    assert snapshot.as_of == CUTOFF
    assert snapshot.completed_daily_bars == (_bar(),)
    assert snapshot.quality_flags == (
        "ADJUSTMENT_SPLIT",
        "FEED_IEX_SINGLE_EXCHANGE",
    )
    assert metric.value == Decimal("101")


def test_history_identity_is_stable_and_provenance_sensitive() -> None:
    first = HistoricalDailyBars.create(
        symbol="SPY",
        observations=(_observation(),),
        provenance=_provenance(),
        quality_flags=("ADJUSTMENT_SPLIT",),
    )
    replay = HistoricalDailyBars.create(
        symbol="SPY",
        observations=(_observation(),),
        provenance=_provenance(),
        quality_flags=("ADJUSTMENT_SPLIT",),
    )
    later = HistoricalDailyBars.create(
        symbol="SPY",
        observations=(_observation(),),
        provenance=_provenance(
            retrieved_at=datetime(2026, 8, 25, 12, 41, tzinfo=UTC)
        ),
        quality_flags=("ADJUSTMENT_SPLIT",),
    )

    assert first == replay
    assert first.history_id == replay.history_id
    assert first.history_id != later.history_id
    assert to_market_snapshot(first).snapshot_id != to_market_snapshot(later).snapshot_id


def test_history_identity_is_independent_of_ambient_decimal_precision() -> None:
    observation = DailyBarObservation(
        source_timestamp=datetime(2026, 8, 24, 4, tzinfo=UTC),
        bar=DailyBar(
            session_date=date(2026, 8, 24),
            open=Decimal("1.234567890123456789"),
            high=Decimal("2"),
            low=Decimal("1"),
            close=Decimal("1.234567890123456789"),
            volume=1_000_000,
        ),
    )

    with localcontext() as context:
        context.prec = 5
        low_precision = HistoricalDailyBars.create(
            symbol="SPY",
            observations=(observation,),
            provenance=_provenance(),
            quality_flags=("ADJUSTMENT_SPLIT",),
        )
    with localcontext() as context:
        context.prec = 50
        high_precision = HistoricalDailyBars.create(
            symbol="SPY",
            observations=(observation,),
            provenance=_provenance(),
            quality_flags=("ADJUSTMENT_SPLIT",),
        )

    assert low_precision.history_id == high_precision.history_id
    assert (
        to_market_snapshot(low_precision).snapshot_id
        == to_market_snapshot(high_precision).snapshot_id
    )


def test_historical_contracts_are_deeply_immutable_at_collection_boundaries() -> None:
    history = HistoricalDailyBars.create(
        symbol="SPY",
        observations=(_observation(),),
        provenance=_provenance(),
        quality_flags=("ADJUSTMENT_SPLIT",),
    )

    with pytest.raises(FrozenInstanceError):
        history.symbol = "QQQ"  # type: ignore[misc]
    with pytest.raises(InvalidMarketDataError, match="immutable tuples"):
        replace(history, observations=[_observation()])  # type: ignore[arg-type]
    with pytest.raises(InvalidMarketDataError, match="immutable tuples"):
        replace(history, quality_flags=["ADJUSTMENT_SPLIT"])  # type: ignore[arg-type]


def test_request_rejects_noncanonical_or_non_utc_inputs() -> None:
    valid = {
        "symbols": ("QQQ", "SPY"),
        "start_at": datetime(2026, 8, 21, 4, tzinfo=UTC),
        "end_at": datetime(2026, 8, 25, 3, 59, 59, tzinfo=UTC),
        "expected_sessions": (date(2026, 8, 21), date(2026, 8, 24)),
        "completed_through_session": date(2026, 8, 24),
        "feed": MarketDataFeed.IEX,
        "adjustment": BarAdjustment.SPLIT,
        "evidence_cutoff_at": CUTOFF,
    }

    request = HistoricalDailyBarsRequest(**valid)
    assert request.symbols == ("QQQ", "SPY")

    with pytest.raises(InvalidMarketDataError, match="canonical"):
        HistoricalDailyBarsRequest(**{**valid, "symbols": ("SPY", "QQQ")})
    with pytest.raises(InvalidMarketDataError, match="UTC"):
        HistoricalDailyBarsRequest(
            **{**valid, "start_at": datetime(2026, 8, 21, 4)}
        )
    with pytest.raises(InvalidMarketDataError, match="completed-through"):
        HistoricalDailyBarsRequest(
            **{
                **valid,
                "completed_through_session": date(2026, 8, 25),
            }
        )
    with pytest.raises(InvalidMarketDataError, match="must be a date"):
        HistoricalDailyBarsRequest(
            **{
                **valid,
                "completed_through_session": datetime(2026, 8, 24, tzinfo=UTC),
            }
        )


def test_premarket_request_cannot_declare_the_current_session_complete() -> None:
    with pytest.raises(InvalidMarketDataError, match="earlier than"):
        HistoricalDailyBarsRequest(
            symbols=("SPY",),
            start_at=datetime(2026, 8, 25, 4, tzinfo=UTC),
            end_at=datetime(2026, 8, 25, 4, tzinfo=UTC),
            expected_sessions=(date(2026, 8, 25),),
            completed_through_session=date(2026, 8, 25),
            feed=MarketDataFeed.IEX,
            adjustment=BarAdjustment.SPLIT,
            evidence_cutoff_at=CUTOFF,
        )


def test_provenance_rejects_impossible_request_and_retrieval_order() -> None:
    with pytest.raises(InvalidMarketDataError, match="retrieved_at"):
        replace(
            _provenance(),
            requested_end_at=datetime(2026, 8, 25, 12, 41, tzinfo=UTC),
        )

    with pytest.raises(InvalidMarketDataError, match="must be a date"):
        replace(
            _provenance(),
            completed_through_session=datetime(2026, 8, 24, tzinfo=UTC),
        )

    with pytest.raises(InvalidMarketDataError, match="earlier than"):
        replace(
            _provenance(),
            completed_through_session=date(2026, 8, 25),
        )


def test_history_rejects_observations_outside_the_request_bounds() -> None:
    observation = DailyBarObservation(
        source_timestamp=datetime(2026, 8, 24, 5, tzinfo=UTC),
        bar=_bar(),
    )
    provenance = replace(
        _provenance(),
        requested_end_at=datetime(2026, 8, 24, 4, 30, tzinfo=UTC),
    )

    with pytest.raises(InvalidMarketDataError, match="request bounds"):
        HistoricalDailyBars.create(
            symbol="SPY",
            observations=(observation,),
            provenance=provenance,
            quality_flags=("ADJUSTMENT_SPLIT",),
        )


def test_feed_has_one_unambiguous_coverage_classification() -> None:
    assert coverage_for_feed(MarketDataFeed.IEX) is MarketDataCoverage.SINGLE_EXCHANGE
    assert coverage_for_feed(MarketDataFeed.SIP) is MarketDataCoverage.CONSOLIDATED_US

    with pytest.raises(InvalidMarketDataError, match="coverage"):
        replace(_provenance(), coverage=MarketDataCoverage.CONSOLIDATED_US)


def test_failure_requires_a_typed_reason_and_canonical_missing_sessions() -> None:
    failure = HistoricalBarsFailure(
        schema_version="historical-bars-failure-v1",
        symbol="SPY",
        reason=HistoricalBarsUnavailableReason.MISSING_EXPECTED_SESSION,
        provenance=_provenance(),
        missing_sessions=(date(2026, 8, 21),),
        quality_flags=("FEED_IEX_SINGLE_EXCHANGE",),
    )

    assert failure.reason is HistoricalBarsUnavailableReason.MISSING_EXPECTED_SESSION

    with pytest.raises(InvalidMarketDataError, match="sorted"):
        replace(
            failure,
            missing_sessions=(date(2026, 8, 22), date(2026, 8, 21)),
        )

    with pytest.raises(InvalidMarketDataError, match="only available"):
        to_market_snapshot(failure)  # type: ignore[arg-type]
