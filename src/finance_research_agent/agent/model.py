"""Minimal provider-neutral text contracts for the future agent runtime."""

from dataclasses import dataclass
from typing import Literal

__all__ = ["ModelMessage", "ModelRequest", "ModelResponse"]


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
class ModelResponse:
    """Nonblank response text, preserved exactly without provider metadata."""

    content: str

    def __post_init__(self) -> None:
        _require_content(self.content)
