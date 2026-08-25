from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from finance_research_agent.domain.indicators import (
    atr_percent,
    atr_percentile,
    equal_weight_relative_return,
    percentile_rank,
    realized_volatility,
    realized_volatility_percentile,
    relative_return,
    sma,
    sma_slope,
)
from finance_research_agent.domain.market import (
    DailyBar,
    InvalidMarketDataError,
    MarketDataSource,
    MarketSnapshot,
)
from finance_research_agent.domain.metrics import (
    MetricDirection,
    MetricName,
    MetricResult,
    MetricStatus,
    MetricUnavailableReason,
    MetricUnit,
)

CUTOFF = datetime(2026, 8, 25, 23, 59, tzinfo=UTC)


def _snapshot(
    symbol: str,
    closes: tuple[str, ...],
    *,
    start: date = date(2026, 1, 1),
) -> MarketSnapshot:
    bars = tuple(
        DailyBar(
            session_date=start + timedelta(days=index),
            open=Decimal(close),
            high=Decimal(close) + Decimal("1"),
            low=Decimal(close) - Decimal("1"),
            close=Decimal(close),
            volume=1_000,
        )
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(
        schema_version="market-snapshot-v1",
        snapshot_id=f"synthetic-{symbol.lower()}-{len(bars)}",
        symbol=symbol,
        as_of=CUTOFF,
        currency="USD",
        source=MarketDataSource.SYNTHETIC,
        completed_daily_bars=bars,
        quality_flags=(),
    )


def _ohlc_snapshot(
    symbol: str,
    *,
    opens: tuple[str, ...],
    highs: tuple[str, ...],
    lows: tuple[str, ...],
    closes: tuple[str, ...],
) -> MarketSnapshot:
    start = date(2026, 1, 1)
    bars = tuple(
        DailyBar(
            session_date=start + timedelta(days=index),
            open=Decimal(open_price),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=1_000,
        )
        for index, (open_price, high, low, close) in enumerate(
            zip(opens, highs, lows, closes, strict=True)
        )
    )
    return MarketSnapshot(
        schema_version="market-snapshot-v1",
        snapshot_id=f"synthetic-{symbol.lower()}-ohlc-{len(bars)}",
        symbol=symbol,
        as_of=CUTOFF,
        currency="USD",
        source=MarketDataSource.SYNTHETIC,
        completed_daily_bars=bars,
        quality_flags=(),
    )


def test_sma_and_relative_return_are_exact_decimal_metrics() -> None:
    average = sma(_snapshot("SPY", ("10", "11", "12")), window=3, cutoff_at=CUTOFF)
    relative = relative_return(
        _snapshot("QQQ", ("100", "110")),
        _snapshot("SPY", ("100", "105")),
        window=1,
        cutoff_at=CUTOFF,
    )

    assert average.status is MetricStatus.AVAILABLE
    assert average.value == Decimal("11")
    assert relative.status is MetricStatus.AVAILABLE
    assert relative.value == Decimal("0.05")


def test_sma_slope_uses_the_prior_completed_window() -> None:
    rising = sma_slope(
        _snapshot("SPY", ("10", "11", "12", "13", "14")),
        window=3,
        lookback=2,
        cutoff_at=CUTOFF,
    )
    flat = sma_slope(
        _snapshot("QQQ", ("10", "10", "10", "10", "10")),
        window=3,
        lookback=2,
        cutoff_at=CUTOFF,
    )

    assert rising.value == Decimal("2")
    assert rising.direction is MetricDirection.UP
    assert flat.value == Decimal("0")
    assert flat.direction is MetricDirection.FLAT


def test_atr_percent_uses_simple_true_range_mean() -> None:
    snapshot = _ohlc_snapshot(
        "SPY",
        opens=("10", "10", "11"),
        highs=("11", "12", "13"),
        lows=("9", "10", "11"),
        closes=("10", "11", "12"),
    )

    result = atr_percent(snapshot, window=2, cutoff_at=CUTOFF)

    assert result.value == Decimal("0.1666666666666666666666666667")
    assert result.unit is MetricUnit.DECIMAL


def test_realized_volatility_uses_population_simple_returns_and_annualizes() -> None:
    result = realized_volatility(
        _snapshot("SPY", ("100", "110", "99")),
        window=2,
        cutoff_at=CUTOFF,
    )

    assert result.value == Decimal("1.587450786638754354300969452")


@pytest.mark.parametrize(
    ("current", "history", "expected"),
    [
        ("4", ("1", "2", "3", "5", "6"), "60"),
        ("5", ("1", "2", "3", "4", "6"), "80"),
        ("2", ("1", "2", "2", "3"), "50"),
    ],
)
def test_percentile_rank_uses_midrank_ties(
    current: str,
    history: tuple[str, ...],
    expected: str,
) -> None:
    assert percentile_rank(
        Decimal(current), tuple(Decimal(value) for value in history)
    ) == Decimal(expected)


def test_percentile_metric_unit_declares_zero_to_one_hundred_scale() -> None:
    result = atr_percentile(
        _snapshot("SPY", ("100", "101", "102", "103")),
        window=1,
        history_window=2,
        cutoff_at=CUTOFF,
    )

    assert result.unit is MetricUnit.PERCENTILE_0_TO_100


def test_public_indicators_reject_snapshots_newer_than_their_cutoff() -> None:
    snapshot = _snapshot(
        "SPY",
        ("100", "101", "102", "103"),
        start=date(2026, 8, 22),
    )
    benchmark = _snapshot(
        "QQQ",
        ("100", "99", "98", "97"),
        start=date(2026, 8, 22),
    )
    earlier_cutoff = CUTOFF - timedelta(days=1)
    calculations = (
        lambda: sma(snapshot, window=2, cutoff_at=earlier_cutoff),
        lambda: sma_slope(snapshot, window=2, lookback=1, cutoff_at=earlier_cutoff),
        lambda: atr_percent(snapshot, window=2, cutoff_at=earlier_cutoff),
        lambda: realized_volatility(snapshot, window=2, cutoff_at=earlier_cutoff),
        lambda: atr_percentile(
            snapshot, window=1, history_window=2, cutoff_at=earlier_cutoff
        ),
        lambda: realized_volatility_percentile(
            snapshot, window=1, history_window=2, cutoff_at=earlier_cutoff
        ),
        lambda: relative_return(snapshot, benchmark, window=2, cutoff_at=earlier_cutoff),
        lambda: equal_weight_relative_return(
            (snapshot,), (benchmark,), window=2, cutoff_at=earlier_cutoff
        ),
    )

    for calculate in calculations:
        with pytest.raises(InvalidMarketDataError, match="later than cutoff_at"):
            calculate()


def test_insufficient_or_misaligned_history_is_structured_unavailability() -> None:
    insufficient = sma(_snapshot("SPY", ("10", "11")), window=3, cutoff_at=CUTOFF)
    misaligned = relative_return(
        _snapshot("QQQ", ("100", "110")),
        _snapshot("SPY", ("100", "105"), start=date(2026, 1, 2)),
        window=1,
        cutoff_at=CUTOFF,
    )

    assert insufficient.status is MetricStatus.UNAVAILABLE
    assert insufficient.value is None
    assert insufficient.unavailable_reason is MetricUnavailableReason.INSUFFICIENT_HISTORY
    assert misaligned.status is MetricStatus.UNAVAILABLE
    assert misaligned.unavailable_reason is MetricUnavailableReason.MISALIGNED_DATES


def test_metric_ids_and_parameter_order_are_repeatable() -> None:
    snapshot = _snapshot("SPY", ("10", "11", "12", "13"))

    first = sma(snapshot, window=2, cutoff_at=CUTOFF)
    repeated = sma(snapshot, window=2, cutoff_at=CUTOFF)
    different_window = sma(snapshot, window=3, cutoff_at=CUTOFF)

    assert first.metric_id == repeated.metric_id
    assert first.metric_id != different_window.metric_id
    assert first.parameters == (("window", "2"),)
    assert first.calculated_at == CUTOFF


def test_relative_return_metric_id_preserves_asset_and_benchmark_roles() -> None:
    asset = _snapshot("QQQ", ("100", "110"))
    benchmark = _snapshot("SPY", ("100", "105"))

    forward = relative_return(asset, benchmark, window=1, cutoff_at=CUTOFF)
    reverse = relative_return(benchmark, asset, window=1, cutoff_at=CUTOFF)

    assert forward.value == -reverse.value  # type: ignore[operator]
    assert forward.metric_id != reverse.metric_id
    assert ("asset_symbol", "QQQ") in forward.parameters
    assert ("benchmark_symbol", "SPY") in forward.parameters


def test_metric_result_enforces_available_and_unavailable_invariants() -> None:
    common = {
        "metric_id": "metric-invalid",
        "name": MetricName.SMA,
        "unit": MetricUnit.PRICE,
        "direction": MetricDirection.NOT_APPLICABLE,
        "parameters": (("window", "2"),),
        "period_start": date(2026, 1, 1),
        "period_end": date(2026, 1, 2),
        "formula_version": "indicators-v1",
        "input_snapshot_ids": ("snapshot",),
        "calculated_at": CUTOFF,
        "quality_flags": (),
    }

    with pytest.raises(ValueError):
        MetricResult(
            **common,
            status=MetricStatus.AVAILABLE,
            value=None,
            unavailable_reason=None,
        )
    with pytest.raises(ValueError):
        MetricResult(
            **common,
            status=MetricStatus.UNAVAILABLE,
            value=Decimal("1"),
            unavailable_reason=MetricUnavailableReason.INSUFFICIENT_HISTORY,
        )
