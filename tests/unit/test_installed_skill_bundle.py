"""Strict installed resources, independent of the editable source tree."""

import json
from importlib.metadata import PackagePath
from pathlib import Path

import pytest

from finance_research_agent.application import skill_bundle
from finance_research_agent.application.skill_bundle import (
    PLUGIN_RESOURCE_PATHS,
    PLUGIN_RESOURCE_ROOT,
    compute_skill_version,
    load_installed_plugin_version,
    load_installed_premarket_skill_version,
)

ROOT = Path(__file__).resolve().parents[2]


def _contents():
    return {
        ".codex-plugin/plugin.json": json.dumps(
            {
                "name": "ai-market-research-agent",
                "version": "0.1.0",
                "description": "Research only",
                "skills": "./skills/",
                "mcpServers": "./.mcp.json",
                "interface": {"displayName": "Research"},
            }
        ).encode(),
        ".mcp.json": json.dumps(
            {
                "mcpServers": {
                    "ai-market-research": {"command": "ai-market-research-mcp", "args": []},
                }
            }
        ).encode(),
        **{
            name: (ROOT / name).read_bytes()
            for name in PLUGIN_RESOURCE_PATHS
            if name.startswith("skills/")
        },
    }


class InstalledDistribution:
    def __init__(self, root: Path, contents):
        self.root = root
        self.names = [PLUGIN_RESOURCE_ROOT + "/" + name for name in contents]
        for name, content in zip(self.names, contents.values(), strict=True):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def locate_file(self, path):
        return self.root / str(path)

    @property
    def files(self):
        result = []
        for name in self.names:
            path = PackagePath(name)
            path.dist = self
            result.append(path)
        return result

    def read_text(self, name):
        assert name == "RECORD"
        return "\n".join(name + ",," for name in self.names)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    contents = _contents()
    distribution = InstalledDistribution(tmp_path, contents)
    monkeypatch.setattr(skill_bundle, "distribution", lambda name: distribution)
    return distribution, contents


def test_loader_hashes_actual_installed_bytes_and_independent_plugin_version(installed):
    distribution, contents = installed
    expected = compute_skill_version(
        {
            "SKILL.md": contents["skills/premarket-research/SKILL.md"],
            "references/workflow-contract.yaml": contents[
                "skills/premarket-research/references/workflow-contract.yaml"
            ],
        }
    )
    assert load_installed_premarket_skill_version() == expected
    assert load_installed_plugin_version() == "0.1.0"
    path = distribution.root / PLUGIN_RESOURCE_ROOT / "skills/premarket-research/SKILL.md"
    path.write_bytes(path.read_bytes() + b"changed\n")
    assert load_installed_premarket_skill_version() != expected


@pytest.mark.parametrize("change", ("missing", "duplicate", "extra", "suffix", "raw_dot"))
def test_loader_rejects_missing_ambiguous_or_noncanonical_entries(installed, change):
    distribution, _ = installed
    required = PLUGIN_RESOURCE_ROOT + "/skills/premarket-research/SKILL.md"
    if change == "missing":
        distribution.names.remove(required)
    elif change == "duplicate":
        distribution.names.append(required)
    elif change == "extra":
        distribution.names.append(PLUGIN_RESOURCE_ROOT + "/skills/extra/SKILL.md")
    elif change == "suffix":
        distribution.names[distribution.names.index(required)] = "other/" + required
    else:
        distribution.names[distribution.names.index(required)] = required.replace(
            "/skills/", "/./skills/"
        )
    with pytest.raises((ValueError, RuntimeError)):
        load_installed_premarket_skill_version()


@pytest.mark.parametrize("kind", ("leaf", "ancestor", "root"))
def test_loader_rejects_symlinks_even_when_target_is_inside_installation(installed, kind):
    distribution, _ = installed
    root = distribution.root / PLUGIN_RESOURCE_ROOT
    if kind == "leaf":
        path = root / "skills/premarket-research/SKILL.md"
    elif kind == "ancestor":
        path = root / "skills/premarket-research"
    else:
        path = root
    target = path.with_name(path.name + "-real")
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    with pytest.raises(ValueError, match="symlink"):
        load_installed_premarket_skill_version()


def test_loader_rejects_missing_file_without_source_fallback(installed):
    distribution, _ = installed
    (distribution.root / PLUGIN_RESOURCE_ROOT / "skills/premarket-research/SKILL.md").unlink()
    with pytest.raises((ValueError, RuntimeError)):
        load_installed_premarket_skill_version()


@pytest.mark.parametrize("kind", ("resource", "manifest", "mcp", "simple_resource"))
def test_installed_contract_rejects_unhashed_resources_and_manifest_expansion(installed, kind):
    distribution, contents = installed
    if kind == "resource":
        name = "skills/premarket-research/SKILL.md"
        content = contents[name].replace(
            b"- Conditional: None.",
            b"- Conditional: [extra](references/extra.md) \xe2\x80\x94 Load extra.",
        )
    elif kind == "manifest":
        name = ".codex-plugin/plugin.json"
        manifest = json.loads(contents[name])
        manifest["hooks"] = {}
        content = json.dumps(manifest).encode()
    elif kind == "mcp":
        name = ".mcp.json"
        config = json.loads(contents[name])
        config["mcpServers"]["ai-market-research"]["env"] = {"TOKEN": "secret"}
        content = json.dumps(config).encode()
    else:
        name = "skills/market-regime/SKILL.md"
        content = contents[name].replace(
            b"- Required: None.", b"- Required: [extra](references/extra.md)"
        )
    (distribution.root / PLUGIN_RESOURCE_ROOT / name).write_bytes(content)
    with pytest.raises(ValueError):
        load_installed_premarket_skill_version()


def test_distribution_unavailable_or_metadata_absent_fails_closed(monkeypatch):
    class Missing:
        files = None

        def read_text(self, name):
            return None

    monkeypatch.setattr(skill_bundle, "distribution", lambda name: Missing())
    with pytest.raises(RuntimeError):
        load_installed_premarket_skill_version()


def test_fixed_resource_allowlist_has_three_skills_and_one_manifest():
    assert PLUGIN_RESOURCE_PATHS == tuple(_contents())


@pytest.mark.parametrize("kind", ("base", "ancestor"))
def test_installed_distribution_base_and_ancestors_cannot_be_symlinks(tmp_path, monkeypatch, kind):
    root = tmp_path / "installation" / "site"
    distribution = InstalledDistribution(root, _contents())
    path = root if kind == "base" else root.parent
    target = path.with_name(path.name + "-real")
    path.rename(target)
    path.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(skill_bundle, "distribution", lambda name: distribution)
    with pytest.raises(ValueError, match="symlink"):
        load_installed_premarket_skill_version()


@pytest.mark.parametrize(
    "tamper",
    (
        "missing_frontmatter",
        "wrong_name",
        "missing_section",
        "duplicate_section",
        "empty_skill",
        "malformed_yaml",
        "invalid_workflow_utf8",
        "extra_workflow_field",
        "duplicate_workflow_key",
        "nonstring_workflow_key",
        "boolean_schema",
        "float_repair_limit",
        "unknown_operation",
        "watchlist_alias",
    ),
)
def test_installed_bytes_must_validate_skill_and_workflow_contracts_before_hashing(
    installed, tamper
):
    distribution, contents = installed
    name = "skills/premarket-research/SKILL.md"
    content = contents[name]
    if tamper == "missing_frontmatter":
        content = content.split(b"\n---\n", 1)[-1]
    elif tamper == "wrong_name":
        content = content.replace(b"name: premarket-research", b"name: wrong-skill")
    elif tamper == "missing_section":
        content = content.replace(b"## Fail-Closed Behavior", b"Failure behavior")
    elif tamper == "duplicate_section":
        content += b"\n## Allowed Operations\n"
    elif tamper == "empty_skill":
        content = b""
    elif tamper == "watchlist_alias":
        name = "skills/watchlist-management/SKILL.md"
        content = contents[name].replace(b"upsert_watchlist_item", b"upsert_watchlist")
    else:
        name = "skills/premarket-research/references/workflow-contract.yaml"
        content = contents[name]
        if tamper == "malformed_yaml":
            content = b"schema_version: [unfinished\n"
        elif tamper == "invalid_workflow_utf8":
            content = b"\xff"
        elif tamper == "extra_workflow_field":
            content += b"\nprovider: alpaca\n"
        elif tamper == "duplicate_workflow_key":
            content += b"\nschema_version: 1\n"
        elif tamper == "nonstring_workflow_key":
            content += b"\n[]: bad\n"
        elif tamper == "boolean_schema":
            content = content.replace(b"schema_version: 1", b"schema_version: true")
        elif tamper == "float_repair_limit":
            content = content.replace(b"max_repairs: 2", b"max_repairs: 2.0")
        elif tamper == "unknown_operation":
            content = content.replace(
                b"operation: prepare_premarket_run", b"operation: get_positions"
            )
    (distribution.root / PLUGIN_RESOURCE_ROOT / name).write_bytes(content)
    with pytest.raises(ValueError):
        load_installed_premarket_skill_version()
