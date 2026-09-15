"""Bounded synchronous orchestration over injected model and tool capabilities."""

from dataclasses import dataclass

from finance_research_agent.agent.model import (
    FinalAnswer,
    ModelRequest,
    ToolObservation,
    _require_content,
)
from finance_research_agent.agent.ports import ModelPort
from finance_research_agent.agent.registry import ToolRegistry
from finance_research_agent.agent.tool import ToolRequest

__all__ = ["AgentRunResult", "AgentRuntime"]


def _require_positive_steps(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Exact final-answer text and the number of model completions that produced it."""

    final_answer: str
    steps: int

    def __post_init__(self) -> None:
        _require_content(self.final_answer)
        _require_positive_steps(self.steps, "steps")


@dataclass(frozen=True, slots=True)
class AgentRuntime:
    """Own state transitions; the model selects actions and tools execute capabilities.

    Dependencies may be stateful. History and step counters belong to each run;
    reproducible runs require equivalently configured dependency state.
    """

    model: ModelPort
    tools: ToolRegistry

    def __post_init__(self) -> None:
        if not callable(getattr(self.model, "complete", None)):
            raise ValueError("model must expose a callable complete")
        if not isinstance(self.tools, ToolRegistry):
            raise ValueError("tools must be a ToolRegistry")

    def run(self, request: ModelRequest, *, max_steps: int) -> AgentRunResult:
        """Complete at most max_steps model turns, propagating port/lookup failures.

        Each ToolCall is executed once, including on the final permitted turn.
        Successful results append a paired observation to a fresh request. Only
        FinalAnswer returns a result; exhausting the bound raises RuntimeError.
        The bound limits completions, not the duration of an individual call.
        """

        if not isinstance(request, ModelRequest):
            raise ValueError("request must be a ModelRequest")
        _require_positive_steps(max_steps, "max_steps")
        current_request = request
        for step in range(1, max_steps + 1):
            action = self.model.complete(current_request).action
            if isinstance(action, FinalAnswer):
                return AgentRunResult(final_answer=action.content, steps=step)
            tool = self.tools.get(action.name)
            tool_request = ToolRequest(name=action.name, arguments=action.arguments)
            result = tool.execute(tool_request)
            observation = ToolObservation(call=action, result=result)
            current_request = ModelRequest((*current_request.messages, observation))
        raise RuntimeError("agent max_steps exhausted")
