from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from finance_research_agent.adapters.fake_tool import FakeToolPort
from finance_research_agent.agent import ToolDefinition, ToolPort, ToolRegistry


def _tool(name: str) -> FakeToolPort:
    return FakeToolPort(ToolDefinition(name, "Fixed test definition"), ())


@pytest.mark.parametrize("tools", [[], [_tool("test")], None, "test"])
def test_registry_requires_tuple(tools: object) -> None:
    with pytest.raises(ValueError, match="tools must be an immutable tuple"):
        ToolRegistry(cast(tuple[ToolPort, ...], tools))


def test_empty_registry_has_empty_discovery_and_explicit_missing_lookup() -> None:
    registry = ToolRegistry(())

    assert registry.tools == ()
    assert registry.definitions == ()
    with pytest.raises(KeyError, match="unknown tool 'missing'"):
        registry.get("missing")


@pytest.mark.parametrize("same_instance", [False, True])
def test_duplicate_names_rejected_even_with_different_descriptions(same_instance: bool) -> None:
    first = _tool("duplicate")
    second = first if same_instance else FakeToolPort(ToolDefinition("duplicate", "Other"), ())

    with pytest.raises(ValueError, match="duplicate tool name 'duplicate'"):
        ToolRegistry((first, second))
    assert first.requests == second.requests == ()


def test_registry_preserves_caller_order_and_original_tool_identity_without_execution() -> None:
    last, first = _tool("z-last"), _tool("a-first")
    tools = (last, first)
    registry = ToolRegistry(tools)

    assert registry.tools is tools
    assert registry.definitions == (last.definition, first.definition)
    assert registry.definitions[0] is last.definition
    assert registry.get("a-first") is first
    assert registry.get("z-last") is last
    assert last.requests == first.requests == ()


@pytest.mark.parametrize("name", ["missing", "TEST", " test", "test ", "test\n", "", "tes"])
def test_lookup_requires_exact_registered_name(name: str) -> None:
    registry = ToolRegistry((_tool("test"),))

    with pytest.raises(KeyError, match="unknown tool"):
        registry.get(name)


@pytest.mark.parametrize("name", [None, 1, []])
def test_lookup_rejects_non_string_names(name: object) -> None:
    with pytest.raises(ValueError, match="lookup name must be a string"):
        ToolRegistry(()).get(cast(str, name))


@pytest.mark.parametrize("tool", [None, object(), "test", ToolDefinition("test", "Description")])
def test_registry_rejects_members_without_definition(tool: object) -> None:
    with pytest.raises(ValueError, match="tools must expose a ToolDefinition"):
        ToolRegistry((cast(ToolPort, tool),))


class _MissingExecute:
    definition = ToolDefinition("test", "Description")


class _NonCallableExecute:
    definition = ToolDefinition("test", "Description")
    execute = "not callable"


@pytest.mark.parametrize("tool", [_MissingExecute(), _NonCallableExecute()])
def test_registry_rejects_members_without_callable_execute(tool: object) -> None:
    with pytest.raises(ValueError, match="tools must expose a callable execute"):
        ToolRegistry((cast(ToolPort, tool),))


@pytest.mark.parametrize("field", ["tools", "definitions"])
def test_registry_collection_and_discovery_are_frozen_and_slotted(field: str) -> None:
    registry = ToolRegistry((_tool("test"),))

    with pytest.raises(FrozenInstanceError):
        setattr(registry, field, ())
    assert not hasattr(registry, "__dict__")
    assert isinstance(registry.tools, tuple)
    assert isinstance(registry.definitions, tuple)


def test_repeated_construction_and_lookup_are_deterministic() -> None:
    tools = (_tool("second"), _tool("first"))
    first, second = ToolRegistry(tools), ToolRegistry(tools)

    assert first == second
    assert first.definitions == second.definitions
    for name in ("first", "second", "first"):
        assert first.get(name) is second.get(name)
