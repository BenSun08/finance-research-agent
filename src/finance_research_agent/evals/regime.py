"""Single-case evaluation of the existing deterministic regime workflow."""

from dataclasses import dataclass
from datetime import datetime

from finance_research_agent.application.regime_workflow import run_regime_workflow
from finance_research_agent.domain.regime import Regime, RegimePolicy
from finance_research_agent.market_data.historical import HistoricalBarsOutcome

__all__ = ["RegimeEvalCase", "RegimeEvalObservation", "evaluate_regime_case"]


@dataclass(frozen=True, slots=True)
class RegimeEvalCase:
    """One frozen scenario with a caller-assigned identity and expected regime."""

    case_id: str
    outcomes: tuple[HistoricalBarsOutcome, ...]
    policy: RegimePolicy
    cutoff_at: datetime
    expected_regime: Regime

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id must be a nonempty string")
        if not isinstance(self.outcomes, tuple):
            raise ValueError("evaluation outcomes must be an immutable tuple")


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
