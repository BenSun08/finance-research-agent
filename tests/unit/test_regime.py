from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from finance_research_agent.domain.market import InvalidMarketDataError
from finance_research_agent.domain.metrics import MetricStatus, MetricUnavailableReason
from finance_research_agent.domain.regime import (
    Regime,
    RegimeComponent,
    RegimeComponentReason,
    RegimeComponentState,
    RegimePolicy,
    _leadership_state,
    _participation_state,
    _volatility_state,
    calculate_regime,
    classify_score,
)
from tests.support.synthetic_market import (
    CUTOFF,
    SECTORS,
    compound_closes,
    make_regime_case,
    make_snapshot,
)


@pytest.mark.parametrize("case_id", ["risk-on", "neutral", "risk-off"])
def test_calculate_regime_classifies_clear_synthetic_cases(case_id: str) -> None:
    case = make_regime_case(case_id)

    result = calculate_regime(case.snapshots, RegimePolicy(), CUTOFF)

    assert result.regime is case.expected_regime
    assert result.score == case.expected_score
    assert tuple(component.state for component in result.components) == case.expected_components


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        ("35", Regime.PERMISSIVE),
        ("30", Regime.NEUTRAL),
        ("-30", Regime.NEUTRAL),
        ("-35", Regime.DEFENSIVE),
    ],
)
def test_classification_boundaries_are_inclusive(score: str, expected: Regime) -> None:
    assert classify_score(
        Decimal(score), critical_stress=False, policy=RegimePolicy()
    ) is expected


def test_explicit_critical_stress_forces_defensive_classification() -> None:
    assert classify_score(
        Decimal("100"), critical_stress=True, policy=RegimePolicy()
    ) is Regime.DEFENSIVE


@pytest.mark.parametrize(
    ("valid", "above", "above_rising", "expected"),
    [
        (11, 7, 7, RegimeComponentState.POSITIVE),
        (11, 6, 6, RegimeComponentState.MIXED),
        (11, 4, 4, RegimeComponentState.NEGATIVE),
        (8, 8, 8, RegimeComponentState.UNAVAILABLE),
    ],
)
def test_participation_boundaries(
    valid: int,
    above: int,
    above_rising: int,
    expected: RegimeComponentState,
) -> None:
    assert _participation_state(
        valid_series=valid,
        above_sma=above,
        above_rising_sma=above_rising,
        policy=RegimePolicy(),
    ) is expected


@pytest.mark.parametrize(
    ("observations", "expected"),
    [
        ((Decimal("1"), Decimal("2"), Decimal("-1")), RegimeComponentState.POSITIVE),
        ((Decimal("-1"), Decimal("-2"), Decimal("1")), RegimeComponentState.NEGATIVE),
        ((Decimal("1"), Decimal("-1"), Decimal("0")), RegimeComponentState.MIXED),
        ((Decimal("1"), None, None), RegimeComponentState.UNAVAILABLE),
    ],
)
def test_leadership_vote_boundaries(
    observations: tuple[Decimal | None, ...], expected: RegimeComponentState
) -> None:
    assert _leadership_state(observations, RegimePolicy()) is expected


@pytest.mark.parametrize(
    ("realized", "atr", "expected"),
    [
        ("60", "60", RegimeComponentState.POSITIVE),
        ("80", "50", RegimeComponentState.NEGATIVE),
        ("61", "79", RegimeComponentState.MIXED),
    ],
)
def test_volatility_percentile_boundaries(
    realized: str, atr: str, expected: RegimeComponentState
) -> None:
    assert _volatility_state(
        realized_percentile=Decimal(realized),
        atr_percentile=Decimal(atr),
        policy=RegimePolicy(),
    ) is expected


def test_missing_broad_trend_forces_unknown_with_no_score() -> None:
    case = make_regime_case("risk-on")
    snapshots = dict(case.snapshots)
    snapshots.pop("SPY")

    result = calculate_regime(snapshots, RegimePolicy(), CUTOFF)

    assert result.regime is Regime.UNKNOWN
    assert result.score is None
    broad = result.components[0]
    assert broad.component is RegimeComponent.BROAD_TREND
    assert broad.state is RegimeComponentState.UNAVAILABLE
    assert broad.weighted_score is None
    assert broad.reason_code is RegimeComponentReason.MISSING_REQUIRED_SNAPSHOT
    assert result.unavailable_reasons == (
        "broad_trend:missing_required_snapshot",
        "leadership:insufficient_valid_series",
        "volatility_stress:missing_required_snapshot",
    )


def test_two_other_unavailable_components_force_unknown() -> None:
    case = make_regime_case("risk-on")
    snapshots = {
        symbol: case.snapshots[symbol]
        for symbol in ("SPY", "QQQ", "HYG", "LQD")
    }

    result = calculate_regime(snapshots, RegimePolicy(), CUTOFF)

    assert result.regime is Regime.UNKNOWN
    assert result.score is None
    states = {component.component: component for component in result.components}
    assert states[RegimeComponent.BROAD_TREND].state is RegimeComponentState.POSITIVE
    assert states[RegimeComponent.PARTICIPATION].state is RegimeComponentState.UNAVAILABLE
    assert (
        states[RegimeComponent.PARTICIPATION].reason_code
        is RegimeComponentReason.INSUFFICIENT_VALID_SERIES
    )
    assert states[RegimeComponent.PARTICIPATION].weighted_score is None
    assert states[RegimeComponent.LEADERSHIP].state is RegimeComponentState.UNAVAILABLE
    assert (
        states[RegimeComponent.LEADERSHIP].reason_code
        is RegimeComponentReason.INSUFFICIENT_VALID_SERIES
    )
    assert states[RegimeComponent.LEADERSHIP].weighted_score is None
    assert result.unavailable_reasons == (
        "leadership:insufficient_valid_series",
        "participation:insufficient_valid_series",
    )


def test_one_other_unavailable_component_does_not_force_unknown() -> None:
    case = make_regime_case("risk-on")
    snapshots = dict(case.snapshots)
    for symbol in SECTORS:
        snapshots.pop(symbol)

    result = calculate_regime(snapshots, RegimePolicy(), CUTOFF)

    assert result.regime is Regime.PERMISSIVE
    assert result.score == Decimal("75")
    participation = next(
        component
        for component in result.components
        if component.component is RegimeComponent.PARTICIPATION
    )
    assert participation.state is RegimeComponentState.UNAVAILABLE
    assert participation.weighted_score is None
    assert participation.reason_code is RegimeComponentReason.INSUFFICIENT_VALID_SERIES
    assert result.unavailable_reasons == (
        "participation:insufficient_valid_series",
    )
    assert result.score == sum(
        (
            component.weighted_score
            for component in result.components
            if component.weighted_score is not None
        ),
        Decimal(0),
    )


def test_insufficient_history_produces_structured_unknown_metrics() -> None:
    case = make_regime_case("risk-on")
    snapshots = dict(case.snapshots)
    snapshots["SPY"] = make_snapshot(
        symbol="SPY",
        closes=compound_closes(count=10, daily_return=Decimal("0.001")),
        case_id="short-history",
    )

    result = calculate_regime(snapshots, RegimePolicy(), CUTOFF)

    assert result.regime is Regime.UNKNOWN
    assert result.score is None
    broad = result.components[0]
    assert broad.component is RegimeComponent.BROAD_TREND
    assert broad.state is RegimeComponentState.UNAVAILABLE
    assert broad.weighted_score is None
    assert broad.reason_code is RegimeComponentReason.INSUFFICIENT_HISTORY
    broad_metrics = tuple(
        metric for metric in result.metrics if metric.metric_id in broad.metric_ids
    )
    assert broad_metrics
    short_snapshot_id = snapshots["SPY"].snapshot_id
    affected_metrics = tuple(
        metric for metric in broad_metrics if short_snapshot_id in metric.input_snapshot_ids
    )
    assert affected_metrics
    assert all(metric.status is MetricStatus.UNAVAILABLE for metric in affected_metrics)
    assert all(metric.value is None for metric in affected_metrics)
    assert all(
        metric.unavailable_reason is MetricUnavailableReason.INSUFFICIENT_HISTORY
        for metric in affected_metrics
    )
    assert "broad_trend:insufficient_history" in result.unavailable_reasons


def test_invalid_mapping_or_cutoff_is_rejected() -> None:
    case = make_regime_case("risk-on")
    lowercase_key = dict(case.snapshots)
    lowercase_key["spy"] = lowercase_key.pop("SPY")

    with pytest.raises(InvalidMarketDataError):
        calculate_regime(lowercase_key, RegimePolicy(), CUTOFF)
    with pytest.raises(InvalidMarketDataError):
        calculate_regime(case.snapshots, RegimePolicy(), datetime(2026, 8, 25))


def test_result_is_repeatable_and_independent_of_mapping_order_or_extra_symbols() -> None:
    case = make_regime_case("risk-on")
    forward = dict(case.snapshots)
    reverse = dict(reversed(tuple(case.snapshots.items())))
    reverse["DIA"] = make_snapshot(
        symbol="DIA",
        closes=compound_closes(daily_return=Decimal("0.009")),
        case_id="ignored-extra",
    )

    first = calculate_regime(forward, RegimePolicy(), CUTOFF)
    repeated = calculate_regime(forward, RegimePolicy(), CUTOFF)
    reordered_with_extra = calculate_regime(reverse, RegimePolicy(), CUTOFF)

    assert first == repeated == reordered_with_extra
    assert first.schema_version == "regime-result-v1"
    assert first.policy_version == "regime-policy-v1"
    assert first.formula_version == "regime-v1"
    assert first.critical_stress is False
    assert first.critical_stress_reasons == ()
    assert first.input_snapshot_ids == tuple(sorted(first.input_snapshot_ids))
    assert first.metrics == tuple(sorted(first.metrics, key=lambda metric: metric.metric_id))
    assert tuple(component.component for component in first.components) == tuple(RegimeComponent)


def test_result_id_changes_when_structured_quality_output_changes() -> None:
    case = make_regime_case("risk-on")
    flagged_snapshots = dict(case.snapshots)
    flagged_snapshots["SPY"] = replace(
        flagged_snapshots["SPY"], quality_flags=("SYNTHETIC_WARNING",)
    )

    original = calculate_regime(case.snapshots, RegimePolicy(), CUTOFF)
    flagged = calculate_regime(flagged_snapshots, RegimePolicy(), CUTOFF)

    assert original.quality_flags == ()
    assert flagged.quality_flags == ("SYNTHETIC_WARNING",)
    assert original.result_id != flagged.result_id


def test_regime_result_enforces_structural_output_invariants() -> None:
    result = calculate_regime(make_regime_case("risk-on").snapshots, RegimePolicy(), CUTOFF)

    with pytest.raises(ValueError, match="UNKNOWN regime"):
        replace(result, regime=Regime.UNKNOWN)
    with pytest.raises(ValueError, match="known regimes"):
        replace(result, score=None)
    with pytest.raises(ValueError, match="canonical order"):
        replace(result, components=tuple(reversed(result.components)))
    with pytest.raises(ValueError, match="sorted by metric ID"):
        replace(result, metrics=tuple(reversed(result.metrics)))
    with pytest.raises(ValueError, match="snapshot IDs must be sorted"):
        replace(result, input_snapshot_ids=tuple(reversed(result.input_snapshot_ids)))
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        replace(result, calculated_at=datetime(2026, 8, 25))
    with pytest.raises(ValueError, match="critical stress and reasons"):
        replace(result, critical_stress=True)
    with pytest.raises(ValueError, match="score must equal"):
        replace(result, score=Decimal("99"))
    with pytest.raises(ValueError, match="immutable tuples"):
        replace(result, components=list(result.components))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="immutable tuples"):
        replace(result, quality_flags=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="immutable tuples"):
        replace(result, critical_stress_reasons=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="immutable tuples"):
        replace(result, unavailable_reasons=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="critical stress requires"):
        replace(
            result,
            critical_stress=True,
            critical_stress_reasons=("SYNTHETIC_CRITICAL_STRESS",),
        )

    missing_broad = dict(make_regime_case("risk-on").snapshots)
    missing_broad.pop("SPY")
    unknown = calculate_regime(missing_broad, RegimePolicy(), CUTOFF)
    available_score = sum(
        (
            component.weighted_score
            for component in unknown.components
            if component.weighted_score is not None
        ),
        Decimal(0),
    )
    with pytest.raises(ValueError, match="availability requires"):
        replace(unknown, regime=Regime.NEUTRAL, score=available_score)


def test_regime_component_result_enforces_weighted_score_and_metric_order() -> None:
    component = calculate_regime(
        make_regime_case("risk-on").snapshots, RegimePolicy(), CUTOFF
    ).components[0]

    with pytest.raises(ValueError, match="weighted_score"):
        replace(component, weighted_score=Decimal("0"))
    with pytest.raises(ValueError, match="finite Decimal"):
        replace(component, weighted_score=30)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="metric IDs must be sorted"):
        replace(component, metric_ids=tuple(reversed(component.metric_ids)))
    with pytest.raises(ValueError, match="immutable tuples"):
        replace(component, quality_flags=[])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"broad_symbols": ("SPY",)},
        {"broad_symbols": ("SPY", "SPY")},
        {"broad_symbols": ["SPY", "QQQ"]},
        {"sector_symbols": ("XLC",) * 11},
        {"cyclical_symbols": ("XLY", "NOT_A_SECTOR")},
        {"defensive_symbols": ()},
        {"volatility_positive_maximum": Decimal("NaN")},
        {"permissive_threshold": Decimal("Infinity")},
    ],
)
def test_regime_policy_rejects_malformed_symbols_and_non_finite_thresholds(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        RegimePolicy(**overrides)  # type: ignore[arg-type]


def test_regime_policy_rejects_nested_mutable_component_weights() -> None:
    mutable_pairs = tuple(
        [component, weight] for component, weight in RegimePolicy().component_weights
    )

    with pytest.raises(ValueError, match="immutable tuples"):
        RegimePolicy(component_weights=mutable_pairs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "window_field",
    [
        "short_sma_window",
        "long_sma_window",
        "slope_lookback",
        "relative_return_window",
        "atr_window",
        "realized_volatility_window",
        "percentile_history",
    ],
)
@pytest.mark.parametrize("invalid_window", [True, False, 1.5])
def test_regime_policy_rejects_non_integer_windows(
    window_field: str, invalid_window: object
) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        RegimePolicy(**{window_field: invalid_window})  # type: ignore[arg-type]
