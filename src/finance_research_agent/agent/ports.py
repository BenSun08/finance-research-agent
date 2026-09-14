"""Provider-neutral outbound capabilities for the future agent runtime."""

from typing import Protocol

from finance_research_agent.agent.model import ModelRequest, ModelResponse
from finance_research_agent.agent.tool import ToolDefinition, ToolRequest, ToolResult

__all__ = ["ModelPort", "ToolPort"]


class ModelPort(Protocol):
    """Complete one explicit text request through an injected implementation."""

    def complete(self, request: ModelRequest) -> ModelResponse: ...


class ToolPort(Protocol):
    """Execute an explicit request through a tool with a fixed definition.

    Implementations must reject requests for another tool. This execution
    contract neither selects tools nor grants permission for side effects.
    """

    @property
    def definition(self) -> ToolDefinition: ...

    def execute(self, request: ToolRequest) -> ToolResult: ...
