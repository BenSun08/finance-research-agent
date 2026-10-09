"""Validate the bounded Product A skill contracts without executing workflows."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from finance_research_agent.application.operations import OPERATION_NAMES
from finance_research_agent.application.skill_bundle import validate_logical_path

REQUIRED_SKILL_SECTIONS = (
    "Purpose and Trigger", "Accepted Inputs and Authority", "Allowed Operations",
    "Output Obligations", "Fail-Closed Behavior", "Resource Loading",
    "Safety and Forbidden Behavior",
)


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of accepting the last value."""


def _unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError("duplicate YAML key")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


@dataclass(frozen=True)
class Skill:
    path: Path
    frontmatter: dict[str, str]
    body: str


def parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("skill frontmatter is required")
    header, body = text[4:].split("\n---\n", 1)
    metadata = yaml.load(header, Loader=UniqueLoader)
    if (
        not isinstance(metadata, dict)
        or set(metadata) != {"name", "description"}
        or any(not isinstance(value, str) or not value.strip() for value in metadata.values())
        or metadata["name"] != path.parent.name
    ):
        raise ValueError("invalid skill frontmatter")
    headings = re.findall(r"^## (.+)$", body, flags=re.MULTILINE)
    if tuple(headings) != REQUIRED_SKILL_SECTIONS:
        raise ValueError("skill sections must occur exactly once in the required order")
    return Skill(path, metadata, body)


def declared_resources(skill: Skill) -> tuple[PurePosixPath, ...]:
    section = skill.body.split("## Resource Loading\n", 1)[1].split("\n## ", 1)[0]
    resources = set()
    categories = set()
    for line in section.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"- (Required|Conditional): (.+)", line)
        if match is None:
            raise ValueError("invalid resource declaration")
        category, declaration = match.groups()
        categories.add(category)
        if declaration == "None.":
            continue
        link = re.fullmatch(r"\[[^\]]+\]\(([^)]*)\)(?: — .+)?", declaration)
        if link is None or (category == "Conditional" and " — " not in declaration):
            raise ValueError("invalid resource link or missing load condition")
        raw = validate_logical_path(link.group(1))
        if raw == "SKILL.md" or raw in resources:
            raise ValueError("duplicate skill resource")
        resources.add(raw)
    if categories != {"Required", "Conditional"}:
        raise ValueError("both resource categories must be explicit")
    return tuple(PurePosixPath(value) for value in sorted(resources))


def _regular_confined(root: Path, path: Path) -> None:
    if root.is_symlink() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("symlink resource is forbidden")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("missing or unconfined resource")


def validate_skill_contracts(root: Path) -> tuple[str, ...]:
    errors = []
    for path in sorted((root / "skills").glob("*/SKILL.md")):
        try:
            _regular_confined(root, path)
            skill = parse_skill(path)
            for resource in declared_resources(skill):
                _regular_confined(path.parent, path.parent / resource)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
    return tuple(errors)


_STEPS = (
    ("system_status", "mcp_tool", "get_system_status", [], ["system_status"], "blocked"),
    ("validate_configuration", "mcp_tool", "validate_configuration", ["system_status"],
     ["configuration_validation"], "blocked"),
    ("prepare_run", "mcp_tool", "prepare_premarket_run", ["configuration_validation"],
     ["run_state", "research_packet"], "blocked"),
    ("synthesize_draft", "codex_synthesis", "research_brief_draft", ["research_packet"],
     ["research_brief_draft"], "publish_reduced_report"),
    ("validate_and_publish", "mcp_tool", "validate_and_publish_brief",
     ["research_packet", "research_brief_draft"], ["validated_publication"],
     "repair_then_publish_reduced"),
    ("publish_reduced", "mcp_tool", "publish_reduced_report", ["run_state"],
     ["reduced_publication"], "blocked"),
    ("read_report", "mcp_tool", "get_report", ["run_state"], ["report"], "blocked"),
)


def validate_workflow(path: Path) -> tuple[str, ...]:
    try:
        contract = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
        expected = {
            "schema_version": 1, "id": "premarket-research",
            "steps": [dict(zip(
                ("id", "kind", "operation", "consumes", "produces", "on_failure"), step,
                strict=True,
            )) for step in _STEPS],
            "repair": {"max_repairs": 2, "max_validations": 3,
                       "research_packet": "same_frozen_packet"},
            "invariants": ["no_new_evidence_after_cutoff", "no_numeric_recalculation_in_synthesis",
                           "no_policy_mutation", "no_tool_discovery", "no_brokerage_or_execution"],
            "terminal_outcomes": ["published", "deterministic_reduced", "blocked"],
        }
        if not isinstance(contract, dict) or contract != expected:
            raise ValueError("workflow differs from the approved version 1 contract")
        if type(contract["schema_version"]) is not int or any(
            type(contract["repair"][key]) is not int for key in ("max_repairs", "max_validations")
        ):
            raise ValueError("workflow limits and schema version require integer types")
        if any(step[2] not in OPERATION_NAMES for step in _STEPS if step[1] == "mcp_tool"):
            raise ValueError("workflow references an unavailable typed operation")
    except (ValueError, OSError, yaml.YAMLError) as exc:
        return (f"{path.name}: {exc}",)
    return ()


def validate_product_a_contracts(root: Path) -> tuple[str, ...]:
    errors = list(validate_skill_contracts(root))
    if {path.parent.name for path in (root / "skills").glob("*/SKILL.md")} != {
        "market-regime", "premarket-research", "watchlist-management",
    }:
        errors.append("Product A requires exactly three canonical skills")
    manifest = root / "skills/premarket-research/references/workflow-contract.yaml"
    if set((root / "skills").rglob("*.yaml")) != {manifest}:
        errors.append("Product A requires exactly one adjacent workflow manifest")
    errors.extend(validate_workflow(manifest))
    return tuple(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = validate_product_a_contracts(args.root)
    for error in errors:
        print(error)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
