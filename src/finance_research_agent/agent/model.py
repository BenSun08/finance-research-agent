"""Provider-neutral model messages and assistant actions; no action execution."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from finance_research_agent.agent.tool import ToolArgumentValue, _copy_arguments, _require_name

__all__ = [
    "AssistantAction",
    "FinalAnswer",
    "ModelMessage",
    "ModelRequest",
    "ModelResponse",
    "ToolCall",
]


def _require_content(content: str) -> None:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a nonempty string with non-whitespace text")


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """One supported role and nonblank text, preserved exactly as supplied."""

    role: Literal["system", "user", "assistant"]
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or self.role not in ("system", "user", "assistant"):
            raise ValueError("role must be system, user, or assistant")
        _require_content(self.content)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """A nonempty immutable message tuple in caller order, with no added prompt."""

    messages: tuple[ModelMessage, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.messages, tuple):
            raise ValueError("messages must be an immutable tuple")
        if not self.messages:
            raise ValueError("messages must not be empty")
        if any(not isinstance(message, ModelMessage) for message in self.messages):
            raise ValueError("messages must contain ModelMessage values")


@dataclass(frozen=True, slots=True)
class FinalAnswer:
    """Nonblank final-answer text, preserved exactly without provider metadata."""

    content: str

    def __post_init__(self) -> None:
        _require_content(self.content)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Model intent to request a capability, without lookup or execution.

    Name and copied scalar arguments follow ToolRequest validation. A future
    runtime translates this intent into ToolRequest execution intent; neither
    contract establishes capability availability or authorization.
    """

    name: str
    arguments: Mapping[str, ToolArgumentValue]

    def __post_init__(self) -> None:
        _require_name(self.name)
        object.__setattr__(self, "arguments", _copy_arguments(self.arguments))


type AssistantAction = FinalAnswer | ToolCall


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Exactly one supported assistant action, without provider metadata."""

    action: AssistantAction

    def __post_init__(self) -> None:
        if not isinstance(self.action, (FinalAnswer, ToolCall)):
            raise ValueError("action must be a FinalAnswer or ToolCall")
