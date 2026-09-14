import ast
from pathlib import Path
from typing import get_type_hints

import pytest

import finance_research_agent.adapters.fake_model as fake_module
import finance_research_agent.adapters.fake_tool as fake_tool_module
import finance_research_agent.agent as agent_package
from finance_research_agent.agent import ToolDefinition, ToolPort, ToolRequest, ToolResult

AGENT_SOURCES = (
    *Path(agent_package.__file__).parent.rglob("*.py"),
    Path(fake_module.__file__),
    Path(fake_tool_module.__file__),
)


@pytest.mark.parametrize("path", AGENT_SOURCES, ids=lambda path: path.name)
def test_agent_boundary_has_no_sdk_network_environment_clock_git_or_process_dependency(
    path: Path,
) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed_imports = {
        "collections.abc",
        "dataclasses",
        "math",
        "re",
        "types",
        "typing",
        "finance_research_agent.agent.model",
        "finance_research_agent.agent.ports",
        "finance_research_agent.agent.registry",
        "finance_research_agent.agent.tool",
    }
    forbidden_calls = {"open", "print", "input", "exec", "eval", "__import__", "exit", "quit"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed_imports for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module in allowed_imports
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"now", "utcnow", "today", "time"}


def test_existing_domain_data_workflow_and_evals_remain_independent_of_agent() -> None:
    package_root = Path(agent_package.__file__).parent.parent
    for layer in ("domain", "market_data", "application", "evals"):
        for path in (package_root / layer).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    assert node.level == 0, path
                    names = [node.module or ""]
                    names.extend(f"{node.module}.{alias.name}" for alias in node.names)
                else:
                    continue
                assert not any(
                    name == "finance_research_agent.agent"
                    or name.startswith("finance_research_agent.agent.")
                    for name in names
                ), path


def test_tool_port_signature_uses_only_execution_contracts() -> None:
    assert get_type_hints(ToolPort.execute) == {
        "request": ToolRequest,
        "return": ToolResult,
    }
    assert isinstance(ToolPort.definition, property)
    assert ToolPort.definition.fget is not None
    assert get_type_hints(ToolPort.definition.fget) == {"return": ToolDefinition}


@pytest.mark.parametrize("filename", ["tool.py", "registry.py"])
def test_execution_contracts_and_registry_do_not_reference_model_contracts(
    filename: str,
) -> None:
    path = Path(agent_package.__file__).parent / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"ModelResponse", "ModelRequest", "ModelPort", "AssistantAction", "ToolCall"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id not in forbidden
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "finance_research_agent.agent.model"
            assert not any(alias.name in forbidden for alias in node.names)


@pytest.mark.parametrize("filename", ["model.py", "../adapters/fake_model.py"])
def test_model_boundary_does_not_lookup_or_execute_tools(filename: str) -> None:
    path = Path(agent_package.__file__).parent / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {
                "finance_research_agent.agent.ports",
                "finance_research_agent.agent.registry",
            }
            assert not any(alias.name in {"ToolPort", "ToolRequest"} for alias in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"execute", "get"}
