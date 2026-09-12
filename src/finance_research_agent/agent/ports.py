"""Provider-neutral outbound model capability for the future agent runtime."""

from typing import Protocol

from finance_research_agent.agent.model import ModelRequest, ModelResponse

__all__ = ["ModelPort"]


class ModelPort(Protocol):
    """Complete one explicit text request through an injected implementation."""

    def complete(self, request: ModelRequest) -> ModelResponse: ...
