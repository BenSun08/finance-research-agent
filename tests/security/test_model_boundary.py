import ast
from pathlib import Path

import pytest

import finance_research_agent.adapters.fake_model as fake_module
import finance_research_agent.adapters.fake_tool as fake_tool_module
import finance_research_agent.agent as agent_package

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
