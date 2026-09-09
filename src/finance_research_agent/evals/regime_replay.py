"""Point-in-time contracts over validated historical regime evaluation evidence."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from finance_research_agent.evals.regime import RegimeEvalCase
from finance_research_agent.market_data.historical import (
    HistoricalBarsFailure,
    HistoricalBarsProvenance,
    HistoricalDailyBars,
)

__all__ = ["RegimeReplayCase"]


@dataclass(frozen=True, slots=True)
class RegimeReplayCase:
    """Bind an evaluation case to an explicit, timezone-aware UTC decision time.

    The evaluator cutoff must equal decision_at, and every outcome's evidence
    cutoff must be no later than decision_at, including failure outcomes.
    Retrieval may follow the historical decision: it records dataset collection,
    not historical availability. Source timestamps and completed sessions remain
    governed by the validated market-data contracts.

    No clock, evaluation, or data retrieval is performed during construction.
    This boundary cannot detect revision leakage without historical publication
    or revision metadata. Evaluate the validated case with evaluate_regime_case.
    """

    case: RegimeEvalCase
    decision_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.case, RegimeEvalCase):
            raise ValueError("case must be a RegimeEvalCase")
        for field_name, value in (
            ("decision_at", self.decision_at),
            ("case.cutoff_at", self.case.cutoff_at),
        ):
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() != timedelta(0)
            ):
                raise ValueError(f"{field_name} must be timezone-aware UTC")
        if self.case.cutoff_at != self.decision_at:
            raise ValueError("case.cutoff_at must equal decision_at")
        for outcome in self.case.outcomes:
            if not isinstance(outcome, (HistoricalDailyBars, HistoricalBarsFailure)):
                raise ValueError("replay outcomes must be HistoricalBarsOutcome values")
            if not isinstance(outcome.provenance, HistoricalBarsProvenance):
                raise ValueError("replay outcome provenance must be HistoricalBarsProvenance")
            if outcome.provenance.evidence_cutoff_at > self.decision_at:
                raise ValueError(
                    f"{outcome.symbol} evidence_cutoff_at must be no later than decision_at"
                )
