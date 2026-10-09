from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from finance_research_agent.domain.enums import DataQualityStatus
from finance_research_agent.evaluation.scenarios import (
    DEFAULT_MANIFEST,
    load_evaluation_scenarios,
)

SCENARIO_IDS = tuple(f"S{number:02d}" for number in range(1, 26))


def test_default_manifest_contains_exact_ordered_scenarios_and_scope() -> None:
    scenarios = load_evaluation_scenarios()

    assert tuple(scenario.id for scenario in scenarios) == SCENARIO_IDS
    assert len({scenario.title for scenario in scenarios}) == 25
    assert all(scenario.original_scope_reference for scenario in scenarios)
    assert all(scenario.current_scope_expectation for scenario in scenarios)
    assert all(isinstance(scenario.domain_assertions, tuple) for scenario in scenarios)

    breakout = scenarios[0]
    assert breakout.expected_data_quality_status is DataQualityStatus.PASS
    assert (
        breakout.current_scope_expectation.primary.data_quality_status
        is DataQualityStatus.DEGRADED
    )
    assert breakout.expected_report_banner == "NOT_SPECIFIED_IN_TASK_18"


def test_loader_rejects_duplicate_yaml_keys_before_model_validation(tmp_path: Path) -> None:
    source = DEFAULT_MANIFEST.read_text(encoding="utf-8")
    duplicate = source.replace("- id: S01\n", "- id: S01\n  id: S01\n", 1)
    assert duplicate != source
    path = tmp_path / "duplicate-key.yaml"
    path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate YAML key.*id"):
        load_evaluation_scenarios(path)


def test_loader_rejects_duplicate_scenario_ids(tmp_path: Path) -> None:
    manifest = yaml.safe_load(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    manifest["scenarios"][1]["id"] = "S01"
    path = tmp_path / "duplicate-id.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="S01 through S25 in order"):
        load_evaluation_scenarios(path)


def test_loader_rejects_unknown_scenario_fields(tmp_path: Path) -> None:
    manifest = yaml.safe_load(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    manifest["scenarios"][0]["unreviewed_policy"] = "enabled"
    path = tmp_path / "unknown-field.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValidationError, match="unreviewed_policy"):
        load_evaluation_scenarios(path)
