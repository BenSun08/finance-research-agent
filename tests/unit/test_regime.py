from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from finance_research_agent.domain.market import InvalidMarketDataError
from finance_research_agent.domain.regime import (
    Regime,
    RegimeComponent,
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
    assert any(reason.startswith("broad_trend:") for reason in result.unavailable_reasons)


def test_two_other_unavailable_components_force_unknown() -> None:
    case = make_regime_case("risk-on")
    snapshots = {
        symbol: case.snapshots[symbol]
        for symbol in ("SPY", "QQQ", "HYG", "LQD")
    }

    result = calculate_regime(snapshots, RegimePolicy(), CUTOFF)

    assert result.regime is Regime.UNKNOWN
    assert result.score is None


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
    assert any(metric.status.value == "unavailable" for metric in result.metrics)


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
