"""Pure byte validation for the fixed Product A skill and workflow contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

import yaml
from yaml.nodes import MappingNode

from finance_research_agent.application.operations import OPERATION_NAMES
from finance_research_agent.application.skill_bundle import declared_skill_resources

REQUIRED_SKILL_SECTIONS = (
    "Purpose and Trigger",
    "Accepted Inputs and Authority",
    "Allowed Operations",
    "Output Obligations",
    "Fail-Closed Behavior",
    "Resource Loading",
    "Safety and Forbidden Behavior",
)
_SKILL_NAMES = frozenset({"market-regime", "premarket-research", "watchlist-management"})


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate or nonstring YAML keys instead of accepting coercion."""


def _unique_mapping(loader: yaml.SafeLoader, node: MappingNode) -> dict[str, object]:
    result: dict[str, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise ValueError("YAML contract keys must be strings")
        if key in result:
            raise ValueError("duplicate YAML key")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _unique_yaml(content: str) -> object:
    try:
        return yaml.load(content, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError("invalid YAML contract") from exc


@dataclass(frozen=True)
class ParsedSkill:
    """Validated discovery metadata, body and raw declared resource paths."""

    frontmatter: dict[str, str]
    body: str
    resources: tuple[str, ...]


def parse_skill_bytes(content: bytes, expected_name: str) -> ParsedSkill:
    """Validate one canonical skill without consulting the filesystem."""
    text = content.decode("utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("skill frontmatter is required")
    header, body = text[4:].split("\n---\n", 1)
    metadata = _unique_yaml(header)
    if (
        expected_name not in _SKILL_NAMES
        or not isinstance(metadata, dict)
        or set(metadata) != {"name", "description"}
        or any(not isinstance(value, str) or not value.strip() for value in metadata.values())
        or metadata["name"] != expected_name
    ):
        raise ValueError("invalid skill frontmatter")
    headings = re.findall(r"^## (.+)$", body, flags=re.MULTILINE)
    if tuple(headings) != REQUIRED_SKILL_SECTIONS:
        raise ValueError("skill sections must occur exactly once in the required order")
    if expected_name == "watchlist-management":
        allowed = body.split("## Allowed Operations\n", 1)[1].split("\n## ", 1)[0]
        operations = set(re.findall(r"`([a-z][a-z_]+)`", allowed)) - {"expected_version"}
        expected = {"list_watchlist", "upsert_watchlist_item", "remove_watchlist_item"}
        if operations != expected or not expected.issubset(OPERATION_NAMES):
            raise ValueError("watchlist skill must name the exact typed operations")
    return ParsedSkill(
        frontmatter=cast(dict[str, str], metadata),
        body=body,
        resources=declared_skill_resources(content),
    )


_STEPS: tuple[tuple[str, str, str, list[str], list[str], str], ...] = (
    ("system_status", "mcp_tool", "get_system_status", [], ["system_status"], "blocked"),
    (
        "validate_configuration",
        "mcp_tool",
        "validate_configuration",
        ["system_status"],
        ["configuration_validation"],
        "blocked",
    ),
    (
        "prepare_run",
        "mcp_tool",
        "prepare_premarket_run",
        ["configuration_validation"],
        ["run_state", "research_packet"],
        "blocked",
    ),
    (
        "synthesize_draft",
        "codex_synthesis",
        "research_brief_draft",
        ["research_packet"],
        ["research_brief_draft"],
        "publish_reduced_report",
    ),
    (
        "validate_and_publish",
        "mcp_tool",
        "validate_and_publish_brief",
        ["research_packet", "research_brief_draft"],
        ["validated_publication"],
        "repair_then_publish_reduced",
    ),
    (
        "publish_reduced",
        "mcp_tool",
        "publish_reduced_report",
        ["run_state"],
        ["reduced_publication"],
        "blocked",
    ),
    ("read_report", "mcp_tool", "get_report", ["run_state"], ["report"], "blocked"),
)


def validate_workflow_bytes(content: bytes) -> None:
    """Require the approved exact v1 workflow and strict integer repair bounds."""
    contract = _unique_yaml(content.decode("utf-8"))
    expected = {
        "schema_version": 1,
        "id": "premarket-research",
        "steps": [
            dict(
                zip(
                    ("id", "kind", "operation", "consumes", "produces", "on_failure"),
                    step,
                    strict=True,
                )
            )
            for step in _STEPS
        ],
        "repair": {
            "max_repairs": 2,
            "max_validations": 3,
            "research_packet": "same_frozen_packet",
        },
        "invariants": [
            "no_new_evidence_after_cutoff",
            "no_numeric_recalculation_in_synthesis",
            "no_policy_mutation",
            "no_tool_discovery",
            "no_brokerage_or_execution",
        ],
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
