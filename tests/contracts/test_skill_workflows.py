"""Static contracts for the three bounded Product A skills."""

from pathlib import Path, PurePosixPath
from shutil import copytree

import pytest

from scripts.check_docs_examples import (
    REQUIRED_SKILL_SECTIONS,
    declared_resources,
    parse_skill,
    validate_skill_contracts,
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
