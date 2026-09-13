"""Deterministic tool dependency substitution for offline runtime tests."""

from finance_research_agent.agent.tool import ToolDefinition, ToolRequest, ToolResult

__all__ = ["FakeToolPort"]


class FakeToolPort:
    """Consume predetermined results without interpreting argument values.

    Each instance owns its call history and result cursor. All valid requests
    are recorded, including exhausted calls; invalid requests leave state
    unchanged. An empty result tuple is valid and starts exhausted.
    """

    def __init__(self, definition: ToolDefinition, results: tuple[ToolResult, ...]) -> None:
        if not isinstance(definition, ToolDefinition):
            raise ValueError("definition must be a ToolDefinition")
        if not isinstance(results, tuple):
            raise ValueError("results must be an immutable tuple")
        if any(not isinstance(result, ToolResult) for result in results):
            raise ValueError("results must contain ToolResult values")
        self._definition = definition
        self._results = results
        self._next_result = 0
        self._requests: list[ToolRequest] = []

    @property
    def definition(self) -> ToolDefinition:
        """Return the fixed definition supplied at construction."""

        return self._definition

    @property
    def requests(self) -> tuple[ToolRequest, ...]:
        """Return an immutable snapshot of received requests in call order."""

        return tuple(self._requests)

    def execute(self, request: ToolRequest) -> ToolResult:
        """Return the next result or raise RuntimeError on exhaustion."""

        if not isinstance(request, ToolRequest):
            raise ValueError("request must be a ToolRequest")
        if request.name != self.definition.name:
            raise ValueError("request name must match tool definition")
        self._requests.append(request)
        if self._next_result >= len(self._results):
            raise RuntimeError("fake tool results exhausted")
        result = self._results[self._next_result]
        self._next_result += 1
        return result
