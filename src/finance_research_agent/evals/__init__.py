"""Deterministic evaluations over existing research capabilities."""

from finance_research_agent.evals.regime import (
    RegimeEvalCase,
    RegimeEvalObservation,
    RegimeEvalReport,
    TagEvalSummary,
    evaluate_regime_case,
    evaluate_regime_cases,
)
from finance_research_agent.evals.regime_benchmark import (
    RegimeBenchmark,
    RegimeBenchmarkComparison,
    RegimeBenchmarkRun,
    build_regime_benchmark_v1,
    compare_regime_benchmark_runs,
    run_regime_benchmark,
)

__all__ = [
    "RegimeBenchmark",
    "RegimeBenchmarkComparison",
    "RegimeBenchmarkRun",
    "RegimeEvalCase",
    "RegimeEvalObservation",
    "RegimeEvalReport",
    "TagEvalSummary",
    "build_regime_benchmark_v1",
    "compare_regime_benchmark_runs",
    "evaluate_regime_case",
    "evaluate_regime_cases",
    "run_regime_benchmark",
]
