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
ALPACA_HISTORICAL_ADAPTER = Path("adapters/alpaca_historical.py")
ALLOWED_ALPACA_FROM_IMPORTS = {
    "alpaca.common.exceptions": frozenset({"APIError"}),
    "alpaca.data.enums": frozenset({"Adjustment", "DataFeed"}),
    "alpaca.data.historical": frozenset({"StockHistoricalDataClient"}),
    "alpaca.data.models": frozenset({"BarSet"}),
    "alpaca.data.requests": frozenset({"StockBarsRequest"}),
    "alpaca.data.timeframe": frozenset({"TimeFrame"}),
}
FORBIDDEN_ALPACA_MODULE_PREFIXES = ("alpaca.broker", "alpaca.trading")


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


def _is_alpaca_module(module: str) -> bool:
    return module == "alpaca" or module.startswith("alpaca.")


def test_alpaca_sdk_imports_are_confined_to_historical_adapter() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative_path = path.relative_to(PACKAGE_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                alpaca_names = [
                    alias.name for alias in node.names if _is_alpaca_module(alias.name)
                ]
                assert not alpaca_names, (path, alpaca_names)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and _is_alpaca_module(node.module)
            ):
                assert relative_path == ALPACA_HISTORICAL_ADAPTER, (
                    path,
                    node.module,
                )
                assert node.level == 0, (path, node.module)
                assert node.module in ALLOWED_ALPACA_FROM_IMPORTS, (
                    path,
                    node.module,
                )
                imported_names = {alias.name for alias in node.names}
                assert imported_names <= ALLOWED_ALPACA_FROM_IMPORTS[node.module], (
                    path,
                    node.module,
                    imported_names,
                )
                assert all(
                    alias.name != "*" and alias.asname is None for alias in node.names
                ), path


def test_production_code_imports_no_alpaca_trading_or_broker_surface() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        for module in _imports(path):
            assert not any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in FORBIDDEN_ALPACA_MODULE_PREFIXES
            ), (path, module)


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
