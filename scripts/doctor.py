"""Run bounded local release-readiness diagnostics without provider access."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _report(name: str, passed: bool, detail: str = "") -> bool:
    result = "PASS" if passed else "FAIL"
    suffix = f" ({detail})" if detail and not passed else ""
    print(f"{name}: {result}{suffix}")
    return passed


def _project_versions(root: Path) -> tuple[str, str, str]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise ValueError("uv.lock must contain package records")
    if any(not isinstance(item, dict) for item in packages):
        raise ValueError("uv.lock package records must be tables")
    locked = [item for item in packages if item.get("name") == "finance-research-agent"]
    if len(locked) != 1:
        raise ValueError("project package must appear exactly once in uv.lock")
    return project["project"]["version"], locked[0]["version"], version("finance-research-agent")


def _private_tree(data_root: Path) -> bool:
    if data_root.is_symlink() or not data_root.is_dir():
        return False
    if stat.S_IMODE(data_root.stat().st_mode) & 0o077:
        return False
    for name in ("config", "runs", "reports", "cache", "diagnostics", "logs", "feedback"):
        path = data_root / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            return False
    config_root = data_root / "config"
    if config_root.is_dir():
        for path in config_root.iterdir():
            if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
                return False
    return True


def _run_check(command: list[str], root: Path, *, timeout: int) -> bool:
    environment = os.environ.copy()
    guard = root / "tests/network_guard"
    inherited = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(guard), str(root / "src"), str(root), inherited) if part
    )
    environment["FINANCE_RESEARCH_AGENT_OFFLINE_TESTS"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            check=False,
            timeout=timeout,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    data_root = Path(os.environ.get("AI_MARKET_RESEARCH_DATA_DIR") or "data").expanduser()
    outcomes: list[bool] = []

    python_ok = sys.version_info >= (3, 12)
    outcomes.append(
        _report(
            "Supported Python",
            python_ok,
            f"{sys.version_info.major}.{sys.version_info.minor}" if not python_ok else "",
        )
    )

    try:
        project_version, locked_version, installed_version = _project_versions(root)
        package_ok = project_version == locked_version == installed_version
    except (OSError, KeyError, PackageNotFoundError, ValueError, tomllib.TOMLDecodeError):
        package_ok = False
    detail = "" if package_ok else "project, uv.lock, and installed versions must match"
    outcomes.append(_report("Installed locked package", package_ok, detail))

    try:
        from finance_research_agent.settings import Settings

        settings = Settings.from_sources()
        credentials = settings.status()
        print(f"Alpaca API key: {credentials['alpaca_api_key'].lower()}")
        print(f"Alpaca API secret: {credentials['alpaca_api_secret'].lower()}")
    except Exception:
        settings = None
        print("Alpaca API key: unavailable")
        print("Alpaca API secret: unavailable")

    try:
        private_ok = _private_tree(data_root)
    except OSError:
        private_ok = False
    outcomes.append(
        _report("Private data-root permissions", private_ok, "expected mode 0700 or stricter")
    )

    if settings is None:
        config_ok = False
    else:
        try:
            from finance_research_agent.adapters.yaml_config import YamlConfigurationRepository
            from finance_research_agent.application.config_service import (
                ConfigService,
                configuration_from_snapshot,
            )

            snapshot = ConfigService(
                YamlConfigurationRepository(settings.data_dir / "config")
            ).validate_and_snapshot()
            source_policy = configuration_from_snapshot(snapshot).source
            config_ok = source_policy.allowed_hosts_by_adapter is not None
        except Exception:
            config_ok = False
    outcomes.append(
        _report("Configuration and host policy", config_ok, "run the local config validator")
    )

    try:
        from finance_research_agent.schema_export import check_schemas

        schema_ok = not check_schemas(root / "schemas")
    except Exception:
        schema_ok = False
    outcomes.append(_report("Generated schemas", schema_ok, "run schema export --check"))

    try:
        from scripts.check_docs_examples import validate_documentation, validate_product_a_contracts

        docs_ok = not (validate_product_a_contracts(root) or validate_documentation(root))
    except Exception:
        docs_ok = False
    outcomes.append(
        _report("Documentation and skill contracts", docs_ok, "run check_docs_examples")
    )

    plugin_ok = _run_check(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/contracts/test_plugin_package.py",
        ],
        root,
        timeout=20,
    )
    outcomes.append(_report("Plugin JSON contract", plugin_ok, "run the plugin package contract"))

    mcp_ok = _run_check(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-m",
            "not live",
            "-p",
            "no:cacheprovider",
            "tests/contracts/test_product_a_mcp_server.py",
            "-k",
            "tools_list_matches_exact_operation_names_and_typed_schemas or "
            "initialize_advertises_only_the_tools_capability",
        ],
        root,
        timeout=20,
    )
    outcomes.append(
        _report("MCP initialize/list-tools", mcp_ok, "run the bounded MCP contract tests")
    )

    ready = all(outcomes)
    print(f"Offline package readiness: {'READY' if ready else 'BLOCKED'}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
