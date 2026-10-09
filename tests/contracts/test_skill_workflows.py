"""Static contracts for the three bounded Product A skills."""

from pathlib import Path, PurePosixPath
from shutil import copytree

import pytest

from scripts.check_docs_examples import (
    REQUIRED_SKILL_SECTIONS,
    declared_resources,
    parse_skill,
    validate_documentation,
    validate_product_a_contracts,
    validate_skill_contracts,
    validate_workflow,
)

ROOT = Path(__file__).resolve().parents[2]


def test_existing_market_regime_uses_shared_contract_without_behavior_change():
    assert validate_skill_contracts(ROOT) == ()
    skill = parse_skill(ROOT / "skills/market-regime/SKILL.md")
    assert set(skill.frontmatter) == {"name", "description"}
    assert skill.frontmatter["name"] == "market-regime"
    assert "synthetic completed daily bars" in skill.frontmatter["description"]
    assert declared_resources(skill) == ()
    assert "calculate_regime(snapshots, RegimePolicy(), cutoff_at)" in skill.body
    assert "exactly once" in skill.body
    assert "SYNTHETIC" in skill.body
    assert "RegimeResult" in skill.body
    assert not tuple((ROOT / "skills/market-regime").rglob("*.yaml"))


def _copy(tmp_path):
    copytree(ROOT / "skills", tmp_path / "skills")
    return tmp_path / "skills/market-regime/SKILL.md"


@pytest.mark.parametrize("mutation", [
    lambda text: text.replace("name: market-regime", "name: different"),
    lambda text: text.replace("name: market-regime", "name: market-regime\nextra: true"),
    lambda text: text.replace("name: market-regime", "name: market-regime\nname: market-regime"),
    lambda text: text.replace("## Output Obligations", "## Missing Obligations"),
    lambda text: text + "\n## Output Obligations\nDuplicate section.\n",
    lambda text: text.replace(
        "## Resource Loading", "## Resource Loading\n- Required: [X](../outside.md)"
    ),
])
def test_static_skill_drift_is_rejected(tmp_path, mutation):
    path = _copy(tmp_path)
    path.write_text(mutation(path.read_text()))
    assert validate_skill_contracts(tmp_path)


@pytest.mark.parametrize("unsafe", [
    "/absolute.md", "../outside.md", "references/../x.md", "references/./x.md",
    "references//x.md", "references\\x.md", "", "SKILL.md",
])
def test_resources_reject_raw_unsafe_paths_before_posix_normalization(tmp_path, unsafe):
    path = _copy(tmp_path)
    text = path.read_text().replace("- Required: None.", f"- Required: [X]({unsafe})")
    path.write_text(text)
    assert validate_skill_contracts(tmp_path)


def test_declared_resources_include_conditional_bytes_and_reject_duplicates(tmp_path):
    path = _copy(tmp_path)
    (path.parent / "references").mkdir()
    for name in ("a.md", "b.md"):
        (path.parent / "references" / name).write_text(name)
    text = path.read_text().replace(
        "- Required: None.", "- Required: [B](references/b.md)"
    ).replace("- Conditional: None.", "- Conditional: [A](references/a.md) — Load for review.")
    path.write_text(text)
    assert declared_resources(parse_skill(path)) == (
        PurePosixPath("references/a.md"), PurePosixPath("references/b.md"),
    )
    assert validate_skill_contracts(tmp_path) == ()
    path.write_text(text.replace("references/b.md", "references/a.md"))
    assert validate_skill_contracts(tmp_path)


def test_declared_resource_symlink_ancestors_are_rejected(tmp_path):
    path = _copy(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("outside bytes")
    (path.parent / "references").symlink_to(outside, target_is_directory=True)
    path.write_text(path.read_text().replace(
        "- Required: None.", "- Required: [X](references/x.md)"
    ))
    assert validate_skill_contracts(tmp_path)


def test_required_sections_are_exact_and_ordered():
    assert REQUIRED_SKILL_SECTIONS == (
        "Purpose and Trigger", "Accepted Inputs and Authority", "Allowed Operations",
        "Output Obligations", "Fail-Closed Behavior", "Resource Loading",
        "Safety and Forbidden Behavior",
    )


def test_three_skills_and_single_workflow_are_canonical():
    assert validate_product_a_contracts(ROOT) == ()
    assert {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")} == {
        "market-regime", "premarket-research", "watchlist-management",
    }
    manifest = ROOT / "skills/premarket-research/references/workflow-contract.yaml"
    assert validate_workflow(manifest) == ()
    assert tuple((ROOT / "skills").rglob("*.yaml")) == (manifest,)
    skill = parse_skill(manifest.parent.parent / "SKILL.md")
    assert declared_resources(skill) == (PurePosixPath("references/workflow-contract.yaml"),)


@pytest.mark.parametrize("mutation", [
    lambda text: text.replace("schema_version: 1", "schema_version: true"),
    lambda text: text.replace("schema_version: 1", "schema_version: 1\nschema_version: 1"),
    lambda text: text.replace("max_repairs: 2", "max_repairs: 3"),
    lambda text: text.replace("get_system_status", "discover_tools"),
    lambda text: text.replace("consumes: []", "consumes: [research_packet]"),
    lambda text: text.replace("same_frozen_packet", "new_packet"),
    lambda text: text + "\nprovider: alpaca\n",
    lambda text: text.replace("kind: codex_synthesis", "kind: mcp_tool"),
])
def test_workflow_drift_is_rejected(tmp_path, mutation):
    source = ROOT / "skills/premarket-research/references/workflow-contract.yaml"
    target = tmp_path / "workflow-contract.yaml"
    target.write_text(mutation(source.read_text()))
    assert validate_workflow(target)


def test_navigation_and_paused_scheduling_are_derived_from_canonical_contracts():
    assert validate_documentation(ROOT) == ()


def test_broken_documentation_link_is_rejected(tmp_path):
    copytree(ROOT / "skills", tmp_path / "skills")
    copytree(ROOT / "docs", tmp_path / "docs")
    copytree(ROOT / "src", tmp_path / "src")
    (tmp_path / "README.md").write_text((ROOT / "README.md").read_text())
    path = tmp_path / "skills/README.md"
    path.write_text(path.read_text() + "\n- [Missing](missing.md)\n")
    assert validate_documentation(tmp_path)


@pytest.mark.parametrize("declaration", [
    "- Required: None.\n- Required: None.",
    "- Required: None.\n- Required: [X](references/x.md)",
])
def test_resource_none_cannot_conflict_with_another_declaration(tmp_path, declaration):
    path = _copy(tmp_path)
    (path.parent / "references").mkdir()
    (path.parent / "references/x.md").write_text("resource")
    path.write_text(path.read_text().replace("- Required: None.", declaration))
    assert validate_skill_contracts(tmp_path)


def test_malformed_yaml_mapping_is_a_contract_error(tmp_path):
    path = tmp_path / "workflow-contract.yaml"
    path.write_text("? [unhashable, key]\n: value\n")
    assert validate_workflow(path)


def test_skill_local_executable_script_is_rejected(tmp_path):
    path = _copy(tmp_path)
    (path.parent / "scripts").mkdir()
    (path.parent / "scripts/calculate.py").write_text("print('wrong owner')")
    assert validate_product_a_contracts(tmp_path)


def test_missing_internal_documentation_anchor_is_rejected(tmp_path):
    copytree(ROOT / "skills", tmp_path / "skills")
    copytree(ROOT / "docs", tmp_path / "docs")
    copytree(ROOT / "src", tmp_path / "src")
    (tmp_path / "README.md").write_text((ROOT / "README.md").read_text())
    path = tmp_path / "docs/operations/scheduling-and-recovery.md"
    path.write_text(path.read_text().replace("#product-a-stdio-server", "#missing-heading"))
    assert validate_documentation(tmp_path)


@pytest.mark.parametrize("mutation", [
    lambda text: text.removeprefix("---\n"),
    lambda text: text.replace("description: Use", "description: \nignored: Use"),
    lambda text: text.replace("- Required: None.", "Unexpected prose"),
    lambda text: text.replace("- Required: None.", "- Required: invalid"),
    lambda text: text.replace("- Conditional: None.", ""),
    lambda text: text.replace("- Conditional: None.", "- Conditional: [X](references/x.md)"),
    lambda text: text.replace("- Required: None.", "- Required: [X](references/missing.md)"),
])
def test_static_checker_failure_branches(tmp_path, mutation):
    path = _copy(tmp_path)
    path.write_text(mutation(path.read_text()))
    assert validate_skill_contracts(tmp_path)


@pytest.mark.parametrize("target, old, new", [
    ("skills/README.md", "# Project Skills", "Unapproved workflow prose"),
    ("docs/architecture/v0.1-boundaries.md", "Python typed domain", "Missing authority"),
    ("docs/architecture/v0.1-boundaries.md", "does not rewrite history", "rewrites history"),
    ("docs/operations/scheduling-and-recovery.md", "PAUSED", "ACTIVE"),
    ("docs/operations/scheduling-and-recovery.md", "no execution", "get_system_status"),
])
def test_documentation_contract_drift_branches(tmp_path, target, old, new):
    copytree(ROOT / "skills", tmp_path / "skills")
    copytree(ROOT / "docs", tmp_path / "docs")
    copytree(ROOT / "src", tmp_path / "src")
    (tmp_path / "README.md").write_text((ROOT / "README.md").read_text())
    path = tmp_path / target
    path.write_text(path.read_text().replace(old, new))
    assert validate_documentation(tmp_path)


def test_missing_inventory_and_documentation_fail_closed(tmp_path):
    assert validate_product_a_contracts(tmp_path)
    assert validate_documentation(tmp_path)


def test_unavailable_operation_allowlist_fails_closed(monkeypatch):
    from finance_research_agent.application import skill_contracts

    monkeypatch.setattr(skill_contracts, "OPERATION_NAMES", ())
    manifest = ROOT / "skills/premarket-research/references/workflow-contract.yaml"
    assert validate_workflow(manifest)


def test_checker_cli_reports_success_and_failure(tmp_path, monkeypatch, capsys):
    from scripts.check_docs_examples import main

    monkeypatch.setattr("sys.argv", ["checker", "--root", str(ROOT)])
    assert main() == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr("sys.argv", ["checker", "--root", str(tmp_path)])
    assert main() == 1
    assert "missing documentation" in capsys.readouterr().out


def test_watchlist_skill_names_exact_typed_write_operations():
    from finance_research_agent.application.operations import OPERATION_NAMES

    body = parse_skill(ROOT / "skills/watchlist-management/SKILL.md").body
    for operation in ("list_watchlist", "upsert_watchlist_item", "remove_watchlist_item"):
        assert operation in OPERATION_NAMES
        assert f"`{operation}`" in body


@pytest.mark.parametrize("operation", ["upsert_watchlist_item", "remove_watchlist_item"])
def test_watchlist_operation_alias_cannot_pass_static_gate(tmp_path, operation):
    copytree(ROOT / "skills", tmp_path / "skills")
    path = tmp_path / "skills/watchlist-management/SKILL.md"
    path.write_text(path.read_text().replace(operation, operation.replace("_item", "_entry")))
    assert validate_skill_contracts(tmp_path)
