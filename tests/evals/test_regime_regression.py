import ast
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from math import inf, nan, nextafter
from pathlib import Path

import pytest

import finance_research_agent.evals as eval_package
import finance_research_agent.evals.regime as eval_module
import finance_research_agent.evals.regime_benchmark as benchmark_module
import finance_research_agent.evals.regime_regression as regression_module
from finance_research_agent.domain.regime import Regime
from finance_research_agent.evals import (
    RegimeBenchmarkComparison,
    RegimeBenchmarkRun,
    RegimeEvalObservation,
    RegimeEvalReport,
    RegimeRegressionGateResult,
    RegimeRegressionPolicy,
    compare_regime_benchmark_runs,
    evaluate_regime_regression_gate,
)

ABSOLUTE_FAILURE = "candidate accuracy below minimum"
REGRESSION_FAILURE = "accuracy regression exceeds maximum"


def _run(passed: int, revision: str, total: int = 100) -> RegimeBenchmarkRun:
    """Represent supplied observations without executing a benchmark or workflow."""

    return RegimeBenchmarkRun(
        benchmark_name="gate-test",
        benchmark_version="v1",
        policy_version="regime-policy-v1",
        system_revision=revision,
        report=RegimeEvalReport(
            observations=tuple(
                RegimeEvalObservation(
                    case_id=f"case_{index}",
                    expected_regime=Regime.NEUTRAL,
                    actual_regime=Regime.NEUTRAL if index < passed else Regime.UNKNOWN,
                )
                for index in range(total)
            ),
            tag_summaries=(),
        ),
    )


@pytest.mark.parametrize(
    ("baseline_passed", "candidate_passed", "minimum", "maximum", "failures"),
    [
        (100, 100, 1.0, 0.0, ()),
        (70, 75, 0.75, 0.02, ()),
        (70, 70, 0.75, 0.02, (ABSOLUTE_FAILURE,)),
        (95, 90, 0.75, 0.02, (REGRESSION_FAILURE,)),
        (95, 70, 0.75, 0.02, (ABSOLUTE_FAILURE, REGRESSION_FAILURE)),
        (70, 74, 0.75, 0.02, (ABSOLUTE_FAILURE,)),
        (80, 79, 0.75, 0.02, ()),
        (80, 80, 0.75, 0.0, ()),
        (80, 79, 0.75, 0.0, (REGRESSION_FAILURE,)),
        (80, 81, 0.75, 0.0, ()),
        (100, 0, 0.0, 1.0, ()),
        (0, 0, 0.0, 0.0, ()),
    ],
    ids=[
        "perfect-candidate", "exact-floor", "below-floor", "excessive-regression",
        "both-failures", "improved-but-below-floor", "small-allowed-regression",
        "zero-tolerance-unchanged", "zero-tolerance-decline", "zero-tolerance-improvement",
        "full-range-regression", "zero-floor",
    ],
)
def test_independent_quality_checks(
    baseline_passed: int,
    candidate_passed: int,
    minimum: float,
    maximum: float,
    failures: tuple[str, ...],
) -> None:
    baseline = _run(baseline_passed, "baseline")
    candidate = _run(candidate_passed, "candidate")
    policy = RegimeRegressionPolicy(minimum, maximum)

    result = evaluate_regime_regression_gate(baseline, candidate, policy)

    assert result.passed is (not failures)
    assert result.failures == failures
    assert result.candidate_accuracy == candidate.report.accuracy
    comparison = compare_regime_benchmark_runs(baseline, candidate)
    assert result.accuracy_delta == comparison.accuracy_delta


def test_exact_allowed_regression_passes() -> None:
    baseline = _run(30, "baseline", total=32)
    candidate = _run(29, "candidate", total=32)

    result = evaluate_regime_regression_gate(
        baseline, candidate, RegimeRegressionPolicy(0.85, 0.03125),
    )

    assert result == RegimeRegressionGateResult(True, 0.90625, -0.03125, ())


def test_decimal_regression_boundary_passes_without_changing_metrics() -> None:
    baseline, candidate = _run(92, "baseline"), _run(90, "candidate")
    accuracy = candidate.report.accuracy
    delta = compare_regime_benchmark_runs(baseline, candidate).accuracy_delta
    result = evaluate_regime_regression_gate(
        baseline, candidate, RegimeRegressionPolicy(0.85, 0.02),
    )

    assert result == RegimeRegressionGateResult(True, 0.9, -0.020000000000000018, ())
    assert result.candidate_accuracy == accuracy
    assert result.accuracy_delta == delta


def test_genuinely_larger_decimal_regression_fails() -> None:
    baseline, candidate = _run(92, "baseline"), _run(89, "candidate")
    comparison = compare_regime_benchmark_runs(baseline, candidate)

    result = evaluate_regime_regression_gate(
        baseline, candidate, RegimeRegressionPolicy(0.85, 0.02),
    )

    assert result == RegimeRegressionGateResult(
        False, candidate.report.accuracy, comparison.accuracy_delta, (REGRESSION_FAILURE,),
    )


@pytest.mark.parametrize(
    ("candidate_accuracy", "delta", "maximum", "failures"),
    [
        (0.75, -0.03125, 0.03125, ()),
        (nextafter(0.75, -inf), -0.03125, 0.03125, (ABSOLUTE_FAILURE,)),
        (nextafter(0.75, inf), -0.03125, 0.03125, ()),
        (0.75, nextafter(-0.03125, -inf), 0.03125, ()),
        (0.75, nextafter(-0.03125, inf), 0.03125, ()),
        (0.75, -0.02 - 5e-13, 0.02, ()),
        (0.75, -0.02 - 2e-12, 0.02, (REGRESSION_FAILURE,)),
        (0.75, -0.5 - 1e-10, 0.5, (REGRESSION_FAILURE,)),
        (0.75, -5e-13, 0.0, (REGRESSION_FAILURE,)),
        (0.75, nextafter(0.0, -inf), 0.0, (REGRESSION_FAILURE,)),
        (0.75, 0.0, 0.0, ()),
    ],
)
def test_explicit_regression_tolerance_with_strict_floor_and_zero_limit(
    candidate_accuracy: float,
    delta: float,
    maximum: float,
    failures: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, candidate = _run(80, "baseline"), _run(75, "candidate")
    comparison = RegimeBenchmarkComparison("baseline", "candidate", delta)
    monkeypatch.setattr(
        regression_module, "compare_regime_benchmark_runs", lambda a, b: comparison,
    )
    monkeypatch.setattr(RegimeEvalReport, "accuracy", property(lambda report: candidate_accuracy))

    result = evaluate_regime_regression_gate(
        baseline, candidate, RegimeRegressionPolicy(0.75, maximum),
    )

    assert result == RegimeRegressionGateResult(
        not failures, candidate_accuracy, delta, failures,
    )


@pytest.mark.parametrize("field", ["minimum_accuracy", "maximum_accuracy_regression"])
@pytest.mark.parametrize(
    "value", [-0.01, 1.01, -10**1000, 10**1000, nan, inf, -inf, True, False, "0.5", None,
              Decimal("0.5")],
    ids=["below-zero", "above-one", "huge-negative", "huge-positive", "nan", "infinity",
         "negative-infinity", "true", "false", "string", "none", "decimal"],
)
def test_policy_rejects_invalid_thresholds(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        replace(RegimeRegressionPolicy(0.75, 0.02), **{field: value})


@pytest.mark.parametrize("value", [0, 1, 0.0, 1.0, 0.75])
def test_policy_accepts_and_preserves_valid_thresholds(value: float) -> None:
    policy = RegimeRegressionPolicy(value, value)

    assert policy.minimum_accuracy is value
    assert policy.maximum_accuracy_regression is value


@pytest.mark.parametrize(
    ("field", "value"),
    [("benchmark_name", "other-benchmark"), ("benchmark_version", "v2"),
     ("policy_version", "regime-policy-v2")],
)
def test_incompatible_runs_propagate_existing_error_before_accuracy_reads(
    field: str, value: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _run(80, "baseline")
    candidate = replace(_run(90, "candidate"), **{field: value})
    errors: list[ValueError] = []

    def capture_comparison(
        a: RegimeBenchmarkRun, b: RegimeBenchmarkRun,
    ) -> RegimeBenchmarkComparison:
        assert a is baseline and b is candidate
        try:
            return compare_regime_benchmark_runs(a, b)
        except ValueError as error:
            errors.append(error)
            raise

    def unexpected_accuracy(report: RegimeEvalReport) -> float:
        raise AssertionError("compatibility must be checked before metrics")

    monkeypatch.setattr(regression_module, "compare_regime_benchmark_runs", capture_comparison)
    monkeypatch.setattr(RegimeEvalReport, "accuracy", property(unexpected_accuracy))

    with pytest.raises(ValueError, match=f"benchmark runs have different {field}") as raised:
        evaluate_regime_regression_gate(baseline, candidate, RegimeRegressionPolicy(0.75, 0.02))

    assert len(errors) == 1
    assert raised.value is errors[0]


def test_gate_delegates_once_without_rerunning_benchmark_or_recomputing_accuracy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, candidate = _run(100, "baseline"), _run(100, "candidate")
    comparison_calls: list[tuple[RegimeBenchmarkRun, RegimeBenchmarkRun]] = []
    accuracy_reads: list[RegimeEvalReport] = []

    def existing_accuracy(report: RegimeEvalReport) -> float:
        accuracy_reads.append(report)
        # Deliberately differ from the all-passing observations.
        return 0.875 if report is candidate.report else 0.9375

    def capture_comparison(
        a: RegimeBenchmarkRun, b: RegimeBenchmarkRun,
    ) -> RegimeBenchmarkComparison:
        comparison_calls.append((a, b))
        return compare_regime_benchmark_runs(a, b)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("gate must use comparison and report accuracy only")

    monkeypatch.setattr(RegimeEvalReport, "accuracy", property(existing_accuracy))
    monkeypatch.setattr(RegimeEvalObservation, "passed", property(unexpected))
    for metric in ("total", "passed", "failed", "confusion_counts"):
        monkeypatch.setattr(RegimeEvalReport, metric, property(unexpected))
    for module, names in (
        (eval_package, ("run_regime_benchmark", "evaluate_regime_cases", "evaluate_regime_case")),
        (benchmark_module, ("run_regime_benchmark", "evaluate_regime_cases")),
        (eval_module, ("evaluate_regime_cases", "evaluate_regime_case", "run_regime_workflow")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, unexpected)
    monkeypatch.setattr(regression_module, "compare_regime_benchmark_runs", capture_comparison)

    result = evaluate_regime_regression_gate(
        baseline, candidate, RegimeRegressionPolicy(0.85, 0.02),
    )

    assert result == RegimeRegressionGateResult(False, 0.875, -0.0625, (REGRESSION_FAILURE,))
    assert len(comparison_calls) == 1
    assert comparison_calls[0][0] is baseline and comparison_calls[0][1] is candidate
    assert sum(report is baseline.report for report in accuracy_reads) == 1
    assert sum(report is candidate.report for report in accuracy_reads) == 2


def test_gate_uses_comparison_delta_without_recomputing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline, candidate = _run(75, "baseline"), _run(100, "candidate")
    # A sentinel comparison proves that the gate consumes its result verbatim.
    comparison = RegimeBenchmarkComparison("baseline", "candidate", -0.0625)
    calls: list[tuple[RegimeBenchmarkRun, RegimeBenchmarkRun]] = []

    def compare(a: RegimeBenchmarkRun, b: RegimeBenchmarkRun) -> RegimeBenchmarkComparison:
        calls.append((a, b))
        return comparison

    monkeypatch.setattr(regression_module, "compare_regime_benchmark_runs", compare)

    result = evaluate_regime_regression_gate(
        baseline, candidate, RegimeRegressionPolicy(0.85, 0.02),
    )

    assert calls == [(baseline, candidate)]
    assert result == RegimeRegressionGateResult(False, 1.0, -0.0625, (REGRESSION_FAILURE,))


def test_policy_and_result_are_frozen_and_slotted() -> None:
    policy = RegimeRegressionPolicy(0.75, 0.02)
    result = evaluate_regime_regression_gate(_run(95, "baseline"), _run(70, "candidate"), policy)

    for value, names in (
        (policy, ("minimum_accuracy", "maximum_accuracy_regression")),
        (result, ("passed", "candidate_accuracy", "accuracy_delta", "failures")),
    ):
        assert tuple(field.name for field in fields(value)) == names
        assert not hasattr(value, "__dict__")
        for name in names:
            with pytest.raises(FrozenInstanceError):
                setattr(value, name, None)
            with pytest.raises(FrozenInstanceError):
                delattr(value, name)
    with pytest.raises(TypeError):
        result.failures[0] = "changed"  # type: ignore[index]


@pytest.mark.parametrize("failures", [[ABSOLUTE_FAILURE], ([],), (1,), "failure", None])
def test_result_requires_immutable_failure_strings(failures: object) -> None:
    with pytest.raises(ValueError, match="immutable tuple of strings"):
        RegimeRegressionGateResult(False, 0.7, -0.2, failures)  # type: ignore[arg-type]


@pytest.mark.parametrize("candidate_passed", [70, 79, 100])
def test_repeated_evaluation_is_deterministic(candidate_passed: int) -> None:
    baseline, candidate = _run(80, "baseline"), _run(candidate_passed, "candidate")
    policy = RegimeRegressionPolicy(0.75, 0.02)
    first = evaluate_regime_regression_gate(baseline, candidate, policy)

    for _ in range(5):
        assert evaluate_regime_regression_gate(baseline, candidate, policy) == first


def test_gate_has_no_clock_network_git_process_environment_file_or_random_dependency() -> None:
    tree = ast.parse(Path(regression_module.__file__).read_text(encoding="utf-8"))
    allowed_imports = {"dataclasses", "math", "finance_research_agent.evals.regime_benchmark"}
    forbidden_calls = {"open", "print", "input", "exec", "eval", "__import__", "exit", "quit"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed_imports for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed_imports
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"now", "utcnow", "today", "time"}
