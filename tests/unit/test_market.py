from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from finance_research_agent.domain.market import (
    DailyBar,
    InvalidMarketDataError,
    MarketDataSource,
    MarketSnapshot,
)


def test_market_snapshot_accepts_valid_completed_synthetic_bars() -> None:
    bars = (
        DailyBar(
            session_date=date(2026, 8, 21),
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("99"),
            close=Decimal("101"),
            volume=1_000_000,
        ),
        DailyBar(
            session_date=date(2026, 8, 24),
            open=Decimal("101"),
            high=Decimal("103"),
            low=Decimal("100"),
            close=Decimal("102"),
            volume=None,
        ),
    )

    snapshot = MarketSnapshot(
        schema_version="market-snapshot-v1",
        snapshot_id="synthetic-spy-valid",
        symbol="SPY",
        as_of=datetime(2026, 8, 25, tzinfo=UTC),
        currency="USD",
        source=MarketDataSource.SYNTHETIC,
        completed_daily_bars=bars,
        quality_flags=(),
    )

    assert snapshot.completed_daily_bars == bars
    assert snapshot.symbol == "SPY"


@pytest.mark.parametrize(
    ("open_price", "high", "low", "close", "volume"),
    [
        ("0", "102", "99", "101", 1_000),
        ("100", "99", "98", "99", 1_000),
        ("100", "102", "101", "100", 1_000),
        ("100", "102", "99", "103", 1_000),
        ("100", "102", "99", "101", -1),
    ],
)
def test_daily_bar_rejects_invalid_price_or_volume_structure(
    open_price: str,
    high: str,
    low: str,
    close: str,
    volume: int,
) -> None:
    with pytest.raises(InvalidMarketDataError):
        DailyBar(
            session_date=date(2026, 8, 25),
            open=Decimal(open_price),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=volume,
        )


@pytest.mark.parametrize(
    "non_finite_price",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_daily_bar_rejects_non_finite_prices(non_finite_price: Decimal) -> None:
    with pytest.raises(InvalidMarketDataError, match="positive and finite"):
        DailyBar(
            session_date=date(2026, 8, 25),
            open=non_finite_price,
            high=Decimal("102"),
            low=Decimal("99"),
            close=Decimal("101"),
            volume=1_000,
        )


def test_market_snapshot_rejects_duplicate_or_out_of_order_sessions() -> None:
    first = DailyBar(
        session_date=date(2026, 8, 25),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=1_000,
    )
    earlier = DailyBar(
        session_date=date(2026, 8, 24),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=1_000,
    )

    for bars in ((first, first), (first, earlier)):
        with pytest.raises(InvalidMarketDataError):
            MarketSnapshot(
                schema_version="market-snapshot-v1",
                snapshot_id="synthetic-spy-invalid-order",
                symbol="SPY",
                as_of=datetime(2026, 8, 25, 23, 59, tzinfo=UTC),
                currency="USD",
                source=MarketDataSource.SYNTHETIC,
                completed_daily_bars=bars,
                quality_flags=(),
            )


@pytest.mark.parametrize(
    ("schema_version", "snapshot_id", "symbol", "as_of", "currency"),
    [
        ("market-snapshot-v2", "snapshot", "SPY", datetime(2026, 8, 25, tzinfo=UTC), "USD"),
        ("market-snapshot-v1", "", "SPY", datetime(2026, 8, 25, tzinfo=UTC), "USD"),
        ("market-snapshot-v1", "snapshot", "spy", datetime(2026, 8, 25, tzinfo=UTC), "USD"),
        ("market-snapshot-v1", "snapshot", "SPY", datetime(2026, 8, 25), "USD"),
        (
            "market-snapshot-v1",
            "snapshot",
            "SPY",
            datetime(2026, 8, 25, tzinfo=timezone(timedelta(hours=8))),
            "USD",
        ),
        ("market-snapshot-v1", "snapshot", "SPY", datetime(2026, 8, 25, tzinfo=UTC), "EUR"),
    ],
)
def test_market_snapshot_rejects_invalid_identity_or_time_contract(
    schema_version: str,
    snapshot_id: str,
    symbol: str,
    as_of: datetime,
    currency: str,
) -> None:
    with pytest.raises(InvalidMarketDataError):
        MarketSnapshot(
            schema_version=schema_version,
            snapshot_id=snapshot_id,
            symbol=symbol,
            as_of=as_of,
            currency=currency,
            source=MarketDataSource.SYNTHETIC,
            completed_daily_bars=(),
            quality_flags=(),
        )


def test_market_snapshot_rejects_a_bar_after_its_as_of_date() -> None:
    future_bar = DailyBar(
        session_date=date(2026, 8, 26),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=None,
    )

    with pytest.raises(InvalidMarketDataError):
        MarketSnapshot(
            schema_version="market-snapshot-v1",
            snapshot_id="synthetic-spy-future-bar",
            symbol="SPY",
            as_of=datetime(2026, 8, 25, 23, 59, tzinfo=UTC),
            currency="USD",
            source=MarketDataSource.SYNTHETIC,
            completed_daily_bars=(future_bar,),
            quality_flags=(),
        )


def test_market_snapshot_allows_empty_history_for_structured_unavailability() -> None:
    snapshot = MarketSnapshot(
        schema_version="market-snapshot-v1",
        snapshot_id="synthetic-spy-empty",
        symbol="SPY",
        as_of=datetime(2026, 8, 25, tzinfo=UTC),
        currency="USD",
        source=MarketDataSource.SYNTHETIC,
        completed_daily_bars=(),
        quality_flags=(),
    )

    assert snapshot.completed_daily_bars == ()


def test_market_snapshot_rejects_mutable_bar_or_flag_collections() -> None:
    bar = DailyBar(
        session_date=date(2026, 8, 25),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=None,
    )

    with pytest.raises(InvalidMarketDataError):
        MarketSnapshot(
            schema_version="market-snapshot-v1",
            snapshot_id="synthetic-spy-mutable-bars",
            symbol="SPY",
            as_of=datetime(2026, 8, 25, tzinfo=UTC),
            currency="USD",
            source=MarketDataSource.SYNTHETIC,
            completed_daily_bars=[bar],  # type: ignore[arg-type]
            quality_flags=(),
        )
    with pytest.raises(InvalidMarketDataError):
        MarketSnapshot(
            schema_version="market-snapshot-v1",
            snapshot_id="synthetic-spy-mutable-flags",
            symbol="SPY",
            as_of=datetime(2026, 8, 25, tzinfo=UTC),
            currency="USD",
            source=MarketDataSource.SYNTHETIC,
            completed_daily_bars=(bar,),
            quality_flags=[],  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("snapshot_id", ["", "contains space", "control\x1fseparator"])
def test_market_snapshot_rejects_unsafe_snapshot_ids(snapshot_id: str) -> None:
    with pytest.raises(InvalidMarketDataError, match="snapshot_id"):
        MarketSnapshot(
            schema_version="market-snapshot-v1",
            snapshot_id=snapshot_id,
            symbol="SPY",
            as_of=datetime(2026, 8, 25, tzinfo=UTC),
            currency="USD",
            source=MarketDataSource.SYNTHETIC,
            completed_daily_bars=(),
            quality_flags=(),
        )
