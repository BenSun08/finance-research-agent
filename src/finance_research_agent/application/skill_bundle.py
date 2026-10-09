"""Exact byte provenance for the fixed installed Product A skill bundle."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from hashlib import sha256

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
        match = _RESOURCE_LINE.fullmatch(line)
        if match is None:
            raise ValueError("malformed resource declaration")
        path = validate_logical_path(match.group(1))
        if path in resources or path == "SKILL.md":
            raise ValueError("duplicate logical path")
        resources.add(path)
    return tuple(sorted(resources, key=lambda value: value.encode("utf-8")))
