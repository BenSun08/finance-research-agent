"""Strict loading for the immutable Product A scenario manifest."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import model_validator

from finance_research_agent.domain.models import StrictModel
from finance_research_agent.evaluation.models import (
    SCENARIO_IDS,
    EvaluationScenario,
)

DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "evals/scenarios/v0.1-scenarios.yaml"
_MAX_MANIFEST_BYTES = 1_000_000


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[str, object]:
    loader.flatten_mapping(node)
    mapping: dict[str, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError("evaluation manifest mapping keys must be strings")
        if key in mapping:
            raise ValueError(f"duplicate YAML key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class _ScenarioManifest(StrictModel):
    scenarios: tuple[EvaluationScenario, ...]

    @model_validator(mode="after")
    def _fixed_complete_order(self) -> _ScenarioManifest:
        scenario_ids = tuple(scenario.id for scenario in self.scenarios)
        if scenario_ids != SCENARIO_IDS:
            raise ValueError("scenario manifest must contain S01 through S25 in order")
        titles = tuple(scenario.title for scenario in self.scenarios)
        if len(titles) != len(set(titles)):
            raise ValueError("scenario titles must be unique")
        fixture_sets = tuple(scenario.fixture_set for scenario in self.scenarios)
        if len(fixture_sets) != len(set(fixture_sets)):
            raise ValueError("scenario fixture_set ids must be unique")
        return self


def load_evaluation_scenarios(
    path: Path = DEFAULT_MANIFEST,
) -> tuple[EvaluationScenario, ...]:
    """Load the one strict S01-S25 manifest without accepting duplicate keys."""

    payload_bytes = path.read_bytes()
    if len(payload_bytes) > _MAX_MANIFEST_BYTES:
        raise ValueError("evaluation manifest exceeds the one-megabyte limit")
    text = payload_bytes.decode("utf-8", errors="strict")
    try:
        payload = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ValueError("invalid evaluation manifest YAML") from error
    if not isinstance(payload, dict):
        raise ValueError("evaluation manifest root must be a mapping")
    if set(payload) != {"schema_version", "scenarios"}:
        raise ValueError("evaluation manifest must contain only schema_version and scenarios")
    if payload["schema_version"] != "0.1":
        raise ValueError("evaluation manifest schema_version must be 0.1")
    try:
        encoded = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError(
            "evaluation manifest must contain only strict JSON-compatible values"
        ) from error
    manifest = _ScenarioManifest.model_validate_json(encoded)
    return manifest.scenarios
