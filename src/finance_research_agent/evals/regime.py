"""Deterministic case evaluation and aggregation over the regime workflow."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from finance_research_agent.application.regime_workflow import run_regime_workflow
from finance_research_agent.domain.regime import Regime, RegimePolicy
from finance_research_agent.market_data.historical import HistoricalBarsOutcome

__all__ = [
    "RegimeEvalCase",
    "RegimeEvalObservation",
    "RegimeEvalReport",
    "TagEvalSummary",
    "evaluate_regime_case",
    "evaluate_regime_cases",
]


def _require_tag(tag: str) -> None:
    if not isinstance(tag, str) or not tag.strip():
        raise ValueError("evaluation tag must be a nonblank string")


def _require_unique_case_ids(case_ids: tuple[str, ...]) -> None:
    seen: set[str] = set()
    for case_id in case_ids:
        if case_id in seen:
            raise ValueError(f"duplicate evaluation case_id {case_id!r}")
        seen.add(case_id)


@dataclass(frozen=True, slots=True)
class RegimeEvalCase:
    """One frozen scenario with a caller-assigned identity and expected regime."""

    case_id: str
    outcomes: tuple[HistoricalBarsOutcome, ...]
    policy: RegimePolicy
    cutoff_at: datetime
    expected_regime: Regime
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id must be a nonempty string")
        if not isinstance(self.outcomes, tuple):
            raise ValueError("evaluation outcomes must be an immutable tuple")
        if not isinstance(self.tags, tuple):
            raise ValueError("evaluation tags must be an immutable tuple")
        for tag in self.tags:
            _require_tag(tag)
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("evaluation tags must be unique")


@dataclass(frozen=True, slots=True)
class RegimeEvalObservation:
    """Expected and observed regimes for one evaluation case."""

    case_id: str
    expected_regime: Regime
    actual_regime: Regime

    @property
    def passed(self) -> bool:
        """Whether the observed regime matches the frozen expectation."""

        return self.expected_regime == self.actual_regime


def evaluate_regime_case(case: RegimeEvalCase) -> RegimeEvalObservation:
    """Run the workflow once; expectation mismatches are observations, not errors."""

    result = run_regime_workflow(case.outcomes, case.policy, case.cutoff_at)
    return RegimeEvalObservation(
        case_id=case.case_id,
        expected_regime=case.expected_regime,
        actual_regime=result.regime,
    )


@dataclass(frozen=True, slots=True)
class TagEvalSummary:
    """Counts for one benchmark tag, independent of domain classification."""

    tag: str
    total: int
    passed: int

    def __post_init__(self) -> None:
        _require_tag(self.tag)
        if not isinstance(self.total, int) or isinstance(self.total, bool) or self.total <= 0:
            raise ValueError("tag total must be a positive integer")
        if (
            not isinstance(self.passed, int)
            or isinstance(self.passed, bool)
            or not 0 <= self.passed <= self.total
        ):
            raise ValueError("tag passed must be an integer between zero and total")

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def accuracy(self) -> float:
        return self.passed / self.total


@dataclass(frozen=True, slots=True)
class RegimeEvalReport:
    """Ordered observations and tag counts for one nonempty evaluation run."""

    observations: tuple[RegimeEvalObservation, ...]
    tag_summaries: tuple[TagEvalSummary, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.observations, tuple) or not isinstance(self.tag_summaries, tuple):
            raise ValueError("report collections must be immutable tuples")
        if not self.observations:
            raise ValueError("evaluation report observations must not be empty")
        _require_unique_case_ids(tuple(observation.case_id for observation in self.observations))

    @property
    def total(self) -> int:
        return len(self.observations)

    @property
    def passed(self) -> int:
        return sum(observation.passed for observation in self.observations)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def accuracy(self) -> float:
        return self.passed / self.total

    @property
    def confusion_counts(self) -> Mapping[tuple[Regime, Regime], int]:
        """Count (expected, actual) pairs, including zeros, in enum declaration order."""

        counts = {(expected, actual): 0 for expected in Regime for actual in Regime}
        for observation in self.observations:
            counts[(observation.expected_regime, observation.actual_regime)] += 1
        return MappingProxyType(counts)


def evaluate_regime_cases(cases: tuple[RegimeEvalCase, ...]) -> RegimeEvalReport:
    """Evaluate each case once in order, propagating errors without a partial report."""

    if not isinstance(cases, tuple):
        raise ValueError("evaluation cases must be an immutable tuple")
    if not cases:
        raise ValueError("evaluation cases must not be empty")
    _require_unique_case_ids(tuple(case.case_id for case in cases))

    observations = tuple(evaluate_regime_case(case) for case in cases)
    tag_counts: dict[str, tuple[int, int]] = {}
    for case, observation in zip(cases, observations, strict=True):
        for tag in case.tags:
            total, passed = tag_counts.get(tag, (0, 0))
            tag_counts[tag] = (total + 1, passed + int(observation.passed))

    return RegimeEvalReport(
        observations=observations,
        tag_summaries=tuple(
            TagEvalSummary(tag=tag, total=total, passed=passed)
            for tag, (total, passed) in sorted(tag_counts.items())
        ),
    )
