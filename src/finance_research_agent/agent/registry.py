"""Deterministic capability discovery and lookup, without tool execution."""

from dataclasses import dataclass, field

from finance_research_agent.agent.ports import ToolPort
from finance_research_agent.agent.tool import ToolDefinition

__all__ = ["ToolRegistry"]


@dataclass(frozen=True, slots=True)
class ToolRegistry:
    """Preserve tools and snapshot their definitions in caller order.

    The collection is immutable; tool implementations can own execution state.
    Definitions must remain fixed for the lifetime of a tool. Lookup uses the
    registration snapshot and never calls execute, selects a tool, or retries.
    """

    tools: tuple[ToolPort, ...]
    definitions: tuple[ToolDefinition, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.tools, tuple):
            raise ValueError("tools must be an immutable tuple")
        definitions: list[ToolDefinition] = []
        names: set[str] = set()
        for tool in self.tools:
            definition = getattr(tool, "definition", None)
            if not isinstance(definition, ToolDefinition):
                raise ValueError("tools must expose a ToolDefinition")
            if not callable(getattr(tool, "execute", None)):
                raise ValueError("tools must expose a callable execute")
            if definition.name in names:
                raise ValueError(f"duplicate tool name {definition.name!r}")
            names.add(definition.name)
            definitions.append(definition)
        object.__setattr__(self, "definitions", tuple(definitions))

    def get(self, name: str) -> ToolPort:
        """Return the original tool by exact name, or raise KeyError if absent."""

        if not isinstance(name, str):
            raise ValueError("lookup name must be a string")
        for definition, tool in zip(self.definitions, self.tools, strict=True):
            if definition.name == name:
                return tool
        raise KeyError(f"unknown tool {name!r}")
