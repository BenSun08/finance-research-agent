import ast
import re
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "finance_research_agent"
PRODUCT_A_LAYERS = ("domain", "market_data", "application", "evals")
NEUTRAL_LAYERS = ("domain", "application")
FORBIDDEN_PRODUCT_A_DEPENDENCIES = (
    "alpaca",
    "codex",
    "mcp",
    "finance_research_agent.adapters",
    "finance_research_agent.agent",
)
FORBIDDEN_MARKET_CAPABILITY_TOKENS = frozenset(
    {
        "account",
        "accounts",
        "broker",
        "brokerage",
        "buying",
        "cancel",
        "cancellation",
        "execution",
        "holding",
        "holdings",
        "order",
        "orders",
        "position",
        "positions",
        "route",
        "routing",
        "trade",
        "trading",
    }
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative_prefix = "." * node.level + (node.module or "")
                imported.append(relative_prefix)
                imported.extend(f"{relative_prefix}.{alias.name}" for alias in node.names)
            elif node.module is not None:
                imported.append(node.module)
                imported.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return tuple(imported)


def _uses_forbidden_dependency(module_name: str) -> bool:
    absolute_match = any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_PRODUCT_A_DEPENDENCIES
    )
    relative_root = module_name.lstrip(".").split(".", 1)[0]
    return absolute_match or relative_root in {"adapters", "agent"}


def _public_names(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and not node.name.startswith("_"):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(
                target.id
                for target in node.targets
                if isinstance(target, ast.Name) and not target.id.startswith("_")
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                names.append(node.target.id)
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            if not node.name.id.startswith("_"):
                names.append(node.name.id)
    return tuple(names)


def _name_tokens(name: str) -> frozenset[str]:
    snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return frozenset(snake_case.split("_"))


@pytest.mark.parametrize("layer", PRODUCT_A_LAYERS)
def test_product_a_capable_layers_do_not_import_generic_agent_runtime(layer: str) -> None:
    for path in (PACKAGE_ROOT / layer).rglob("*.py"):
        imported = _imports(path)
        assert not any(
            module.lstrip(".") == "agent"
            or module.lstrip(".").startswith("agent.")
            or module == "finance_research_agent.agent"
            or module.startswith("finance_research_agent.agent.")
            for module in imported
        ), (path, imported)


@pytest.mark.parametrize("layer", NEUTRAL_LAYERS)
def test_domain_and_application_depend_only_on_provider_neutral_layers(layer: str) -> None:
    for path in (PACKAGE_ROOT / layer).rglob("*.py"):
        imported = _imports(path)
        assert not any(_uses_forbidden_dependency(module) for module in imported), (
            path,
            imported,
        )


def test_product_a_public_surface_has_no_brokerage_or_execution_capability() -> None:
    for layer in ("domain", "market_data", "application"):
        for path in (PACKAGE_ROOT / layer).rglob("*.py"):
            for name in _public_names(path):
                assert not _name_tokens(name) & FORBIDDEN_MARKET_CAPABILITY_TOKENS, (
                    path,
                    name,
                )


def test_architecture_guard_detects_absolute_and_relative_forbidden_imports(
    tmp_path: Path,
) -> None:
    source = tmp_path / "violation.py"
    source.write_text(
        "from finance_research_agent.agent import AgentRuntime\nfrom ..adapters import alpaca\n",
        encoding="utf-8",
    )

    imported = _imports(source)

    assert any(_uses_forbidden_dependency(module) for module in imported)
