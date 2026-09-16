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
UNAMBIGUOUS_FORBIDDEN_MARKET_CAPABILITY_TOKENS = frozenset(
    {
        "account",
        "accounts",
        "broker",
        "brokerage",
        "buying",
        "cancel",
        "cancellation",
        "holding",
        "holdings",
        "route",
        "router",
        "routing",
        "stream",
        "streaming",
        "subscribe",
        "subscription",
        "subscriptions",
        "websocket",
        "websockets",
    }
)
AMBIGUOUS_MARKET_CAPABILITY_TOKENS = frozenset(
    {"execution", "order", "orders", "position", "positions", "trade", "trading"}
)
BROKER_OPERATION_CONTEXT_TOKENS = frozenset(
    {
        "cancel",
        "cancellation",
        "client",
        "endpoint",
        "execute",
        "fill",
        "fills",
        "gateway",
        "place",
        "port",
        "service",
        "submit",
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


def _assignment_names(target: ast.expr) -> tuple[str, ...]:
    if isinstance(target, ast.Name):
        return (target.id,) if not target.id.startswith("_") else ()
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        and not target.attr.startswith("_")
    ):
        return (target.attr,)
    if isinstance(target, (ast.List, ast.Tuple)):
        return tuple(name for element in target.elts for name in _assignment_names(element))
    return ()


def _instance_field_names(target: ast.expr) -> tuple[str, ...]:
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        and not target.attr.startswith("_")
    ):
        return (target.attr,)
    if isinstance(target, (ast.List, ast.Tuple)):
        return tuple(name for element in target.elts for name in _instance_field_names(element))
    return ()


def _annotation_text(annotation: ast.expr | None) -> tuple[str, ...]:
    return () if annotation is None else (ast.unparse(annotation),)


def _argument_surface(arguments: ast.arguments) -> tuple[str, ...]:
    values: list[str] = []
    all_arguments = (
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
    )
    for argument in all_arguments:
        if argument.arg not in {"self", "cls"} and not argument.arg.startswith("_"):
            values.append(argument.arg)
        values.extend(_annotation_text(argument.annotation))
    for variadic_argument in (arguments.vararg, arguments.kwarg):
        if variadic_argument is None:
            continue
        if not variadic_argument.arg.startswith("_"):
            values.append(variadic_argument.arg)
        values.extend(_annotation_text(variadic_argument.annotation))
    return tuple(values)


def _literal_strings(value: ast.expr | None) -> tuple[str, ...]:
    if not isinstance(value, (ast.List, ast.Set, ast.Tuple)):
        return ()
    return tuple(
        element.value
        for element in value.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    )


def _special_assignment_values(
    node: ast.Assign | ast.AnnAssign,
    target_name: str,
) -> tuple[str, ...]:
    targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
    if not any(isinstance(target, ast.Name) and target.id == target_name for target in targets):
        return ()
    return _literal_strings(node.value)


def _module_public_binding_names(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(
                alias.asname or alias.name.split(".", 1)[0]
                for alias in node.names
                if not (alias.asname or alias.name.split(".", 1)[0]).startswith("_")
            )
        elif isinstance(node, ast.ImportFrom):
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if (alias.asname or alias.name) != "*"
                and not (alias.asname or alias.name).startswith("_")
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(name for target in node.targets for name in _assignment_names(target))
        elif isinstance(node, ast.AnnAssign):
            names.update(_assignment_names(node.target))
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            if not node.name.id.startswith("_"):
                names.add(node.name.id)
    return frozenset(names)


def _import_binding_origins(tree: ast.Module) -> dict[str, str]:
    origins: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                origins[alias.asname or alias.name.split(".", 1)[0]] = f"import:{alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                if alias.name != "*":
                    origins[alias.asname or alias.name] = f"import:{node.module}.{alias.name}"
    return origins


def _public_assignment_import_origins(
    node: ast.Assign | ast.AnnAssign,
    import_origins: dict[str, str],
) -> tuple[str, ...]:
    if not isinstance(node.value, ast.Name):
        return ()
    origin = import_origins.get(node.value.id)
    if origin is None:
        return ()
    targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
    return tuple(origin for target in targets if _assignment_names(target))


def _public_surface_exposures(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    public_binding_names = _module_public_binding_names(tree)
    import_origins = _import_binding_origins(tree)
    exposures: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                exposed_name = alias.asname or alias.name.split(".", 1)[0]
                if not exposed_name.startswith("_"):
                    exposures.append(exposed_name)
                    exposures.append(f"import:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                exposed_name = alias.asname or alias.name
                if exposed_name != "*" and not exposed_name.startswith("_"):
                    exposures.append(exposed_name)
                    if node.module is not None:
                        exposures.append(f"import:{node.module}.{alias.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                exposures.append(node.name)
                exposures.extend(_argument_surface(node.args))
                exposures.extend(_annotation_text(node.returns))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            exposures.append(node.name)
            exposures.extend(ast.unparse(base) for base in node.bases)
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not member.name.startswith("_"):
                        exposures.append(member.name)
                    if not member.name.startswith("_") or member.name == "__init__":
                        exposures.extend(_argument_surface(member.args))
                        exposures.extend(_annotation_text(member.returns))
                    for descendant in ast.walk(member):
                        if isinstance(descendant, ast.Assign):
                            exposures.extend(
                                name
                                for target in descendant.targets
                                for name in _instance_field_names(target)
                            )
                        elif isinstance(descendant, ast.AnnAssign):
                            field_names = _instance_field_names(descendant.target)
                            exposures.extend(field_names)
                            if field_names:
                                exposures.extend(_annotation_text(descendant.annotation))
                elif isinstance(member, ast.Assign):
                    exposures.extend(_special_assignment_values(member, "__slots__"))
                    exposures.extend(
                        name for target in member.targets for name in _assignment_names(target)
                    )
                elif isinstance(member, ast.AnnAssign):
                    exposures.extend(_special_assignment_values(member, "__slots__"))
                    field_names = _assignment_names(member.target)
                    exposures.extend(field_names)
                    if field_names:
                        exposures.extend(_annotation_text(member.annotation))
                elif isinstance(member, ast.TypeAlias) and isinstance(member.name, ast.Name):
                    if not member.name.id.startswith("_"):
                        exposures.append(member.name.id)
                        exposures.append(ast.unparse(member.value))
        elif isinstance(node, ast.Assign):
            exports = _special_assignment_values(node, "__all__")
            exposures.extend(f"export:{name}" for name in exports if name in public_binding_names)
            exposures.extend(name for target in node.targets for name in _assignment_names(target))
            exposures.extend(_public_assignment_import_origins(node, import_origins))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            exports = _special_assignment_values(node, "__all__")
            exposures.extend(f"export:{name}" for name in exports if name in public_binding_names)
            if not node.target.id.startswith("_"):
                exposures.append(node.target.id)
                exposures.extend(_annotation_text(node.annotation))
            exposures.extend(_public_assignment_import_origins(node, import_origins))
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            if not node.name.id.startswith("_"):
                exposures.append(node.name.id)
                exposures.append(ast.unparse(node.value))
    return tuple(exposures)


def _name_tokens(name: str) -> frozenset[str]:
    tokens: set[str] = set()
    for identifier in re.findall(r"[A-Za-z][A-Za-z0-9_]*", name):
        snake_case = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", identifier).lower()
        tokens.update(part for part in snake_case.split("_") if part)
    return frozenset(tokens)


def _is_forbidden_market_capability(exposure: str) -> bool:
    tokens = _name_tokens(exposure)
    if tokens & UNAMBIGUOUS_FORBIDDEN_MARKET_CAPABILITY_TOKENS:
        return True
    if exposure.startswith("import:") and tokens & AMBIGUOUS_MARKET_CAPABILITY_TOKENS:
        return True
    return bool(
        tokens & AMBIGUOUS_MARKET_CAPABILITY_TOKENS and tokens & BROKER_OPERATION_CONTEXT_TOKENS
    )


def _forbidden_capability_exposures(path: Path) -> tuple[str, ...]:
    return tuple(
        exposure
        for exposure in _public_surface_exposures(path)
        if _is_forbidden_market_capability(exposure)
    )


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
            forbidden_exposures = _forbidden_capability_exposures(path)
            assert not forbidden_exposures, (path, forbidden_exposures)


@pytest.mark.parametrize(
    "source_text",
    [
        pytest.param(
            "from finance_research_agent.agent import AgentRuntime\n",
            id="absolute-agent-import",
        ),
        pytest.param(
            "from ..adapters import alpaca\n",
            id="relative-adapter-import",
        ),
    ],
)
def test_dependency_guard_detects_each_forbidden_import_form_independently(
    tmp_path: Path,
    source_text: str,
) -> None:
    source = tmp_path / "violation.py"
    source.write_text(source_text, encoding="utf-8")

    imported = _imports(source)

    assert any(_uses_forbidden_dependency(module) for module in imported)


@pytest.mark.parametrize(
    ("source_text", "expected_exposures"),
    [
        pytest.param(
            "class OrderGateway:\n    pass\n",
            ("OrderGateway",),
            id="declaration",
        ),
        pytest.param(
            "class MarketData:\n    def stream_quotes(self):\n        pass\n",
            ("stream_quotes",),
            id="public-method",
        ),
        pytest.param(
            "class Snapshot:\n    position_client: object\n",
            ("position_client",),
            id="public-class-field",
        ),
        pytest.param(
            "class Snapshot:\n    __slots__ = ('order_gateway',)\n",
            ("order_gateway",),
            id="public-slotted-field",
        ),
        pytest.param(
            "class Snapshot:\n    def __init__(self):\n        self.buying_power = 1\n",
            ("buying_power",),
            id="public-instance-field",
        ),
        pytest.param(
            "def fetch_quotes() -> StreamingFeed:\n    pass\n",
            ("StreamingFeed",),
            id="annotation",
        ),
        pytest.param(
            "def submit_order(order):\n    pass\n",
            ("submit_order",),
            id="public-parameter",
        ),
        pytest.param(
            "from provider import PositionClient\n",
            ("PositionClient", "import:provider.PositionClient"),
            id="public-import",
        ),
        pytest.param(
            "from provider import Client as TradingClient\n",
            ("TradingClient",),
            id="aliased-public-import",
        ),
        pytest.param(
            "from provider import PositionClient as client\n",
            ("import:provider.PositionClient",),
            id="neutral-alias-from-import",
        ),
        pytest.param(
            "import provider.brokerage as market_api\n",
            ("import:provider.brokerage",),
            id="neutral-alias-module-import",
        ),
        pytest.param(
            "import provider.trading as market_api\n",
            ("import:provider.trading",),
            id="neutral-alias-trading-module-import",
        ),
        pytest.param(
            "from provider import AccountClient as _account_client\n"
            "AccountClient = _account_client\n"
            "__all__ = ['AccountClient']\n",
            ("AccountClient", "import:provider.AccountClient", "export:AccountClient"),
            id="private-import-public-re-export",
        ),
    ],
)
def test_capability_guard_detects_each_public_exposure_form(
    tmp_path: Path,
    source_text: str,
    expected_exposures: tuple[str, ...],
) -> None:
    source = tmp_path / "capability.py"
    source.write_text(source_text, encoding="utf-8")

    assert _forbidden_capability_exposures(source) == expected_exposures


@pytest.mark.parametrize(
    "source_text",
    [
        pytest.param(
            "class TradePlanDraft:\n    position_sizing: PositionSizing\n",
            id="research-contract-vocabulary",
        ),
        pytest.param(
            "class RunContext:\n    execution_status: str\n",
            id="workflow-state-vocabulary",
        ),
        pytest.param(
            "def sort_candidates(order: str):\n    pass\n",
            id="ordering-parameter-vocabulary",
        ),
    ],
)
def test_capability_guard_allows_legitimate_research_and_workflow_vocabulary(
    tmp_path: Path,
    source_text: str,
) -> None:
    source = tmp_path / "legitimate.py"
    source.write_text(source_text, encoding="utf-8")

    assert _forbidden_capability_exposures(source) == ()
