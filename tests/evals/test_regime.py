import ast
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import finance_research_agent.evals.regime as eval_module
from finance_research_agent.domain.market import DailyBar, InvalidMarketDataError
from finance_research_agent.domain.regime import (
    Regime,
    RegimePolicy,
    RegimeResult,
    calculate_regime,
)
from finance_research_agent.evals import (
    RegimeEvalCase,
    RegimeEvalObservation,
    evaluate_regime_case,
)
from finance_research_agent.market_data.historical import (
    FAILURE_SCHEMA_VERSION,
    BarAdjustment,
    DailyBarObservation,
    HistoricalBarsFailure,
    HistoricalBarsOutcome,
    HistoricalBarsProvenance,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
    MarketDataCoverage,
    MarketDataFeed,
)
from tests.support.synthetic_market import CUTOFF as SYNTHETIC_CUTOFF
from tests.support.synthetic_market import make_regime_case

CUTOFF = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)
SESSION = date(2026, 8, 24)


@pytest.fixture
def missing_data_case() -> RegimeEvalCase:
    provenance = HistoricalBarsProvenance(
        provider="test-provider",
        feed=MarketDataFeed.IEX,
        coverage=MarketDataCoverage.SINGLE_EXCHANGE,
        adjustment=BarAdjustment.SPLIT,
        requested_start_at=datetime(2026, 8, 24, 4, tzinfo=UTC),
        requested_end_at=datetime(2026, 8, 25, 3, 59, 59, tzinfo=UTC),
        retrieved_at=datetime(2026, 8, 25, 12, 40, tzinfo=UTC),
        evidence_cutoff_at=CUTOFF,
        completed_through_session=SESSION,
        adapter_version="test-adapter-v1",
    )
    history = HistoricalDailyBars.create(
        symbol="SPY",
        observations=(
            DailyBarObservation(
                source_timestamp=datetime(2026, 8, 24, 4, tzinfo=UTC),
                bar=DailyBar(
                    session_date=SESSION,
                    open=Decimal("100"),
                    high=Decimal("102"),
                    low=Decimal("99"),
                    close=Decimal("101"),
                    volume=1_000_000,
                ),
            ),
        ),
        provenance=provenance,
        quality_flags=("TEST_DATA",),
    )
    failure = HistoricalBarsFailure(
        schema_version=FAILURE_SCHEMA_VERSION,
        symbol="QQQ",
        reason=HistoricalBarsUnavailableReason.NO_DATA,
        provenance=provenance,
        missing_sessions=(SESSION,),
        quality_flags=("NO_DATA",),
    )
    return RegimeEvalCase(
        case_id="missing-broad-history-v1",
        outcomes=(history, failure),
        policy=RegimePolicy(),
        cutoff_at=CUTOFF,
        expected_regime=Regime.UNKNOWN,
    )


def test_matching_expectation_passes(missing_data_case: RegimeEvalCase) -> None:
    observation = evaluate_regime_case(missing_data_case)

    assert observation == RegimeEvalObservation(
        case_id=missing_data_case.case_id,
        expected_regime=Regime.UNKNOWN,
        actual_regime=Regime.UNKNOWN,
    )
    assert observation.passed is True


def test_wrong_expectation_is_a_failed_observation(missing_data_case: RegimeEvalCase) -> None:
    case = replace(missing_data_case, expected_regime=Regime.PERMISSIVE)

    observation = evaluate_regime_case(case)

    assert observation.case_id == case.case_id
    assert observation.expected_regime is Regime.PERMISSIVE
    assert observation.actual_regime is Regime.UNKNOWN
    assert observation.passed is False


def test_frozen_case_replays_deterministically(missing_data_case: RegimeEvalCase) -> None:
    first = evaluate_regime_case(missing_data_case)
    second = evaluate_regime_case(missing_data_case)

    assert first == second
    assert first.passed is second.passed is True


def test_calls_workflow_once_with_unchanged_inputs_and_observes_its_result(
    missing_data_case: RegimeEvalCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = replace(missing_data_case, expected_regime=Regime.NEUTRAL)
    known_result = calculate_regime(
        make_regime_case("neutral").snapshots, case.policy, SYNTHETIC_CUTOFF
    )
    calls: list[tuple[tuple[HistoricalBarsOutcome, ...], RegimePolicy, datetime]] = []

    def capture_workflow(
        outcomes: tuple[HistoricalBarsOutcome, ...],
        policy: RegimePolicy,
        cutoff_at: datetime,
    ) -> RegimeResult:
        calls.append((outcomes, policy, cutoff_at))
        return known_result

    monkeypatch.setattr(eval_module, "run_regime_workflow", capture_workflow)

    observation = evaluate_regime_case(case)

    assert len(calls) == 1
    outcomes, policy, cutoff_at = calls[0]
    assert outcomes is case.outcomes
    assert policy is case.policy
    assert cutoff_at is case.cutoff_at
    assert observation.actual_regime is known_result.regime is Regime.NEUTRAL
    assert observation.passed is True


def test_programmer_error_propagates(
    missing_data_case: RegimeEvalCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError("workflow bug")

    def fail_workflow(
        outcomes: tuple[HistoricalBarsOutcome, ...],
        policy: RegimePolicy,
        cutoff_at: datetime,
    ) -> RegimeResult:
        raise error

    monkeypatch.setattr(eval_module, "run_regime_workflow", fail_workflow)

    with pytest.raises(RuntimeError, match="workflow bug") as raised:
        evaluate_regime_case(missing_data_case)

    assert raised.value is error


def test_domain_cutoff_error_propagates(missing_data_case: RegimeEvalCase) -> None:
    case = replace(missing_data_case, cutoff_at=datetime(2026, 8, 25))

    with pytest.raises(InvalidMarketDataError, match="timezone-aware UTC"):
        evaluate_regime_case(case)


def test_workflow_duplicate_symbol_error_propagates(missing_data_case: RegimeEvalCase) -> None:
    history = missing_data_case.outcomes[0]
    case = replace(missing_data_case, outcomes=(history, history))

    with pytest.raises(ValueError, match="SPY"):
        evaluate_regime_case(case)


def test_case_is_frozen(missing_data_case: RegimeEvalCase) -> None:
    with pytest.raises(FrozenInstanceError):
        missing_data_case.case_id = "changed"  # type: ignore[misc]


def test_observation_is_frozen(missing_data_case: RegimeEvalCase) -> None:
    observation = evaluate_regime_case(missing_data_case)

    with pytest.raises(FrozenInstanceError):
        observation.actual_regime = Regime.DEFENSIVE  # type: ignore[misc]


@pytest.mark.parametrize("case_id", ["", "   "])
def test_case_requires_nonempty_id(missing_data_case: RegimeEvalCase, case_id: str) -> None:
    with pytest.raises(ValueError, match="case_id"):
        replace(missing_data_case, case_id=case_id)


def test_case_rejects_mutable_outcomes(missing_data_case: RegimeEvalCase) -> None:
    with pytest.raises(ValueError, match="immutable tuple"):
        replace(missing_data_case, outcomes=list(missing_data_case.outcomes))  # type: ignore[arg-type]


def test_eval_package_has_no_adapter_or_alpaca_imports() -> None:
    package_root = Path(eval_module.__file__).resolve().parent

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.add(node.module)
                imported_modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        assert not any(
            module == "alpaca"
            or module.startswith("alpaca.")
            or module == "finance_research_agent.adapters"
            or module.startswith("finance_research_agent.adapters.")
            for module in imported_modules
        ), (path, imported_modules)
