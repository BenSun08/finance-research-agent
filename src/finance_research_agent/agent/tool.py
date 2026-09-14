"""Minimal immutable execution contracts; no schemas or authorization policy."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from re import fullmatch
from types import MappingProxyType

__all__ = ["ToolArgumentValue", "ToolDefinition", "ToolRequest", "ToolResult"]

type ToolArgumentValue = str | int | float | bool | None


def _require_name(name: str) -> None:
    if not isinstance(name, str) or fullmatch(r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*", name) is None:
        raise ValueError(
            "name must be a canonical lowercase ASCII token "
            "with optional underscore or hyphen separators"
        )


def _copy_arguments(
    arguments: Mapping[str, ToolArgumentValue],
) -> Mapping[str, ToolArgumentValue]:
    """Validate and freeze scalar arguments shared by intent and execution."""

    if not isinstance(arguments, Mapping):
        raise ValueError("arguments must be a mapping")
    copied = dict(arguments)
    for key, value in copied.items():
        if not isinstance(key, str):
            raise ValueError("argument keys must be strings")
        if type(value) not in (str, int, float, bool, type(None)):
            raise ValueError("argument values must be built-in JSON scalars")
        if isinstance(value, float) and not isfinite(value):
            raise ValueError("argument floats must be finite")
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A fixed tool identity and nonblank description, preserved exactly."""

    name: str
    description: str

    def __post_init__(self) -> None:
        _require_name(self.name)
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description must be a nonblank string")


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """Validated runtime execution intent, distinct from model ToolCall intent.

    Only built-in scalar values are accepted, with finite floats. Nested
    containers and arbitrary objects are deferred until an adapter needs them.
    Copying disconnects the request from the caller's mutable mapping.
    Construction validates shape; it does not authorize execution.
    """

    name: str
    arguments: Mapping[str, ToolArgumentValue]

    def __post_init__(self) -> None:
        _require_name(self.name)
        object.__setattr__(self, "arguments", _copy_arguments(self.arguments))


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Nonblank result text preserved exactly, without provider metadata."""

    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content must be a nonblank string")
