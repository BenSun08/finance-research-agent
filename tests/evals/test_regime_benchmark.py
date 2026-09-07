from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

import finance_research_agent.evals.regime as eval_module
import finance_research_agent.evals.regime_benchmark as benchmark_module
from finance_research_agent.domain.market import InvalidMarketDataError
from finance_research_agent.domain.regime import Regime
from finance_research_agent.evals import (
    RegimeBenchmark,
    RegimeBenchmarkRun,
    RegimeEvalCase,
    RegimeEvalObservation,
    RegimeEvalReport,
    build_regime_benchmark_v1,
    run_regime_benchmark,
)
from finance_research_agent.market_data.historical import (
    HistoricalBarsFailure,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
)


@pytest.fixture(scope="module")
def benchmark() -> RegimeBenchmark:
    return build_regime_benchmark_v1()


def test_corpus_identity_and_explicit_gold_coverage(benchmark: RegimeBenchmark) -> None:
    assert benchmark.name == "deterministic-regime"
    assert benchmark.version == "v1"
    assert isinstance(benchmark.cases, tuple)
    assert len({case.case_id for case in benchmark.cases}) == len(benchmark.cases) == 4
    assert tuple((case.case_id, case.expected_regime, case.tags) for case in benchmark.cases) == (
        ("risk_on", Regime.PERMISSIVE, ("normal",)),
        ("neutral", Regime.NEUTRAL, ("normal",)),
        ("risk_off", Regime.DEFENSIVE, ("stress",)),
        ("missing_broad_data", Regime.UNKNOWN, ("missing_data",)),
    )
    policy = benchmark.cases[0].policy
    assert all(case.policy == policy for case in benchmark.cases)
    assert policy.version == "regime-policy-v1"
    assert policy.permissive_threshold == Decimal("35")
    assert policy.defensive_threshold == Decimal("-35")


def test_corpus_is_authored_without_evaluating(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_evaluation(*args: object, **kwargs: object) -> None:
        raise AssertionError("building gold cases must not execute the system under test")

    monkeypatch.setattr(benchmark_module, "evaluate_regime_cases", unexpected_evaluation)
    monkeypatch.setattr(eval_module, "evaluate_regime_case", unexpected_evaluation)
    monkeypatch.setattr(eval_module, "run_regime_workflow", unexpected_evaluation)

    benchmark = build_regime_benchmark_v1()

    assert len(benchmark.cases) == 4


def test_corpus_build_is_independent_of_ambient_decimal_context(
    benchmark: RegimeBenchmark,
) -> None:
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        rebuilt = build_regime_benchmark_v1()

    assert rebuilt == benchmark


def test_histories_are_provider_neutral_synthetic_and_point_in_time_safe(
    benchmark: RegimeBenchmark,
) -> None:
    cutoff = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)
    final_session = date(2026, 8, 24)
    for case in benchmark.cases:
        assert case.cutoff_at == cutoff
        assert tuple(outcome.symbol for outcome in case.outcomes) == case.policy.required_symbols
        for outcome in case.outcomes:
            provenance = outcome.provenance
            assert provenance.provider == "synthetic"
            assert "SYNTHETIC" in outcome.quality_flags
            assert provenance.completed_through_session == final_session
            assert provenance.requested_end_at <= provenance.retrieved_at <= cutoff
            assert provenance.evidence_cutoff_at == cutoff
            if isinstance(outcome, HistoricalDailyBars):
                assert len(outcome.observations) == 273
                sessions = tuple(item.bar.session_date for item in outcome.observations)
                assert sessions == tuple(sorted(set(sessions)))
                assert all(session.weekday() < 5 for session in sessions)
                assert sessions[-1] == final_session
                assert all(
                    provenance.requested_start_at
                    <= item.source_timestamp
                    <= provenance.requested_end_at
                    for item in outcome.observations
                )
            else:
                assert isinstance(outcome, HistoricalBarsFailure)
                assert case.case_id == "missing_broad_data"
                assert outcome.symbol == "SPY"
                assert outcome.reason is HistoricalBarsUnavailableReason.NO_DATA
                assert len(outcome.missing_sessions) == 273
                assert outcome.missing_sessions[-1] == final_session


def test_benchmark_replays_deterministically_and_all_gold_cases_pass(
    benchmark: RegimeBenchmark,
) -> None:
    first = run_regime_benchmark(benchmark)
    second = run_regime_benchmark(benchmark)

    assert first == second
    assert first.report.confusion_counts == second.report.confusion_counts
    assert first.report.total == first.report.passed == 4
    assert first.report.failed == 0
    assert first.report.accuracy == 1.0
    assert first.benchmark_name == benchmark.name
    assert first.benchmark_version == benchmark.version
    assert first.policy_version == "regime-policy-v1"


def test_changed_gold_label_fails_instead_of_changing_benchmark_truth(
    benchmark: RegimeBenchmark,
) -> None:
    wrong_case = replace(benchmark.cases[0], expected_regime=Regime.DEFENSIVE)
    changed = replace(benchmark, cases=(wrong_case, *benchmark.cases[1:]))

    run = run_regime_benchmark(changed)

    assert run.report.accuracy == 0.75
    assert run.report.observations[0].expected_regime is Regime.DEFENSIVE
    assert run.report.observations[0].actual_regime is Regime.PERMISSIVE
    assert run.report.observations[0].passed is False
    assert benchmark.cases[0].expected_regime is Regime.PERMISSIVE


def test_run_delegates_once_and_preserves_identity_report_and_existing_policy_version(
    benchmark: RegimeBenchmark, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = replace(benchmark.cases[0].policy, version="test-policy-v2")
    custom = replace(
        benchmark,
        name="test-benchmark",
        version="v2",
        cases=tuple(replace(case, policy=policy) for case in benchmark.cases),
    )
    # The wrapper must attach this exact report, not recalculate or rebuild it.
    report = RegimeEvalReport(
        observations=(RegimeEvalObservation("sentinel", Regime.UNKNOWN, Regime.UNKNOWN),),
        tag_summaries=(),
    )
    calls: list[tuple[RegimeEvalCase, ...]] = []

    def capture_evaluation(cases: tuple[RegimeEvalCase, ...]) -> RegimeEvalReport:
        calls.append(cases)
        return report

    monkeypatch.setattr(benchmark_module, "evaluate_regime_cases", capture_evaluation)

    run = run_regime_benchmark(custom)

    assert len(calls) == 1
    assert calls[0] is custom.cases
    assert run.report is report
    assert run == RegimeBenchmarkRun("test-benchmark", "v2", "test-policy-v2", report)


@pytest.mark.parametrize("field", ["name", "version"])
@pytest.mark.parametrize(
    "value", ["", " ", " Stress ", "stress ", "Stress", "stréss", "v1.0", "risk__on", "risk-"]
)
def test_benchmark_rejects_noncanonical_metadata(
    benchmark: RegimeBenchmark, field: str, value: str
) -> None:
    with pytest.raises(ValueError, match=field):
        replace(benchmark, **{field: value})


def test_benchmark_rejects_empty_cases(benchmark: RegimeBenchmark) -> None:
    with pytest.raises(ValueError, match="empty"):
        replace(benchmark, cases=())


def test_benchmark_rejects_mutable_cases(benchmark: RegimeBenchmark) -> None:
    with pytest.raises(ValueError, match="immutable tuple"):
        replace(benchmark, cases=list(benchmark.cases))  # type: ignore[arg-type]


def test_benchmark_rejects_duplicate_case_ids(benchmark: RegimeBenchmark) -> None:
    with pytest.raises(ValueError, match="risk_on"):
        replace(benchmark, cases=(benchmark.cases[0], benchmark.cases[0]))


@pytest.mark.parametrize("same_version", [True, False])
def test_benchmark_requires_one_complete_policy_not_only_one_version(
    benchmark: RegimeBenchmark, same_version: bool
) -> None:
    policy = benchmark.cases[0].policy
    different_policy = (
        replace(policy, permissive_threshold=Decimal("36"))
        if same_version
        else replace(policy, version="test-policy-v2")
    )
    changed = replace(benchmark.cases[1], policy=different_policy)

    with pytest.raises(ValueError, match="same.*policy"):
        replace(benchmark, cases=(benchmark.cases[0], changed))


def test_benchmark_and_run_are_frozen(benchmark: RegimeBenchmark) -> None:
    report = RegimeEvalReport(
        observations=(RegimeEvalObservation("one", Regime.UNKNOWN, Regime.UNKNOWN),),
        tag_summaries=(),
    )
    run = RegimeBenchmarkRun(benchmark.name, benchmark.version, "regime-policy-v1", report)

    with pytest.raises(FrozenInstanceError):
        benchmark.version = "v2"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        run.policy_version = "other"  # type: ignore[misc]


def test_benchmark_run_propagates_programmer_errors(
    benchmark: RegimeBenchmark, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = RuntimeError("evaluation bug")

    def fail_evaluation(cases: tuple[RegimeEvalCase, ...]) -> RegimeEvalReport:
        raise error

    monkeypatch.setattr(benchmark_module, "evaluate_regime_cases", fail_evaluation)

    with pytest.raises(RuntimeError, match="evaluation bug") as raised:
        run_regime_benchmark(benchmark)

    assert raised.value is error


def test_benchmark_run_propagates_domain_errors(benchmark: RegimeBenchmark) -> None:
    invalid = replace(benchmark.cases[0], cutoff_at=datetime(2026, 8, 25))

    with pytest.raises(InvalidMarketDataError, match="timezone-aware UTC"):
        run_regime_benchmark(replace(benchmark, cases=(invalid,)))
