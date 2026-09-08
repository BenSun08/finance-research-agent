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
    RegimeBenchmarkComparison,
    RegimeBenchmarkRun,
    RegimeEvalCase,
    RegimeEvalObservation,
    RegimeEvalReport,
    build_regime_benchmark_v1,
    compare_regime_benchmark_runs,
    run_regime_benchmark,
)
from finance_research_agent.market_data.historical import (
    HistoricalBarsFailure,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
)

SYSTEM_REVISION = "build_2026_09_08"


@pytest.fixture(scope="module")
def benchmark() -> RegimeBenchmark:
    return build_regime_benchmark_v1()


@pytest.fixture(scope="module")
def benchmark_run(benchmark: RegimeBenchmark) -> RegimeBenchmarkRun:
    return run_regime_benchmark(benchmark, system_revision=SYSTEM_REVISION)


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
    first = run_regime_benchmark(benchmark, system_revision=SYSTEM_REVISION)
    second = run_regime_benchmark(benchmark, system_revision=SYSTEM_REVISION)

    assert first == second
    assert first.report.confusion_counts == second.report.confusion_counts
    assert first.report.total == first.report.passed == 4
    assert first.report.failed == 0
    assert first.report.accuracy == 1.0
    assert first.benchmark_name == benchmark.name
    assert first.benchmark_version == benchmark.version
    assert first.policy_version == "regime-policy-v1"
    assert first.system_revision == SYSTEM_REVISION
    assert compare_regime_benchmark_runs(first, second) == RegimeBenchmarkComparison(
        SYSTEM_REVISION, SYSTEM_REVISION, 0.0
    )


def test_changed_gold_label_fails_instead_of_changing_benchmark_truth(
    benchmark: RegimeBenchmark,
) -> None:
    wrong_case = replace(benchmark.cases[0], expected_regime=Regime.DEFENSIVE)
    changed = replace(benchmark, cases=(wrong_case, *benchmark.cases[1:]))

    run = run_regime_benchmark(changed, system_revision=SYSTEM_REVISION)

    assert run.report.accuracy == 0.75
    assert run.report.observations[0].expected_regime is Regime.DEFENSIVE
    assert run.report.observations[0].actual_regime is Regime.PERMISSIVE
    assert run.report.observations[0].passed is False
    assert benchmark.cases[0].expected_regime is Regime.PERMISSIVE


@pytest.mark.parametrize(
    "revision",
    [
        "19511c124aedfed28526dc4badf33ae18482d9c1",
        "v0.5.0",
        "build_2026_09_08",
        "Release-V2.0_rc1",
        "0",
    ],
)
def test_run_delegates_once_and_preserves_identity_report_and_existing_policy_version(
    benchmark: RegimeBenchmark, monkeypatch: pytest.MonkeyPatch, revision: str
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

    run = run_regime_benchmark(custom, system_revision=revision)

    assert len(calls) == 1
    assert calls[0] is custom.cases
    assert run.report is report
    assert run.system_revision == revision
    assert run == RegimeBenchmarkRun("test-benchmark", "v2", "test-policy-v2", revision, report)


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
    run = RegimeBenchmarkRun(
        benchmark.name, benchmark.version, "regime-policy-v1", SYSTEM_REVISION, report
    )

    with pytest.raises(FrozenInstanceError):
        benchmark.version = "v2"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        run.policy_version = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        run.system_revision = "other"  # type: ignore[misc]


def test_benchmark_run_propagates_programmer_errors(
    benchmark: RegimeBenchmark, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = RuntimeError("evaluation bug")

    def fail_evaluation(cases: tuple[RegimeEvalCase, ...]) -> RegimeEvalReport:
        raise error

    monkeypatch.setattr(benchmark_module, "evaluate_regime_cases", fail_evaluation)

    with pytest.raises(RuntimeError, match="evaluation bug") as raised:
        run_regime_benchmark(benchmark, system_revision=SYSTEM_REVISION)

    assert raised.value is error


def test_benchmark_run_propagates_domain_errors(benchmark: RegimeBenchmark) -> None:
    invalid = replace(benchmark.cases[0], cutoff_at=datetime(2026, 8, 25))

    with pytest.raises(InvalidMarketDataError, match="timezone-aware UTC"):
        run_regime_benchmark(
            replace(benchmark, cases=(invalid,)), system_revision=SYSTEM_REVISION
        )


def test_run_requires_caller_supplied_revision(benchmark: RegimeBenchmark) -> None:
    with pytest.raises(TypeError, match="system_revision"):
        run_regime_benchmark(benchmark)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "revision",
    [
        "",
        " ",
        "\t\n",
        " v1",
        "v1 ",
        "v1\n",
        "build 1",
        "révision",
        "版本1",
        "build/1",
        "build\\1",
        ".v1",
        "-v1",
        "_v1",
        "v1.",
        "v1-",
        "v1_",
        "v1..2",
        "build__1",
        "build--1",
        "v1._2",
        "v1+build",
        None,
        123,
        True,
    ],
)
def test_invalid_revision_is_rejected_before_evaluation_and_by_run_constructor(
    benchmark: RegimeBenchmark,
    benchmark_run: RegimeBenchmarkRun,
    monkeypatch: pytest.MonkeyPatch,
    revision: object,
) -> None:
    def unexpected_evaluation(*args: object, **kwargs: object) -> RegimeEvalReport:
        raise AssertionError("invalid system revision must be rejected before evaluation")

    monkeypatch.setattr(benchmark_module, "evaluate_regime_cases", unexpected_evaluation)

    with pytest.raises(ValueError, match="system_revision"):
        run_regime_benchmark(benchmark, system_revision=revision)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="system_revision"):
        replace(benchmark_run, system_revision=revision)


def test_same_benchmark_with_different_system_revisions_is_comparable(
    benchmark: RegimeBenchmark, benchmark_run: RegimeBenchmarkRun
) -> None:
    candidate = run_regime_benchmark(benchmark, system_revision="v0.5.0")

    assert candidate != benchmark_run
    assert candidate.report == benchmark_run.report
    assert compare_regime_benchmark_runs(benchmark_run, candidate) == RegimeBenchmarkComparison(
        baseline_revision=SYSTEM_REVISION,
        candidate_revision="v0.5.0",
        accuracy_delta=0.0,
    )


@pytest.mark.parametrize(
    ("baseline_has_failure", "candidate_has_failure", "expected_delta"),
    [(True, False, 0.25), (False, True, -0.25), (False, False, 0.0)],
)
def test_accuracy_delta_is_candidate_minus_baseline(
    benchmark_run: RegimeBenchmarkRun,
    baseline_has_failure: bool,
    candidate_has_failure: bool,
    expected_delta: float,
) -> None:
    # Represent a different system's observed output without changing corpus gold labels.
    observations = benchmark_run.report.observations
    failed_report = RegimeEvalReport(
        observations=(
            replace(observations[0], actual_regime=Regime.DEFENSIVE), *observations[1:]
        ),
        tag_summaries=(),
    )
    assert failed_report.accuracy == 0.75
    assert benchmark_run.report.accuracy == 1.0
    baseline = replace(
        benchmark_run,
        system_revision="baseline",
        report=failed_report if baseline_has_failure else benchmark_run.report,
    )
    candidate = replace(
        benchmark_run,
        system_revision="candidate",
        report=failed_report if candidate_has_failure else benchmark_run.report,
    )

    comparison = compare_regime_benchmark_runs(baseline, candidate)

    assert comparison == RegimeBenchmarkComparison("baseline", "candidate", expected_delta)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("benchmark_name", "other-benchmark"),
        ("benchmark_version", "v2"),
        ("policy_version", "regime-policy-v2"),
    ],
)
def test_comparison_rejects_incompatible_identity_before_reading_accuracy(
    benchmark_run: RegimeBenchmarkRun,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    candidate = replace(benchmark_run, system_revision="candidate", **{field: value})

    def unexpected_accuracy(report: RegimeEvalReport) -> float:
        raise AssertionError("incompatible reports must not be compared")

    monkeypatch.setattr(RegimeEvalReport, "accuracy", property(unexpected_accuracy))

    with pytest.raises(ValueError, match=field):
        compare_regime_benchmark_runs(benchmark_run, candidate)


def test_comparison_reads_existing_report_accuracy_without_recomputing_or_evaluating(
    benchmark_run: RegimeBenchmarkRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = replace(
        benchmark_run, system_revision="candidate", report=replace(benchmark_run.report)
    )
    accuracy_reads: list[RegimeEvalReport] = []

    def report_accuracy(report: RegimeEvalReport) -> float:
        accuracy_reads.append(report)
        # Deliberately differ from the observations' all-passing accuracy.
        return 0.875 if report is candidate.report else 0.375

    def unexpected_recalculation(*args: object, **kwargs: object) -> None:
        raise AssertionError("comparison must only read report accuracy")

    monkeypatch.setattr(RegimeEvalReport, "accuracy", property(report_accuracy))
    monkeypatch.setattr(RegimeEvalObservation, "passed", property(unexpected_recalculation))
    for metric in ("total", "passed", "failed", "confusion_counts"):
        monkeypatch.setattr(RegimeEvalReport, metric, property(unexpected_recalculation))
    monkeypatch.setattr(benchmark_module, "evaluate_regime_cases", unexpected_recalculation)
    monkeypatch.setattr(eval_module, "evaluate_regime_case", unexpected_recalculation)
    monkeypatch.setattr(eval_module, "run_regime_workflow", unexpected_recalculation)

    comparison = compare_regime_benchmark_runs(benchmark_run, candidate)

    assert comparison.accuracy_delta == 0.5
    assert len(accuracy_reads) == 2
    assert sum(report is benchmark_run.report for report in accuracy_reads) == 1
    assert sum(report is candidate.report for report in accuracy_reads) == 1


def test_comparison_is_frozen(benchmark_run: RegimeBenchmarkRun) -> None:
    comparison = compare_regime_benchmark_runs(benchmark_run, benchmark_run)

    with pytest.raises(FrozenInstanceError):
        comparison.accuracy_delta = 1.0  # type: ignore[misc]
