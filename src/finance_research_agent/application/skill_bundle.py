"""Exact byte provenance for the fixed installed Product A skill bundle."""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Mapping
from hashlib import sha256
from importlib.metadata import PackagePath, distribution
from pathlib import Path, PurePosixPath

_RESOURCE_LINE = re.compile(r"- (?:Required|Conditional): \[[^\]]+\]\(([^)]+)\)(?: — .+)?")


def validate_logical_path(path: str) -> str:
    """Reject unsafe raw spellings before a path class can normalize them."""
    if (
        not isinstance(path, str)
        or not path
        or "\\" in path
        or "\x00" in path
        or re.match(r"^[A-Za-z]:", path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError("unsafe logical path")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("unsafe logical path") from exc
    return path


def compute_skill_version(
    files: Mapping[str, bytes] | Iterable[tuple[str, bytes]],
) -> str:
    """Hash unique raw logical paths and exact bytes using uint64 framing."""
    entries = files.items() if isinstance(files, Mapping) else files
    validated: dict[str, bytes] = {}
    for raw_path, content in entries:
        path = validate_logical_path(raw_path)
        if path in validated:
            raise ValueError("duplicate logical path")
        if not isinstance(content, bytes):
            raise TypeError("skill content must be bytes")
        validated[path] = content
    if "SKILL.md" not in validated:
        raise ValueError("SKILL.md is required")
    digest = sha256()
    for path in sorted(validated, key=lambda value: value.encode("utf-8")):
        path_bytes = path.encode("utf-8")
        content = validated[path]
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def declared_skill_resources(skill_bytes: bytes) -> tuple[str, ...]:
    """Read required and conditional declarations from the loading section."""
    text = skill_bytes.decode("utf-8")
    sections = text.split("## Resource Loading\n")
    if len(sections) != 2:
        raise ValueError("skill must have one Resource Loading section")
    section = sections[1].split("\n## ", 1)[0]
    resources: set[str] = set()
    for line in section.splitlines():
        if not line.startswith(("- Required:", "- Conditional:")):
            continue
        if line in {"- Required: None.", "- Conditional: None."}:
            continue
        match = _RESOURCE_LINE.fullmatch(line)
        if match is None:
            raise ValueError("malformed resource declaration")
        path = validate_logical_path(match.group(1))
        if path in resources or path == "SKILL.md":
            raise ValueError("duplicate logical path")
        resources.add(path)
    return tuple(sorted(resources, key=lambda value: value.encode("utf-8")))


PLUGIN_RESOURCE_ROOT = "finance_research_agent/data/product_a_plugin"
PLUGIN_RESOURCE_PATHS = (
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "skills/market-regime/SKILL.md",
    "skills/watchlist-management/SKILL.md",
    "skills/premarket-research/SKILL.md",
    "skills/premarket-research/references/workflow-contract.yaml",
)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in installed plugin contract")
        result[key] = value
    return result


def _installed_resources() -> dict[str, bytes]:
    installed = distribution("finance-research-agent")
    entries = installed.files
    record = installed.read_text("RECORD")
    if entries is None or record is None:
        raise RuntimeError("installed distribution file metadata is unavailable")
    required = {PLUGIN_RESOURCE_ROOT + "/" + path for path in PLUGIN_RESOURCE_PATHS}
    recorded: list[str] = []
    for row in csv.reader(io.StringIO(record)):
        if not row:
            continue
        raw = row[0]
        normalized = PurePosixPath(raw).as_posix()
        if normalized.startswith(PLUGIN_RESOURCE_ROOT + "/"):
            validate_logical_path(raw)
            if normalized != raw or raw not in required:
                raise ValueError("unexpected or noncanonical installed plugin resource")
            recorded.append(raw)
    if len(recorded) != len(required) or set(recorded) != required:
        raise RuntimeError("installed plugin resources are missing or duplicated")
    found: dict[str, PackagePath] = {}
    for entry in entries:
        name = entry.as_posix()
        if name.startswith(PLUGIN_RESOURCE_ROOT + "/"):
            if name not in required or name in found:
                raise ValueError("unexpected or duplicate installed plugin resource")
            found[name] = entry
    if set(found) != required:
        raise RuntimeError("installed plugin resources are missing")
    base = Path(str(installed.locate_file("")))
    contents: dict[str, bytes] = {}
    for relative in PLUGIN_RESOURCE_PATHS:
        full = PLUGIN_RESOURCE_ROOT + "/" + relative
        current = base
        for part in full.split("/"):
            current = current / part
            if current.is_symlink():
                raise ValueError("installed plugin resource contains a symlink")
        path = Path(str(found[full].locate()))
        if path != current or not path.is_file():
            raise ValueError("installed plugin resource is not a regular confined file")
        skill_root = base / PLUGIN_RESOURCE_ROOT
        if not path.resolve().is_relative_to(skill_root.resolve()):
            raise ValueError("installed plugin resource escapes its root")
        contents[relative] = path.read_bytes()
    return contents


def _validated_plugin_version(contents: Mapping[str, bytes]) -> str:
    manifest = json.loads(
        contents[".codex-plugin/plugin.json"], object_pairs_hook=_unique_json_object
    )
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"name", "version", "description", "skills", "mcpServers", "interface"}
        or manifest["name"] != "ai-market-research-agent"
        or manifest["version"] != "0.1.0"
        or manifest["skills"] != "./skills/"
        or manifest["mcpServers"] != "./.mcp.json"
        or not isinstance(manifest["description"], str)
        or not manifest["description"].strip()
        or not isinstance(manifest["interface"], dict)
    ):
        raise ValueError("invalid installed Product A plugin manifest")
    mcp = json.loads(contents[".mcp.json"], object_pairs_hook=_unique_json_object)
    if mcp != {
        "mcpServers": {"ai-market-research": {"command": "ai-market-research-mcp", "args": []}}
    }:
        raise ValueError("invalid installed Product A MCP contract")
    return "0.1.0"


def load_installed_skill_versions() -> tuple[str, str]:
    """Read one fixed installed bundle and return its skill and plugin versions."""
    contents = _installed_resources()
    plugin_version = _validated_plugin_version(contents)
    for name in ("market-regime", "watchlist-management"):
        if declared_skill_resources(contents[f"skills/{name}/SKILL.md"]):
            raise ValueError("simple Product A skills must not declare resources")
    skill = contents["skills/premarket-research/SKILL.md"]
    resources = declared_skill_resources(skill)
    if resources != ("references/workflow-contract.yaml",):
        raise ValueError("premarket skill has an unexpected declared resource")
    return compute_skill_version(
        {
            "SKILL.md": skill,
            **{path: contents["skills/premarket-research/" + path] for path in resources},
        }
    ), plugin_version


def load_installed_premarket_skill_version() -> str:
    """Hash the fixed installed premarket contract; never read editable source."""
    return load_installed_skill_versions()[0]


def load_installed_plugin_version() -> str:
    """Return the independently declared installed plugin version."""
    return load_installed_skill_versions()[1]
