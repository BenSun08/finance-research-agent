"""Deterministic release-quality policy over existing benchmark comparison metrics."""

from dataclasses import dataclass
from math import isclose

from finance_research_agent.evals.regime_benchmark import (
    RegimeBenchmarkRun,
    compare_regime_benchmark_runs,
)

__all__ = [
    "RegimeRegressionGateResult",
    "RegimeRegressionPolicy",
    "evaluate_regime_regression_gate",
]

# Accuracy units (1e-10 percentage points): comparison-only subtraction slack.
# No relative tolerance, metric rounding, or slack for a zero regression limit.
_REGRESSION_COMPARISON_ABS_TOLERANCE = 1e-12


@dataclass(frozen=True, slots=True)
class RegimeRegressionPolicy:
    """Absolute candidate accuracy floor and nonnegative allowed degradation.

    Both thresholds must be finite int/float values in [0, 1], excluding bool.
    Values are preserved without conversion or clamping. The accuracy floor is
    strict; positive regression limits allow 1e-12 absolute numerical-comparison
    tolerance. A zero regression limit rejects every negative delta.
    """

    minimum_accuracy: float
    maximum_accuracy_regression: float

    def __post_init__(self) -> None:
        for field_name in ("minimum_accuracy", "maximum_accuracy_regression"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError(f"{field_name} must be a finite number between 0 and 1")


@dataclass(frozen=True, slots=True)
class RegimeRegressionGateResult:
    """Decision and original metrics, with absolute failure before regression failure."""

    passed: bool
    candidate_accuracy: float
    accuracy_delta: float
    failures: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.failures, tuple) or any(
            not isinstance(failure, str) for failure in self.failures
        ):
            raise ValueError("gate failures must be an immutable tuple of strings")


def evaluate_regime_regression_gate(
    baseline: RegimeBenchmarkRun,
    candidate: RegimeBenchmarkRun,
    policy: RegimeRegressionPolicy,
) -> RegimeRegressionGateResult:
    """Require the accuracy floor and the regression limit, allowing boundary equality.

    Compatibility validation and candidate-minus-baseline delta belong to the
    existing comparison; its errors propagate unchanged. Report accuracy is
    read directly without rerunning benchmarks or duplicating metric formulas.
    Positive regression limits allow 1e-12 absolute comparison tolerance with
    no relative tolerance. Zero limits and the accuracy floor remain strict.
    Original metrics are never rounded or changed. No CI or I/O work is done.
    """

    comparison = compare_regime_benchmark_runs(baseline, candidate)
    candidate_accuracy = candidate.report.accuracy
    failures: list[str] = []
    if candidate_accuracy < policy.minimum_accuracy:
        failures.append("candidate accuracy below minimum")
    regression_passed = comparison.accuracy_delta >= -policy.maximum_accuracy_regression or (
        policy.maximum_accuracy_regression > 0.0
        and isclose(
            comparison.accuracy_delta,
            -policy.maximum_accuracy_regression,
            rel_tol=0.0,
            abs_tol=_REGRESSION_COMPARISON_ABS_TOLERANCE,
        )
    )
    if not regression_passed:
        failures.append("accuracy regression exceeds maximum")
    return RegimeRegressionGateResult(
        passed=not failures,
        candidate_accuracy=candidate_accuracy,
        accuracy_delta=comparison.accuracy_delta,
        failures=tuple(failures),
    )
