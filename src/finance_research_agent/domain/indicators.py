"""Pure deterministic indicators over completed daily bars."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from hashlib import sha256
from json import dumps

from finance_research_agent.domain.market import (
    DailyBar,
    InvalidMarketDataError,
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

FORMULA_VERSION = "indicators-v1"


def _direction(value: Decimal) -> MetricDirection:
    if value > 0:
        return MetricDirection.UP
    if value < 0:
        return MetricDirection.DOWN
    return MetricDirection.FLAT


def _require_positive_window(window: int) -> None:
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        raise ValueError("indicator windows must be positive integers")


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("cutoff_at must be timezone-aware UTC")


def _require_snapshots_at_or_before_cutoff(
    snapshots: Sequence[MarketSnapshot], cutoff_at: datetime
) -> None:
    if any(snapshot.as_of > cutoff_at for snapshot in snapshots):
        raise InvalidMarketDataError("snapshot as_of cannot be later than cutoff_at")


def _metric_id(
    *,
    name: MetricName,
    parameters: tuple[tuple[str, str], ...],
    period_start: date | None,
    period_end: date | None,
    input_snapshot_ids: tuple[str, ...],
) -> str:
    payload = {
        "formula_version": FORMULA_VERSION,
        "input_snapshot_ids": input_snapshot_ids,
        "name": name.value,
        "parameters": parameters,
        "period_end": period_end.isoformat() if period_end is not None else None,
        "period_start": period_start.isoformat() if period_start is not None else None,
    }
    digest = sha256(
        dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"metric-{digest[:24]}"


def _result(
    *,
    name: MetricName,
    status: MetricStatus,
    value: Decimal | None,
    unit: MetricUnit,
    direction: MetricDirection,
    parameters: tuple[tuple[str, str], ...],
    period_start: date | None,
    period_end: date | None,
    input_snapshot_ids: tuple[str, ...],
    calculated_at: datetime,
    unavailable_reason: MetricUnavailableReason | None,
    quality_flags: tuple[str, ...],
) -> MetricResult:
    sorted_parameters = tuple(sorted(parameters))
    sorted_snapshot_ids = tuple(sorted(input_snapshot_ids))
    return MetricResult(
        metric_id=_metric_id(
            name=name,
            parameters=sorted_parameters,
            period_start=period_start,
            period_end=period_end,
            input_snapshot_ids=sorted_snapshot_ids,
        ),
        name=name,
        status=status,
        value=value,
        unit=unit,
        direction=direction,
        parameters=sorted_parameters,
        period_start=period_start,
        period_end=period_end,
        formula_version=FORMULA_VERSION,
        input_snapshot_ids=sorted_snapshot_ids,
        calculated_at=calculated_at,
        unavailable_reason=unavailable_reason,
        quality_flags=tuple(sorted(set(quality_flags))),
    )


def _unavailable(
    *,
    name: MetricName,
    unit: MetricUnit,
    parameters: tuple[tuple[str, str], ...],
    snapshots: tuple[MarketSnapshot, ...],
    cutoff_at: datetime,
    reason: MetricUnavailableReason,
) -> MetricResult:
    return _result(
        name=name,
        status=MetricStatus.UNAVAILABLE,
        value=None,
        unit=unit,
        direction=MetricDirection.NOT_APPLICABLE,
        parameters=parameters,
        period_start=None,
        period_end=None,
        input_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in snapshots),
        calculated_at=cutoff_at,
        unavailable_reason=reason,
        quality_flags=tuple(flag for snapshot in snapshots for flag in snapshot.quality_flags),
    )


def _atr_percent_value(bars: Sequence[DailyBar], window: int) -> Decimal:
    true_ranges = tuple(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(bars, bars[1:])
    )
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        average_true_range = sum(true_ranges[-window:], Decimal(0)) / Decimal(window)
        return average_true_range / bars[-1].close


def _realized_volatility_value(bars: Sequence[DailyBar], window: int) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        returns = tuple(
            current.close / previous.close - Decimal(1)
            for previous, current in zip(bars, bars[1:])
        )[-window:]
        mean = sum(returns, Decimal(0)) / Decimal(window)
        variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / Decimal(
            window
        )
        return variance.sqrt() * Decimal(252).sqrt()


def sma(snapshot: MarketSnapshot, *, window: int, cutoff_at: datetime) -> MetricResult:
    """Return the arithmetic mean of the final completed closes."""

    _require_positive_window(window)
    _require_utc(cutoff_at)
    _require_snapshots_at_or_before_cutoff((snapshot,), cutoff_at)
    if len(snapshot.completed_daily_bars) < window:
        return _unavailable(
            name=MetricName.SMA,
            unit=MetricUnit.PRICE,
            parameters=(("window", str(window)),),
            snapshots=(snapshot,),
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.INSUFFICIENT_HISTORY,
        )
    bars = snapshot.completed_daily_bars[-window:]
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        value = sum((bar.close for bar in bars), start=Decimal(0)) / Decimal(window)
    return _result(
        name=MetricName.SMA,
        status=MetricStatus.AVAILABLE,
        value=value,
        unit=MetricUnit.PRICE,
        direction=MetricDirection.NOT_APPLICABLE,
        parameters=(("window", str(window)),),
        period_start=bars[0].session_date,
        period_end=bars[-1].session_date,
        input_snapshot_ids=(snapshot.snapshot_id,),
        calculated_at=cutoff_at,
        unavailable_reason=None,
        quality_flags=snapshot.quality_flags,
    )


def sma_slope(
    snapshot: MarketSnapshot,
    *,
    window: int,
    lookback: int,
    cutoff_at: datetime,
) -> MetricResult:
    """Return current SMA minus the SMA ending ``lookback`` sessions earlier."""

    _require_positive_window(window)
    _require_positive_window(lookback)
    _require_utc(cutoff_at)
    _require_snapshots_at_or_before_cutoff((snapshot,), cutoff_at)
    parameters = (("lookback", str(lookback)), ("window", str(window)))
    required = window + lookback
    if len(snapshot.completed_daily_bars) < required:
        return _unavailable(
            name=MetricName.SMA_SLOPE,
            unit=MetricUnit.PRICE,
            parameters=parameters,
            snapshots=(snapshot,),
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.INSUFFICIENT_HISTORY,
        )

    bars = snapshot.completed_daily_bars
    previous = bars[-required:-lookback]
    current = bars[-window:]
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        previous_average = sum((bar.close for bar in previous), Decimal(0)) / Decimal(window)
        current_average = sum((bar.close for bar in current), Decimal(0)) / Decimal(window)
        value = current_average - previous_average
    return _result(
        name=MetricName.SMA_SLOPE,
        status=MetricStatus.AVAILABLE,
        value=value,
        unit=MetricUnit.PRICE,
        direction=_direction(value),
        parameters=parameters,
        period_start=previous[0].session_date,
        period_end=current[-1].session_date,
        input_snapshot_ids=(snapshot.snapshot_id,),
        calculated_at=cutoff_at,
        unavailable_reason=None,
        quality_flags=snapshot.quality_flags,
    )


def relative_return(
    asset: MarketSnapshot,
    benchmark: MarketSnapshot,
    *,
    window: int,
    cutoff_at: datetime,
) -> MetricResult:
    """Return asset simple return minus benchmark simple return."""

    _require_positive_window(window)
    _require_utc(cutoff_at)
    parameters = (
        ("asset_symbol", asset.symbol),
        ("benchmark_symbol", benchmark.symbol),
        ("window", str(window)),
    )
    required = window + 1
    snapshots = (asset, benchmark)
    _require_snapshots_at_or_before_cutoff(snapshots, cutoff_at)
    if any(len(snapshot.completed_daily_bars) < required for snapshot in snapshots):
        return _unavailable(
            name=MetricName.RELATIVE_RETURN,
            unit=MetricUnit.DECIMAL,
            parameters=parameters,
            snapshots=snapshots,
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.INSUFFICIENT_HISTORY,
        )
    asset_bars = asset.completed_daily_bars[-(window + 1) :]
    benchmark_bars = benchmark.completed_daily_bars[-(window + 1) :]
    if tuple(bar.session_date for bar in asset_bars) != tuple(
        bar.session_date for bar in benchmark_bars
    ):
        return _unavailable(
            name=MetricName.RELATIVE_RETURN,
            unit=MetricUnit.DECIMAL,
            parameters=parameters,
            snapshots=snapshots,
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.MISALIGNED_DATES,
        )
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        asset_return = asset_bars[-1].close / asset_bars[0].close - Decimal(1)
        benchmark_return = benchmark_bars[-1].close / benchmark_bars[0].close - Decimal(1)
        value = asset_return - benchmark_return
    return _result(
        name=MetricName.RELATIVE_RETURN,
        status=MetricStatus.AVAILABLE,
        value=value,
        unit=MetricUnit.DECIMAL,
        direction=_direction(value),
        parameters=parameters,
        period_start=asset_bars[0].session_date,
        period_end=asset_bars[-1].session_date,
        input_snapshot_ids=tuple(sorted((asset.snapshot_id, benchmark.snapshot_id))),
        calculated_at=cutoff_at,
        unavailable_reason=None,
        quality_flags=tuple(sorted(set(asset.quality_flags + benchmark.quality_flags))),
    )


def atr_percent(
    snapshot: MarketSnapshot,
    *,
    window: int,
    cutoff_at: datetime,
) -> MetricResult:
    """Return simple average true range divided by the latest completed close."""

    _require_positive_window(window)
    _require_utc(cutoff_at)
    _require_snapshots_at_or_before_cutoff((snapshot,), cutoff_at)
    parameters = (("window", str(window)),)
    required = window + 1
    if len(snapshot.completed_daily_bars) < required:
        return _unavailable(
            name=MetricName.ATR_PERCENT,
            unit=MetricUnit.DECIMAL,
            parameters=parameters,
            snapshots=(snapshot,),
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.INSUFFICIENT_HISTORY,
        )

    bars = snapshot.completed_daily_bars[-required:]
    value = _atr_percent_value(bars, window)
    return _result(
        name=MetricName.ATR_PERCENT,
        status=MetricStatus.AVAILABLE,
        value=value,
        unit=MetricUnit.DECIMAL,
        direction=MetricDirection.NOT_APPLICABLE,
        parameters=parameters,
        period_start=bars[1].session_date,
        period_end=bars[-1].session_date,
        input_snapshot_ids=(snapshot.snapshot_id,),
        calculated_at=cutoff_at,
        unavailable_reason=None,
        quality_flags=snapshot.quality_flags,
    )


def realized_volatility(
    snapshot: MarketSnapshot,
    *,
    window: int,
    cutoff_at: datetime,
) -> MetricResult:
    """Return annualized population volatility of simple completed-bar returns."""

    _require_positive_window(window)
    _require_utc(cutoff_at)
    _require_snapshots_at_or_before_cutoff((snapshot,), cutoff_at)
    parameters = (("annualization", "252"), ("window", str(window)))
    required = window + 1
    if len(snapshot.completed_daily_bars) < required:
        return _unavailable(
            name=MetricName.REALIZED_VOLATILITY,
            unit=MetricUnit.DECIMAL,
            parameters=parameters,
            snapshots=(snapshot,),
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.INSUFFICIENT_HISTORY,
        )

    bars = snapshot.completed_daily_bars[-required:]
    value = _realized_volatility_value(bars, window)
    return _result(
        name=MetricName.REALIZED_VOLATILITY,
        status=MetricStatus.AVAILABLE,
        value=value,
        unit=MetricUnit.DECIMAL,
        direction=MetricDirection.NOT_APPLICABLE,
        parameters=parameters,
        period_start=bars[0].session_date,
        period_end=bars[-1].session_date,
        input_snapshot_ids=(snapshot.snapshot_id,),
        calculated_at=cutoff_at,
        unavailable_reason=None,
        quality_flags=snapshot.quality_flags,
    )


def percentile_rank(current: Decimal, history: Sequence[Decimal]) -> Decimal:
    """Return a deterministic 0-100 percentile using midrank ties."""

    if not history:
        raise ValueError("percentile history must not be empty")
    if not current.is_finite() or any(not value.is_finite() for value in history):
        raise ValueError("percentile values must be finite")
    count_less = sum(value < current for value in history)
    count_equal = sum(value == current for value in history)
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        return (
            Decimal(100)
            * (Decimal(count_less) + Decimal("0.5") * Decimal(count_equal))
            / Decimal(len(history))
        )


def atr_percentile(
    snapshot: MarketSnapshot,
    *,
    window: int,
    history_window: int,
    cutoff_at: datetime,
) -> MetricResult:
    """Rank current ATR percentage against preceding rolling values."""

    _require_positive_window(window)
    _require_positive_window(history_window)
    _require_utc(cutoff_at)
    _require_snapshots_at_or_before_cutoff((snapshot,), cutoff_at)
    parameters = (("history_window", str(history_window)), ("window", str(window)))
    required = window + history_window + 1
    if len(snapshot.completed_daily_bars) < required:
        return _unavailable(
            name=MetricName.ATR_PERCENTILE,
            unit=MetricUnit.PERCENTILE_0_TO_100,
            parameters=parameters,
            snapshots=(snapshot,),
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.INSUFFICIENT_HISTORY,
        )

    bars = snapshot.completed_daily_bars[-required:]
    rolling_values = tuple(
        _atr_percent_value(bars[offset : offset + window + 1], window)
        for offset in range(history_window + 1)
    )
    value = percentile_rank(rolling_values[-1], rolling_values[:-1])
    return _result(
        name=MetricName.ATR_PERCENTILE,
        status=MetricStatus.AVAILABLE,
        value=value,
        unit=MetricUnit.PERCENTILE_0_TO_100,
        direction=MetricDirection.NOT_APPLICABLE,
        parameters=parameters,
        period_start=bars[0].session_date,
        period_end=bars[-1].session_date,
        input_snapshot_ids=(snapshot.snapshot_id,),
        calculated_at=cutoff_at,
        unavailable_reason=None,
        quality_flags=snapshot.quality_flags,
    )


def realized_volatility_percentile(
    snapshot: MarketSnapshot,
    *,
    window: int,
    history_window: int,
    cutoff_at: datetime,
) -> MetricResult:
    """Rank current realized volatility against preceding rolling values."""

    _require_positive_window(window)
    _require_positive_window(history_window)
    _require_utc(cutoff_at)
    _require_snapshots_at_or_before_cutoff((snapshot,), cutoff_at)
    parameters = (
        ("annualization", "252"),
        ("history_window", str(history_window)),
        ("window", str(window)),
    )
    required = window + history_window + 1
    if len(snapshot.completed_daily_bars) < required:
        return _unavailable(
            name=MetricName.REALIZED_VOLATILITY_PERCENTILE,
            unit=MetricUnit.PERCENTILE_0_TO_100,
            parameters=parameters,
            snapshots=(snapshot,),
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.INSUFFICIENT_HISTORY,
        )

    bars = snapshot.completed_daily_bars[-required:]
    rolling_values = tuple(
        _realized_volatility_value(bars[offset : offset + window + 1], window)
        for offset in range(history_window + 1)
    )
    value = percentile_rank(rolling_values[-1], rolling_values[:-1])
    return _result(
        name=MetricName.REALIZED_VOLATILITY_PERCENTILE,
        status=MetricStatus.AVAILABLE,
        value=value,
        unit=MetricUnit.PERCENTILE_0_TO_100,
        direction=MetricDirection.NOT_APPLICABLE,
        parameters=parameters,
        period_start=bars[0].session_date,
        period_end=bars[-1].session_date,
        input_snapshot_ids=(snapshot.snapshot_id,),
        calculated_at=cutoff_at,
        unavailable_reason=None,
        quality_flags=snapshot.quality_flags,
    )


def equal_weight_relative_return(
    assets: Sequence[MarketSnapshot],
    benchmarks: Sequence[MarketSnapshot],
    *,
    window: int,
    cutoff_at: datetime,
) -> MetricResult:
    """Return equal-weight asset-basket return minus benchmark-basket return."""

    _require_positive_window(window)
    _require_utc(cutoff_at)

    def canonicalize(items: Sequence[MarketSnapshot]) -> tuple[MarketSnapshot, ...]:
        snapshots = tuple(items)
        identities = tuple((snapshot.symbol, snapshot.snapshot_id) for snapshot in snapshots)
        if len(set(identities)) != len(identities):
            raise InvalidMarketDataError(
                "equal-weight baskets cannot contain duplicate snapshot identities"
            )
        return tuple(
            sorted(snapshots, key=lambda snapshot: (snapshot.symbol, snapshot.snapshot_id))
        )

    canonical_assets = canonicalize(assets)
    canonical_benchmarks = canonicalize(benchmarks)
    snapshots = canonical_assets + canonical_benchmarks
    _require_snapshots_at_or_before_cutoff(snapshots, cutoff_at)
    parameters = (
        ("asset_symbols", ",".join(snapshot.symbol for snapshot in canonical_assets)),
        ("benchmark_symbols", ",".join(snapshot.symbol for snapshot in canonical_benchmarks)),
        ("window", str(window)),
    )
    if not canonical_assets or not canonical_benchmarks:
        return _unavailable(
            name=MetricName.EQUAL_WEIGHT_RELATIVE_RETURN,
            unit=MetricUnit.DECIMAL,
            parameters=parameters,
            snapshots=snapshots,
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.MISSING_INPUT,
        )

    required = window + 1
    if any(len(snapshot.completed_daily_bars) < required for snapshot in snapshots):
        return _unavailable(
            name=MetricName.EQUAL_WEIGHT_RELATIVE_RETURN,
            unit=MetricUnit.DECIMAL,
            parameters=parameters,
            snapshots=snapshots,
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.INSUFFICIENT_HISTORY,
        )

    dates = tuple(bar.session_date for bar in snapshots[0].completed_daily_bars[-required:])
    if any(
        tuple(bar.session_date for bar in snapshot.completed_daily_bars[-required:]) != dates
        for snapshot in snapshots[1:]
    ):
        return _unavailable(
            name=MetricName.EQUAL_WEIGHT_RELATIVE_RETURN,
            unit=MetricUnit.DECIMAL,
            parameters=parameters,
            snapshots=snapshots,
            cutoff_at=cutoff_at,
            reason=MetricUnavailableReason.MISALIGNED_DATES,
        )

    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN

        def basket_return(items: Sequence[MarketSnapshot]) -> Decimal:
            returns = tuple(
                snapshot.completed_daily_bars[-1].close
                / snapshot.completed_daily_bars[-required].close
                - Decimal(1)
                for snapshot in items
            )
            return sum(returns, Decimal(0)) / Decimal(len(returns))

        value = basket_return(canonical_assets) - basket_return(canonical_benchmarks)
    return _result(
        name=MetricName.EQUAL_WEIGHT_RELATIVE_RETURN,
        status=MetricStatus.AVAILABLE,
        value=value,
        unit=MetricUnit.DECIMAL,
        direction=_direction(value),
        parameters=parameters,
        period_start=dates[0],
        period_end=dates[-1],
        input_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in snapshots),
        calculated_at=cutoff_at,
        unavailable_reason=None,
        quality_flags=tuple(flag for snapshot in snapshots for flag in snapshot.quality_flags),
    )
