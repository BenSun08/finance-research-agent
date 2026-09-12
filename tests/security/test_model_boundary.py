import ast
from pathlib import Path

import pytest

import finance_research_agent.adapters.fake_model as fake_module
import finance_research_agent.agent as agent_package

MODEL_SOURCES = (
    *Path(agent_package.__file__).parent.rglob("*.py"),
    Path(fake_module.__file__),
)


@pytest.mark.parametrize("path", MODEL_SOURCES, ids=lambda path: path.name)
def test_model_boundary_has_no_sdk_network_environment_clock_git_or_process_dependency(
    path: Path,
) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed_imports = {
        "dataclasses",
        "typing",
        "finance_research_agent.agent.model",
        "finance_research_agent.agent.ports",
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
