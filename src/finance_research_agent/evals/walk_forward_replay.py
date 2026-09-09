"""Historical regime evaluation orchestration over preconstructed replay cases.

This is not a trading backtester. Training intervals describe history permitted
for development or configuration; there is no fitting or training stage.
"""

from dataclasses import dataclass

from finance_research_agent.evals.regime import (
    RegimeEvalReport,
    _require_unique_case_ids,
    evaluate_regime_cases,
)
from finance_research_agent.evals.regime_replay import RegimeReplayCase
from finance_research_agent.evals.temporal import WalkForwardWindow, validate_walk_forward_plan

__all__ = ["WalkForwardReplay", "WalkForwardReplayPlan", "evaluate_walk_forward_replay"]


@dataclass(frozen=True, slots=True)
class WalkForwardReplay:
    """Bind ordered replay cases to a window's closed evaluation interval.

    Each decision must satisfy eval_start <= decision_at <= eval_end. Evidence
    validation belongs to RegimeReplayCase; supplied validated values are reused.
    Case IDs must be unique within the window. Invalid bindings raise ValueError.
    """

    window: WalkForwardWindow
    cases: tuple[RegimeReplayCase, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.window, WalkForwardWindow):
            raise ValueError("window must be a WalkForwardWindow")
        if not isinstance(self.cases, tuple):
            raise ValueError("walk-forward replay cases must be an immutable tuple")
        if not self.cases:
            raise ValueError("walk-forward replay cases must not be empty")
        for index, replay in enumerate(self.cases):
            if not isinstance(replay, RegimeReplayCase):
                raise ValueError(f"case at index {index} must be a RegimeReplayCase")
            if not self.window.eval_start <= replay.decision_at <= self.window.eval_end:
                raise ValueError(
                    f"case {replay.case.case_id!r} decision_at must lie within "
                    "the closed evaluation interval [eval_start, eval_end]"
                )
        _require_unique_case_ids(tuple(replay.case.case_id for replay in self.cases))


@dataclass(frozen=True, slots=True)
class WalkForwardReplayPlan:
    """Nonempty ordered replay windows with globally unique evaluation case IDs.

    Temporal validation delegates to validate_walk_forward_plan, including its
    support for expanding and rolling training periods. No inputs are sorted.
    A repeated economic scenario requires a distinct case ID in each window.
    """

    windows: tuple[WalkForwardReplay, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.windows, tuple):
            raise ValueError("walk-forward replay windows must be an immutable tuple")
        for index, replay in enumerate(self.windows):
            if not isinstance(replay, WalkForwardReplay):
                raise ValueError(f"window at index {index} must be a WalkForwardReplay")
        validate_walk_forward_plan(tuple(replay.window for replay in self.windows))
        _require_unique_case_ids(
            tuple(replay.case.case_id for window in self.windows for replay in window.cases)
        )


def evaluate_walk_forward_replay(plan: WalkForwardReplayPlan) -> RegimeEvalReport:
    """Evaluate cases once in window/case order and return the existing report.

    The existing evaluator owns observations, metrics, and error propagation.
    Callers can retain the plan to associate globally unique case IDs with windows.
    """

    if not isinstance(plan, WalkForwardReplayPlan):
        raise ValueError("plan must be a WalkForwardReplayPlan")
    return evaluate_regime_cases(
        tuple(replay.case for window in plan.windows for replay in window.cases)
    )
