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
        "skills/market-regime/SKILL.md": b"## Resource Loading\nNo resources.\n",
        "skills/watchlist-management/SKILL.md": b"## Resource Loading\nNo resources.\n",
        "skills/premarket-research/SKILL.md": (
            b"## Resource Loading\n- Required: [Workflow](references/workflow-contract.yaml)\n"
        ),
        "skills/premarket-research/references/workflow-contract.yaml": b"schema_version: 1\n",
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
        content = contents[name] + b"- Conditional: [extra](references/extra.md)\n"
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
        content = contents[name] + b"- Required: [extra](references/extra.md)\n"
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
