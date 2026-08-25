"""Deterministic five-component market-regime calculation."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from json import dumps
from re import fullmatch

from finance_research_agent.domain.indicators import (
    atr_percent,
    atr_percentile,
    equal_weight_relative_return,
    realized_volatility,
    realized_volatility_percentile,
    relative_return,
    sma,
    sma_slope,
)
from finance_research_agent.domain.market import InvalidMarketDataError, MarketSnapshot
from finance_research_agent.domain.metrics import MetricResult, MetricStatus


class RegimeComponent(StrEnum):
    """Components of the initial deterministic regime model."""

    BROAD_TREND = "broad_trend"
    PARTICIPATION = "participation"
    LEADERSHIP = "leadership"
    VOLATILITY_STRESS = "volatility_stress"
    CREDIT_CROSS_ASSET = "credit_cross_asset"


class RegimeComponentState(StrEnum):
    """Deterministic sign or availability of a regime component."""

    POSITIVE = "positive"
    MIXED = "mixed"
    NEGATIVE = "negative"
    UNAVAILABLE = "unavailable"


class RegimeComponentReason(StrEnum):
    """Reason code explaining a component state."""

    POSITIVE_RULE_MATCHED = "positive_rule_matched"
    NEGATIVE_RULE_MATCHED = "negative_rule_matched"
    MIXED_SIGNALS = "mixed_signals"
    MISSING_REQUIRED_SNAPSHOT = "missing_required_snapshot"
    INSUFFICIENT_HISTORY = "insufficient_history"
    INSUFFICIENT_VALID_SERIES = "insufficient_valid_series"


class Regime(StrEnum):
    """Risk-environment taxonomy for the bounded release."""

    PERMISSIVE = "permissive"
    NEUTRAL = "neutral"
    DEFENSIVE = "defensive"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RegimePolicy:
    """Immutable initial symbols, windows, weights, and thresholds."""

    version: str = "regime-policy-v1"
    broad_symbols: tuple[str, ...] = ("SPY", "QQQ")
    small_cap_symbol: str = "IWM"
    credit_asset_symbol: str = "HYG"
    credit_benchmark_symbol: str = "LQD"
    sector_symbols: tuple[str, ...] = (
        "XLC",
        "XLY",
        "XLP",
        "XLE",
        "XLF",
        "XLV",
        "XLI",
        "XLB",
        "XLRE",
        "XLK",
        "XLU",
    )
    cyclical_symbols: tuple[str, ...] = ("XLY", "XLE", "XLF", "XLI", "XLB", "XLK")
    defensive_symbols: tuple[str, ...] = ("XLP", "XLV", "XLU")
    short_sma_window: int = 50
    long_sma_window: int = 200
    slope_lookback: int = 20
    relative_return_window: int = 20
    atr_window: int = 14
    realized_volatility_window: int = 20
    percentile_history: int = 252
    participation_positive_minimum: int = 7
    participation_negative_maximum: int = 4
    participation_minimum_valid: int = 9
    leadership_vote_threshold: int = 2
    leadership_minimum_valid: int = 2
    volatility_positive_maximum: Decimal = Decimal("60")
    volatility_negative_minimum: Decimal = Decimal("80")
    permissive_threshold: Decimal = Decimal("35")
    defensive_threshold: Decimal = Decimal("-35")
    component_weights: tuple[tuple[RegimeComponent, Decimal], ...] = (
        (RegimeComponent.BROAD_TREND, Decimal("30")),
        (RegimeComponent.PARTICIPATION, Decimal("25")),
        (RegimeComponent.LEADERSHIP, Decimal("20")),
        (RegimeComponent.VOLATILITY_STRESS, Decimal("15")),
        (RegimeComponent.CREDIT_CROSS_ASSET, Decimal("10")),
    )

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("regime policy version must not be empty")
        symbol_groups = (
            self.broad_symbols,
            self.sector_symbols,
            self.cyclical_symbols,
            self.defensive_symbols,
        )
        if any(not isinstance(group, tuple) for group in symbol_groups) or not isinstance(
            self.component_weights, tuple
        ) or any(not isinstance(pair, tuple) for pair in self.component_weights):
            raise ValueError("regime policy collections must be immutable tuples")
        if len(self.broad_symbols) != 2 or len(set(self.broad_symbols)) != 2:
            raise ValueError("regime-policy-v1 requires two unique broad-market symbols")
        if len(self.sector_symbols) != 11 or len(set(self.sector_symbols)) != 11:
            raise ValueError("regime-policy-v1 requires eleven unique sector symbols")
        if (
            not self.cyclical_symbols
            or not self.defensive_symbols
            or len(set(self.cyclical_symbols)) != len(self.cyclical_symbols)
            or len(set(self.defensive_symbols)) != len(self.defensive_symbols)
            or not set(self.cyclical_symbols).issubset(self.sector_symbols)
            or not set(self.defensive_symbols).issubset(self.sector_symbols)
            or set(self.cyclical_symbols) & set(self.defensive_symbols)
        ):
            raise ValueError(
                "leadership baskets must be unique, non-empty, disjoint sector subsets"
            )
        symbols = (
            *self.broad_symbols,
            self.small_cap_symbol,
            self.credit_asset_symbol,
            self.credit_benchmark_symbol,
            *self.sector_symbols,
        )
        if any(
            not isinstance(symbol, str)
            or fullmatch(r"[A-Z][A-Z0-9.-]*", symbol) is None
            or not symbol.isascii()
            for symbol in symbols
        ):
            raise ValueError("regime policy symbols must be normalized uppercase ASCII")
        primary_symbols = (
            *self.broad_symbols,
            self.small_cap_symbol,
            self.credit_asset_symbol,
            self.credit_benchmark_symbol,
        )
        if len(set(primary_symbols)) != len(primary_symbols):
            raise ValueError("regime policy primary symbol roles must be unique")
        if tuple(component for component, _ in self.component_weights) != tuple(RegimeComponent):
            raise ValueError("regime policy must define every component once in canonical order")
        weights = tuple(weight for _, weight in self.component_weights)
        if any(not weight.is_finite() or weight <= 0 for weight in weights):
            raise ValueError("regime component weights must be positive and finite")
        if sum(weights, Decimal(0)) != Decimal(100):
            raise ValueError("regime component weights must total 100")
        windows = (
            self.short_sma_window,
            self.long_sma_window,
            self.slope_lookback,
            self.relative_return_window,
            self.atr_window,
            self.realized_volatility_window,
            self.percentile_history,
        )
        if any(window <= 0 for window in windows):
            raise ValueError("regime policy windows must be positive")
        participation_thresholds = (
            self.participation_positive_minimum,
            self.participation_negative_maximum,
            self.participation_minimum_valid,
        )
        if (
            any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in participation_thresholds
            )
            or not 0
            <= self.participation_negative_maximum
            < self.participation_positive_minimum
            <= 11
            or not 1 <= self.participation_minimum_valid <= 11
        ):
            raise ValueError("invalid participation thresholds")
        if (
            not isinstance(self.leadership_vote_threshold, int)
            or isinstance(self.leadership_vote_threshold, bool)
            or not isinstance(self.leadership_minimum_valid, int)
            or isinstance(self.leadership_minimum_valid, bool)
            or not 1 <= self.leadership_vote_threshold <= 3
            or not 1 <= self.leadership_minimum_valid <= 3
        ):
            raise ValueError("invalid leadership thresholds")
        decimal_thresholds = (
            self.volatility_positive_maximum,
            self.volatility_negative_minimum,
            self.permissive_threshold,
            self.defensive_threshold,
        )
        if any(not value.is_finite() for value in decimal_thresholds):
            raise ValueError("regime policy thresholds must be finite")
        if not (
            Decimal(0)
            <= self.volatility_positive_maximum
            < self.volatility_negative_minimum
            <= Decimal(100)
        ):
            raise ValueError("invalid volatility percentile thresholds")
        if self.defensive_threshold >= self.permissive_threshold:
            raise ValueError("defensive threshold must be below permissive threshold")

    @property
    def required_symbols(self) -> tuple[str, ...]:
        """Return symbols that can affect the v1 regime result."""

        return tuple(
            sorted(
                {
                    *self.broad_symbols,
                    self.small_cap_symbol,
                    self.credit_asset_symbol,
                    self.credit_benchmark_symbol,
                    *self.sector_symbols,
                    *self.cyclical_symbols,
                    *self.defensive_symbols,
                }
            )
        )

    def weight_for(self, component: RegimeComponent) -> Decimal:
        """Return the configured weight for one component."""

        for configured, weight in self.component_weights:
            if configured is component:
                return weight
        raise ValueError(f"missing weight for component {component.value}")


@dataclass(frozen=True, slots=True)
class RegimeComponentResult:
    """One explainable deterministic component result."""

    component: RegimeComponent
    state: RegimeComponentState
    weight: Decimal
    weighted_score: Decimal | None
    metric_ids: tuple[str, ...]
    reason_code: RegimeComponentReason
    quality_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError("component weight must be positive")
        expected = _weighted_score(self.state, self.weight)
        if self.weighted_score != expected:
            raise ValueError("component weighted_score does not match state and weight")
        if self.metric_ids != tuple(sorted(self.metric_ids)):
            raise ValueError("component metric IDs must be sorted")


@dataclass(frozen=True, slots=True)
class RegimeResult:
    """Structured output of the deterministic regime calculation."""

    schema_version: str
    result_id: str
    regime: Regime
    score: Decimal | None
    components: tuple[RegimeComponentResult, ...]
    metrics: tuple[MetricResult, ...]
    critical_stress: bool
    critical_stress_reasons: tuple[str, ...]
    policy_version: str
    formula_version: str
    calculated_at: datetime
    input_snapshot_ids: tuple[str, ...]
    quality_flags: tuple[str, ...]
    unavailable_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "regime-result-v1":
            raise ValueError("unsupported regime result schema version")
        if not self.result_id:
            raise ValueError("result_id must not be empty")
        if self.regime is Regime.UNKNOWN and self.score is not None:
            raise ValueError("UNKNOWN regime must not expose a numeric score")
        if self.regime is not Regime.UNKNOWN and self.score is None:
            raise ValueError("known regimes require a numeric score")
        if tuple(component.component for component in self.components) != tuple(RegimeComponent):
            raise ValueError("regime components must use canonical order")
        if self.metrics != tuple(sorted(self.metrics, key=lambda metric: metric.metric_id)):
            raise ValueError("regime metrics must be sorted by metric ID")
        if self.input_snapshot_ids != tuple(sorted(self.input_snapshot_ids)):
            raise ValueError("regime input snapshot IDs must be sorted")
        if self.calculated_at.tzinfo is None or self.calculated_at.utcoffset() != timedelta(0):
            raise ValueError("calculated_at must be timezone-aware UTC")
        if self.critical_stress != bool(self.critical_stress_reasons):
            raise ValueError("critical stress and reasons must be set together")


def _weighted_score(state: RegimeComponentState, weight: Decimal) -> Decimal | None:
    if state is RegimeComponentState.UNAVAILABLE:
        return None
    if state is RegimeComponentState.POSITIVE:
        return weight
    if state is RegimeComponentState.NEGATIVE:
        return -weight
    return Decimal(0)


def _reason_for_state(state: RegimeComponentState) -> RegimeComponentReason:
    if state is RegimeComponentState.POSITIVE:
        return RegimeComponentReason.POSITIVE_RULE_MATCHED
    if state is RegimeComponentState.NEGATIVE:
        return RegimeComponentReason.NEGATIVE_RULE_MATCHED
    return RegimeComponentReason.MIXED_SIGNALS


def _metric_value(metric: MetricResult) -> Decimal:
    if metric.status is not MetricStatus.AVAILABLE or metric.value is None:
        raise ValueError("available metric value required")
    return metric.value


def _component_result(
    component: RegimeComponent,
    state: RegimeComponentState,
    metrics: Sequence[MetricResult],
    reason_code: RegimeComponentReason,
    policy: RegimePolicy,
) -> RegimeComponentResult:
    weight = policy.weight_for(component)
    return RegimeComponentResult(
        component=component,
        state=state,
        weight=weight,
        weighted_score=_weighted_score(state, weight),
        metric_ids=tuple(sorted(metric.metric_id for metric in metrics)),
        reason_code=reason_code,
        quality_flags=tuple(
            sorted({flag for metric in metrics for flag in metric.quality_flags})
        ),
    )


def classify_score(
    score: Decimal,
    *,
    critical_stress: bool,
    policy: RegimePolicy,
) -> Regime:
    """Classify an available un-renormalized weighted score."""

    if critical_stress or score <= policy.defensive_threshold:
        return Regime.DEFENSIVE
    if score >= policy.permissive_threshold:
        return Regime.PERMISSIVE
    return Regime.NEUTRAL


def _participation_state(
    *,
    valid_series: int,
    above_sma: int,
    above_rising_sma: int,
    policy: RegimePolicy,
) -> RegimeComponentState:
    if valid_series < policy.participation_minimum_valid:
        return RegimeComponentState.UNAVAILABLE
    if above_rising_sma >= policy.participation_positive_minimum:
        return RegimeComponentState.POSITIVE
    if above_sma <= policy.participation_negative_maximum:
        return RegimeComponentState.NEGATIVE
    return RegimeComponentState.MIXED


def _leadership_state(
    observations: Sequence[Decimal | None], policy: RegimePolicy
) -> RegimeComponentState:
    available = tuple(value for value in observations if value is not None)
    if len(available) < policy.leadership_minimum_valid:
        return RegimeComponentState.UNAVAILABLE
    if sum(value > 0 for value in available) >= policy.leadership_vote_threshold:
        return RegimeComponentState.POSITIVE
    if sum(value < 0 for value in available) >= policy.leadership_vote_threshold:
        return RegimeComponentState.NEGATIVE
    return RegimeComponentState.MIXED


def _volatility_state(
    *,
    realized_percentile: Decimal,
    atr_percentile: Decimal,
    policy: RegimePolicy,
) -> RegimeComponentState:
    if (
        realized_percentile <= policy.volatility_positive_maximum
        and atr_percentile <= policy.volatility_positive_maximum
    ):
        return RegimeComponentState.POSITIVE
    if (
        realized_percentile >= policy.volatility_negative_minimum
        or atr_percentile >= policy.volatility_negative_minimum
    ):
        return RegimeComponentState.NEGATIVE
    return RegimeComponentState.MIXED


def _broad_trend(
    snapshots: Mapping[str, MarketSnapshot],
    policy: RegimePolicy,
    cutoff_at: datetime,
) -> tuple[RegimeComponentResult, tuple[MetricResult, ...]]:
    try:
        broad = tuple(snapshots[symbol] for symbol in policy.broad_symbols)
    except KeyError:
        result = _component_result(
            RegimeComponent.BROAD_TREND,
            RegimeComponentState.UNAVAILABLE,
            (),
            RegimeComponentReason.MISSING_REQUIRED_SNAPSHOT,
            policy,
        )
        return result, ()

    metrics: list[MetricResult] = []
    observations: list[tuple[MarketSnapshot, MetricResult, MetricResult, MetricResult]] = []
    for snapshot in broad:
        short_average = sma(snapshot, window=policy.short_sma_window, cutoff_at=cutoff_at)
        long_average = sma(snapshot, window=policy.long_sma_window, cutoff_at=cutoff_at)
        slope = sma_slope(
            snapshot,
            window=policy.short_sma_window,
            lookback=policy.slope_lookback,
            cutoff_at=cutoff_at,
        )
        metrics.extend((short_average, long_average, slope))
        observations.append((snapshot, short_average, long_average, slope))

    if any(
        metric.status is MetricStatus.UNAVAILABLE
        for _, short_average, long_average, slope in observations
        for metric in (short_average, long_average, slope)
    ):
        state = RegimeComponentState.UNAVAILABLE
        reason = RegimeComponentReason.INSUFFICIENT_HISTORY
    else:
        positive = all(
            snapshot.completed_daily_bars[-1].close > _metric_value(short_average)
            and snapshot.completed_daily_bars[-1].close > _metric_value(long_average)
            and _metric_value(slope) > 0
            for snapshot, short_average, long_average, slope in observations
        )
        negative = (
            all(
                snapshot.completed_daily_bars[-1].close < _metric_value(short_average)
                and _metric_value(slope) < 0
                for snapshot, short_average, _, slope in observations
            )
            and any(
                snapshot.completed_daily_bars[-1].close < _metric_value(long_average)
                for snapshot, _, long_average, _ in observations
            )
        )
        state = (
            RegimeComponentState.POSITIVE
            if positive
            else RegimeComponentState.NEGATIVE
            if negative
            else RegimeComponentState.MIXED
        )
        reason = _reason_for_state(state)

    return (
        _component_result(RegimeComponent.BROAD_TREND, state, metrics, reason, policy),
        tuple(metrics),
    )


def _participation(
    snapshots: Mapping[str, MarketSnapshot],
    policy: RegimePolicy,
    cutoff_at: datetime,
) -> tuple[RegimeComponentResult, tuple[MetricResult, ...]]:
    metrics: list[MetricResult] = []
    valid_series = 0
    above_sma = 0
    above_rising_sma = 0
    for symbol in policy.sector_symbols:
        snapshot = snapshots.get(symbol)
        if snapshot is None:
            continue
        average = sma(snapshot, window=policy.short_sma_window, cutoff_at=cutoff_at)
        slope = sma_slope(
            snapshot,
            window=policy.short_sma_window,
            lookback=policy.slope_lookback,
            cutoff_at=cutoff_at,
        )
        metrics.extend((average, slope))
        if average.status is MetricStatus.UNAVAILABLE or slope.status is MetricStatus.UNAVAILABLE:
            continue
        valid_series += 1
        is_above = snapshot.completed_daily_bars[-1].close > _metric_value(average)
        above_sma += is_above
        above_rising_sma += is_above and _metric_value(slope) > 0

    state = _participation_state(
        valid_series=valid_series,
        above_sma=above_sma,
        above_rising_sma=above_rising_sma,
        policy=policy,
    )
    reason = (
        RegimeComponentReason.INSUFFICIENT_VALID_SERIES
        if state is RegimeComponentState.UNAVAILABLE
        else _reason_for_state(state)
    )
    return (
        _component_result(RegimeComponent.PARTICIPATION, state, metrics, reason, policy),
        tuple(metrics),
    )


def _leadership(
    snapshots: Mapping[str, MarketSnapshot],
    policy: RegimePolicy,
    cutoff_at: datetime,
) -> tuple[RegimeComponentResult, tuple[MetricResult, ...]]:
    metrics: list[MetricResult] = []
    observations: list[Decimal | None] = []
    for asset_symbol in (policy.broad_symbols[1], policy.small_cap_symbol):
        asset = snapshots.get(asset_symbol)
        benchmark = snapshots.get(policy.broad_symbols[0])
        if asset is None or benchmark is None:
            observations.append(None)
            continue
        metric = relative_return(
            asset,
            benchmark,
            window=policy.relative_return_window,
            cutoff_at=cutoff_at,
        )
        metrics.append(metric)
        observations.append(
            _metric_value(metric) if metric.status is MetricStatus.AVAILABLE else None
        )

    cyclical = tuple(
        snapshots[symbol] for symbol in policy.cyclical_symbols if symbol in snapshots
    )
    defensive = tuple(
        snapshots[symbol] for symbol in policy.defensive_symbols if symbol in snapshots
    )
    if len(cyclical) == len(policy.cyclical_symbols) and len(defensive) == len(
        policy.defensive_symbols
    ):
        basket = equal_weight_relative_return(
            cyclical,
            defensive,
            window=policy.relative_return_window,
            cutoff_at=cutoff_at,
        )
        metrics.append(basket)
        observations.append(
            _metric_value(basket) if basket.status is MetricStatus.AVAILABLE else None
        )
    else:
        observations.append(None)

    state = _leadership_state(observations, policy)
    reason = (
        RegimeComponentReason.INSUFFICIENT_VALID_SERIES
        if state is RegimeComponentState.UNAVAILABLE
        else _reason_for_state(state)
    )
    return (
        _component_result(RegimeComponent.LEADERSHIP, state, metrics, reason, policy),
        tuple(metrics),
    )


def _volatility_stress(
    snapshots: Mapping[str, MarketSnapshot],
    policy: RegimePolicy,
    cutoff_at: datetime,
) -> tuple[RegimeComponentResult, tuple[MetricResult, ...]]:
    snapshot = snapshots.get(policy.broad_symbols[0])
    if snapshot is None:
        result = _component_result(
            RegimeComponent.VOLATILITY_STRESS,
            RegimeComponentState.UNAVAILABLE,
            (),
            RegimeComponentReason.MISSING_REQUIRED_SNAPSHOT,
            policy,
        )
        return result, ()

    metrics = (
        realized_volatility(
            snapshot,
            window=policy.realized_volatility_window,
            cutoff_at=cutoff_at,
        ),
        atr_percent(snapshot, window=policy.atr_window, cutoff_at=cutoff_at),
        realized_volatility_percentile(
            snapshot,
            window=policy.realized_volatility_window,
            history_window=policy.percentile_history,
            cutoff_at=cutoff_at,
        ),
        atr_percentile(
            snapshot,
            window=policy.atr_window,
            history_window=policy.percentile_history,
            cutoff_at=cutoff_at,
        ),
    )
    if any(metric.status is MetricStatus.UNAVAILABLE for metric in metrics):
        state = RegimeComponentState.UNAVAILABLE
        reason = RegimeComponentReason.INSUFFICIENT_HISTORY
    else:
        state = _volatility_state(
            realized_percentile=_metric_value(metrics[2]),
            atr_percentile=_metric_value(metrics[3]),
            policy=policy,
        )
        reason = _reason_for_state(state)
    return (
        _component_result(RegimeComponent.VOLATILITY_STRESS, state, metrics, reason, policy),
        metrics,
    )


def _credit_cross_asset(
    snapshots: Mapping[str, MarketSnapshot],
    policy: RegimePolicy,
    cutoff_at: datetime,
) -> tuple[RegimeComponentResult, tuple[MetricResult, ...]]:
    asset = snapshots.get(policy.credit_asset_symbol)
    benchmark = snapshots.get(policy.credit_benchmark_symbol)
    if asset is None or benchmark is None:
        result = _component_result(
            RegimeComponent.CREDIT_CROSS_ASSET,
            RegimeComponentState.UNAVAILABLE,
            (),
            RegimeComponentReason.MISSING_REQUIRED_SNAPSHOT,
            policy,
        )
        return result, ()

    metrics = (
        relative_return(
            asset,
            benchmark,
            window=policy.relative_return_window,
            cutoff_at=cutoff_at,
        ),
        sma(asset, window=policy.short_sma_window, cutoff_at=cutoff_at),
        sma_slope(
            asset,
            window=policy.short_sma_window,
            lookback=policy.slope_lookback,
            cutoff_at=cutoff_at,
        ),
    )
    if any(metric.status is MetricStatus.UNAVAILABLE for metric in metrics):
        state = RegimeComponentState.UNAVAILABLE
        reason = RegimeComponentReason.INSUFFICIENT_HISTORY
    else:
        relative_value = _metric_value(metrics[0])
        average_value = _metric_value(metrics[1])
        slope_value = _metric_value(metrics[2])
        latest_close = asset.completed_daily_bars[-1].close
        if relative_value > 0 and latest_close > average_value and slope_value > 0:
            state = RegimeComponentState.POSITIVE
        elif relative_value < 0 and latest_close < average_value and slope_value < 0:
            state = RegimeComponentState.NEGATIVE
        else:
            state = RegimeComponentState.MIXED
        reason = _reason_for_state(state)
    return (
        _component_result(RegimeComponent.CREDIT_CROSS_ASSET, state, metrics, reason, policy),
        metrics,
    )


def _validate_inputs(
    snapshots: Mapping[str, MarketSnapshot], policy: RegimePolicy, cutoff_at: datetime
) -> None:
    if cutoff_at.tzinfo is None or cutoff_at.utcoffset() != timedelta(0):
        raise InvalidMarketDataError("cutoff_at must be timezone-aware UTC")
    required = set(policy.required_symbols)
    required_snapshot_ids: set[str] = set()
    for key, snapshot in snapshots.items():
        if key != key.upper() or key != snapshot.symbol:
            raise InvalidMarketDataError(
                "snapshot mapping keys must be uppercase contained symbols"
            )
        if snapshot.as_of > cutoff_at:
            raise InvalidMarketDataError("snapshot as_of cannot be later than cutoff_at")
        if key in required:
            if snapshot.snapshot_id in required_snapshot_ids:
                raise InvalidMarketDataError("required snapshots must have unique snapshot IDs")
            required_snapshot_ids.add(snapshot.snapshot_id)


def _deduplicate_metrics(metrics: Sequence[MetricResult]) -> tuple[MetricResult, ...]:
    by_id: dict[str, MetricResult] = {}
    for metric in metrics:
        existing = by_id.get(metric.metric_id)
        if existing is not None and existing != metric:
            raise ValueError("metric ID collision between unequal results")
        by_id[metric.metric_id] = metric
    return tuple(by_id[metric_id] for metric_id in sorted(by_id))


def _result_id(
    *,
    regime: Regime,
    score: Decimal | None,
    components: Sequence[RegimeComponentResult],
    metrics: Sequence[MetricResult],
    policy: RegimePolicy,
    calculated_at: datetime,
    input_snapshot_ids: Sequence[str],
    critical_stress: bool,
    critical_stress_reasons: Sequence[str],
    quality_flags: Sequence[str],
    unavailable_reasons: Sequence[str],
) -> str:
    payload = {
        "calculated_at": calculated_at.isoformat(),
        "components": [
            {
                "component": component.component.value,
                "metric_ids": component.metric_ids,
                "quality_flags": component.quality_flags,
                "reason": component.reason_code.value,
                "state": component.state.value,
                "weight": str(component.weight),
                "weighted_score": (
                    str(component.weighted_score)
                    if component.weighted_score is not None
                    else None
                ),
            }
            for component in components
        ],
        "critical_stress": critical_stress,
        "critical_stress_reasons": list(critical_stress_reasons),
        "formula_version": "regime-v1",
        "input_snapshot_ids": list(input_snapshot_ids),
        "metrics": [
            {
                "metric_id": metric.metric_id,
                "name": metric.name.value,
                "unit": metric.unit.value,
                "direction": metric.direction.value,
                "parameters": metric.parameters,
                "period_start": (
                    metric.period_start.isoformat() if metric.period_start is not None else None
                ),
                "period_end": (
                    metric.period_end.isoformat() if metric.period_end is not None else None
                ),
                "formula_version": metric.formula_version,
                "input_snapshot_ids": metric.input_snapshot_ids,
                "calculated_at": metric.calculated_at.isoformat(),
                "status": metric.status.value,
                "unavailable_reason": (
                    metric.unavailable_reason.value
                    if metric.unavailable_reason is not None
                    else None
                ),
                "value": str(metric.value) if metric.value is not None else None,
                "quality_flags": metric.quality_flags,
            }
            for metric in metrics
        ],
        "policy_version": policy.version,
        "quality_flags": list(quality_flags),
        "regime": regime.value,
        "score": str(score) if score is not None else None,
        "unavailable_reasons": list(unavailable_reasons),
    }
    digest = sha256(
        dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"regime-{digest[:24]}"


def calculate_regime(
    snapshots: Mapping[str, MarketSnapshot],
    policy: RegimePolicy,
    cutoff_at: datetime,
) -> RegimeResult:
    """Calculate the deterministic v1 regime from completed market snapshots."""

    _validate_inputs(snapshots, policy, cutoff_at)
    component_calculators = (
        _broad_trend,
        _participation,
        _leadership,
        _volatility_stress,
        _credit_cross_asset,
    )
    components: list[RegimeComponentResult] = []
    all_metrics: list[MetricResult] = []
    for calculator in component_calculators:
        component, metrics = calculator(snapshots, policy, cutoff_at)
        components.append(component)
        all_metrics.extend(metrics)

    component_tuple = tuple(components)
    metrics = _deduplicate_metrics(all_metrics)
    broad_unavailable = components[0].state is RegimeComponentState.UNAVAILABLE
    other_unavailable = sum(
        component.state is RegimeComponentState.UNAVAILABLE for component in components[1:]
    )
    unavailable_reasons = tuple(
        sorted(
            f"{component.component.value}:{component.reason_code.value}"
            for component in components
            if component.state is RegimeComponentState.UNAVAILABLE
        )
    )
    critical_stress = False
    critical_stress_reasons: tuple[str, ...] = ()
    if broad_unavailable or other_unavailable >= 2:
        score = None
        regime = Regime.UNKNOWN
    else:
        score = sum(
            (
                component.weighted_score
                for component in components
                if component.weighted_score is not None
            ),
            Decimal(0),
        )
        regime = classify_score(score, critical_stress=critical_stress, policy=policy)

    required_symbols = set(policy.required_symbols)
    input_snapshot_ids = tuple(
        sorted(
            snapshot.snapshot_id
            for symbol, snapshot in snapshots.items()
            if symbol in required_symbols
        )
    )
    quality_flags = tuple(
        sorted(
            {
                flag
                for symbol, snapshot in snapshots.items()
                if symbol in required_symbols
                for flag in snapshot.quality_flags
            }
        )
    )
    result_id = _result_id(
        regime=regime,
        score=score,
        components=component_tuple,
        metrics=metrics,
        policy=policy,
        calculated_at=cutoff_at,
        input_snapshot_ids=input_snapshot_ids,
        critical_stress=critical_stress,
        critical_stress_reasons=critical_stress_reasons,
        quality_flags=quality_flags,
        unavailable_reasons=unavailable_reasons,
    )
    return RegimeResult(
        schema_version="regime-result-v1",
        result_id=result_id,
        regime=regime,
        score=score,
        components=component_tuple,
        metrics=metrics,
        critical_stress=critical_stress,
        critical_stress_reasons=critical_stress_reasons,
        policy_version=policy.version,
        formula_version="regime-v1",
        calculated_at=cutoff_at,
        input_snapshot_ids=input_snapshot_ids,
        quality_flags=quality_flags,
        unavailable_reasons=unavailable_reasons,
    )
