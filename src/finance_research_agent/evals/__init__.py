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
from finance_research_agent.evals.regime_regression import (
    RegimeRegressionGateResult,
    RegimeRegressionPolicy,
    evaluate_regime_regression_gate,
)
from finance_research_agent.evals.regime_replay import RegimeReplayCase
from finance_research_agent.evals.temporal import WalkForwardWindow, validate_walk_forward_plan
from finance_research_agent.evals.walk_forward_replay import (
    WalkForwardReplay,
    WalkForwardReplayPlan,
    evaluate_walk_forward_replay,
)

__all__ = [
    "RegimeBenchmark",
    "RegimeBenchmarkComparison",
    "RegimeBenchmarkRun",
    "RegimeEvalCase",
    "RegimeEvalObservation",
    "RegimeEvalReport",
    "RegimeRegressionGateResult",
    "RegimeRegressionPolicy",
    "RegimeReplayCase",
    "TagEvalSummary",
    "WalkForwardReplay",
    "WalkForwardReplayPlan",
    "WalkForwardWindow",
    "build_regime_benchmark_v1",
    "compare_regime_benchmark_runs",
    "evaluate_regime_case",
    "evaluate_regime_cases",
    "evaluate_regime_regression_gate",
    "evaluate_walk_forward_replay",
    "run_regime_benchmark",
    "validate_walk_forward_plan",
]
