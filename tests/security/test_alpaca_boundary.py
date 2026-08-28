import ast
from pathlib import Path

PACKAGE_ROOT = (
    Path(__file__).resolve().parents[2] / "src" / "finance_research_agent"
)
FORBIDDEN_DIRECT_TRANSPORT_PREFIXES = (
    "aiohttp",
    "http.client",
    "httpx",
    "importlib",
    "requests",
    "socket",
    "subprocess",
    "urllib",
    "websockets",
)
FORBIDDEN_ENDPOINT_FRAGMENTS = (
    "/v2/account",
    "/v2/orders",
    "/v2/positions",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def _string_literals(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _dynamic_import_calls(path: Path) -> tuple[ast.Call, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            or isinstance(node.func, ast.Attribute)
            and node.func.attr in {"__import__", "import_module"}
        )
    )


def test_first_data_slice_imports_no_alpaca_sdk_surface() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        for module in _imports(path):
            assert not module.startswith("alpaca"), (path, module)


def test_first_data_slice_has_no_direct_network_escape_hatch() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        for module in _imports(path):
            assert not module.startswith(FORBIDDEN_DIRECT_TRANSPORT_PREFIXES), (
                path,
                module,
            )


def test_first_data_slice_has_no_dynamic_import_escape_hatch() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        assert not _dynamic_import_calls(path), path


def test_domain_and_provider_neutral_data_do_not_depend_on_adapters() -> None:
    neutral_paths = tuple((PACKAGE_ROOT / "domain").rglob("*.py")) + tuple(
        (PACKAGE_ROOT / "market_data").rglob("*.py")
    )

    for path in neutral_paths:
        imported = _imports(path)
        assert not any(
            module.startswith("finance_research_agent.adapters")
            or module.startswith("alpaca")
            for module in imported
        ), path


def test_production_code_contains_no_brokerage_endpoint_literal() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        literals = _string_literals(path)
        assert not any(
            fragment in literal
            for fragment in FORBIDDEN_ENDPOINT_FRAGMENTS
            for literal in literals
        ), path
