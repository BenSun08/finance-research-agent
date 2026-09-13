from collections import UserDict
from collections.abc import Mapping, MutableMapping
from dataclasses import FrozenInstanceError
from decimal import Decimal
from types import MappingProxyType
from typing import cast

import pytest

from finance_research_agent.agent import (
    ToolArgumentValue,
    ToolDefinition,
    ToolPort,
    ToolRequest,
    ToolResult,
)


@pytest.mark.parametrize("name", ["a", "tool", "tool2", "test_tool", "test-tool", "a_1-b2"])
def test_valid_definition_and_request_preserve_name(name: str) -> None:
    description = "  Offline test\nRésumé 市场\t "
    definition = ToolDefinition(name, description)

    assert definition.name is name
    assert definition.description is description
    assert ToolRequest(name, {}).name is name


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        "\t\n",
        "Tool",
        " tool",
        "tool ",
        "tool\n",
        "1tool",
        "_tool",
        "tool-",
        "tool__name",
        "tool--name",
        "tool_-name",
        "tool.name",
        "tool/name",
        "工具",
        None,
        1,
        [],
    ],
)
def test_invalid_name_rejected_by_definition_and_request(name: object) -> None:
    with pytest.raises(ValueError, match="name must be a canonical lowercase ASCII token"):
        ToolDefinition(cast(str, name), "Description")
    with pytest.raises(ValueError, match="name must be a canonical lowercase ASCII token"):
        ToolRequest(cast(str, name), {})


@pytest.mark.parametrize("text", ["", " ", "\t\n\r", "\u2003", None, 1, b"Text", []])
def test_invalid_description_and_result_content_rejected(text: object) -> None:
    with pytest.raises(ValueError, match="description must be a nonblank string"):
        ToolDefinition("test", cast(str, text))
    with pytest.raises(ValueError, match="content must be a nonblank string"):
        ToolResult(cast(str, text))


def test_result_preserves_text_exactly() -> None:
    content = "  Predetermined\nRésumé 市场\t "

    assert ToolResult(content).content is content


def test_arguments_copy_preserves_scalar_types_values_and_caller_order() -> None:
    arguments: dict[str, ToolArgumentValue] = {
        "text": "  unchanged  ",
        "integer": 2,
        "float": 2.5,
        "flag": True,
        "missing": None,
        "empty": "",
        "negative": -1,
        "zero": -0.0,
    }
    request = ToolRequest("test", arguments)

    assert request.arguments == arguments
    assert tuple(request.arguments) == tuple(arguments)
    assert all(type(request.arguments[key]) is type(value) for key, value in arguments.items())
    arguments["integer"] = 9
    del arguments["text"]
    arguments["new"] = False
    assert request.arguments["integer"] == 2
    assert request.arguments["text"] == "  unchanged  "
    assert "new" not in request.arguments
    with pytest.raises(TypeError):
        cast(MutableMapping[str, ToolArgumentValue], request.arguments)["integer"] = 10


@pytest.mark.parametrize("arguments", [{}, UserDict({"key": "value"}), MappingProxyType({})])
def test_mapping_inputs_and_empty_arguments_are_valid(
    arguments: Mapping[str, ToolArgumentValue],
) -> None:
    assert ToolRequest("test", arguments).arguments == arguments


def test_proxy_backing_dictionary_is_also_disconnected() -> None:
    original: dict[str, ToolArgumentValue] = {"key": "original"}
    request = ToolRequest("test", MappingProxyType(original))
    original["key"] = "changed"

    assert request.arguments == {"key": "original"}


@pytest.mark.parametrize("arguments", [None, [], (), [("key", "value")], "text", 1])
def test_non_mapping_arguments_rejected(arguments: object) -> None:
    with pytest.raises(ValueError, match="arguments must be a mapping"):
        ToolRequest("test", cast(Mapping[str, ToolArgumentValue], arguments))


@pytest.mark.parametrize("key", [1, None, ("key",), b"key"])
def test_non_string_argument_keys_rejected(key: object) -> None:
    with pytest.raises(ValueError, match="argument keys must be strings"):
        ToolRequest("test", cast(Mapping[str, ToolArgumentValue], {key: "value"}))


def test_string_keys_are_preserved_without_schema_or_normalization() -> None:
    arguments = {"": None, " free form ": True, "工具": "text"}

    assert ToolRequest("test", arguments).arguments == arguments


class _ScalarSubclass(int):
    pass


@pytest.mark.parametrize(
    "value",
    [[], {}, (), {1}, object(), b"bytes", Decimal("1"), _ScalarSubclass(1)],
)
def test_nested_and_non_builtin_argument_values_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="argument values must be built-in JSON scalars"):
        ToolRequest("test", {"key": cast(ToolArgumentValue, value)})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_argument_floats_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="argument floats must be finite"):
        ToolRequest("test", {"key": value})


@pytest.mark.parametrize(
    ("contract", "field", "replacement"),
    [
        (ToolDefinition("test", "Description"), "name", "changed"),
        (ToolDefinition("test", "Description"), "description", "Changed"),
        (ToolRequest("test", {}), "name", "changed"),
        (ToolRequest("test", {}), "arguments", {}),
        (ToolResult("Result"), "content", "Changed"),
    ],
)
def test_contracts_are_frozen_and_slotted(
    contract: object,
    field: str,
    replacement: object,
) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(contract, field, replacement)
    assert not hasattr(contract, "__dict__")


class _IndependentTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition("independent", "Independent structural implementation")

    def execute(self, request: ToolRequest) -> ToolResult:
        if request.name != self.definition.name:
            raise ValueError("request name must match tool definition")
        return ToolResult("Fixed result")


def test_protocol_accepts_independent_implementation_without_inheritance() -> None:
    tool: ToolPort = _IndependentTool()

    assert tool.definition.name == "independent"
    assert tool.execute(ToolRequest("independent", {})) == ToolResult("Fixed result")


def test_repeated_contract_construction_is_deterministic() -> None:
    for _ in range(3):
        assert ToolDefinition("test", "Description") == ToolDefinition("test", "Description")
        assert ToolRequest("test", {"a": 1, "b": None}) == ToolRequest("test", {"a": 1, "b": None})
        assert ToolResult("Result") == ToolResult("Result")
