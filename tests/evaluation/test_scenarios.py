import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from finance_research_agent.domain.enums import DataQualityStatus, PlanStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.regime import Regime
from finance_research_agent.evaluation.models import (
    CandidateRankingAssertion,
    CandidateScoreAssertion,
    DomainAssertion,
    EventRiskAssertion,
    InstrumentEligibilityAssertion,
    PlanBuildAssertion,
    PlanExpiryAssertion,
    PositionSizingAssertion,
    PriorObservationAssertion,
    RegimeCalculationAssertion,
    RunWindowAssertion,
    SetupDetectionAssertion,
)
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


def test_baseline_current_scope_expectations_match_the_recorded_service_projection() -> None:
    scenarios = load_evaluation_scenarios()
    baseline = scenarios[0].current_scope_expectation.primary
    baseline_ids = {
        *(f"S{index:02d}" for index in range(1, 5)),
        *(f"S{index:02d}" for index in range(6, 22)),
    }

    for scenario in scenarios:
        if scenario.id.value not in baseline_ids:
            continue
        current = scenario.current_scope_expectation.primary
        assert current == baseline.model_copy(update={"case_id": current.case_id})

    # The legacy Task 18 expectation remains the full-source reference.
    s02 = scenarios[1]
    assert s02.expected_capabilities != s02.current_scope_expectation.primary.capabilities
    assert s02.expected_plan_states


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


def test_manifest_domain_assertions_use_closed_typed_variants() -> None:
    scenarios = load_evaluation_scenarios()
    assertions = tuple(
        assertion for scenario in scenarios for assertion in scenario.domain_assertions
    )

    assert {type(assertion) for assertion in assertions} == {
        CandidateRankingAssertion,
        CandidateScoreAssertion,
        EventRiskAssertion,
        InstrumentEligibilityAssertion,
        PlanBuildAssertion,
        PlanExpiryAssertion,
        PositionSizingAssertion,
        PriorObservationAssertion,
        RegimeCalculationAssertion,
        RunWindowAssertion,
        SetupDetectionAssertion,
    }
    breakout_score = scenarios[0].domain_assertions[0].expected_fields.total_score
    assert breakout_score == Decimal("79.65965732087227414330218068")
    unknown_regime = scenarios[4].domain_assertions[0].expected_fields.regime
    assert unknown_regime is Regime.UNKNOWN
    invalid_instrument = scenarios[13].domain_assertions[0].expected_fields.reason_codes
    assert invalid_instrument == ("UNSUPPORTED_INSTRUMENT",)
    event_risk = scenarios[6].domain_assertions[0].expected_fields
    assert event_risk.plan_status is PlanStatus.BLOCKED
    assert not hasattr(event_risk, "event_verified")
    source_conflict = scenarios[8].domain_assertions[0].expected_fields
    assert source_conflict.gate_reason_codes == (
        ErrorCode.SOURCE_CONFLICT,
        ErrorCode.UNSUPPORTED_INSTRUMENT,
    )
    prior_observation = scenarios[20].domain_assertions[0].expected_fields
    assert tuple(outcome.value for outcome in prior_observation.outcomes) == (
        "ENTRY_ZONE_OBSERVED",
        "AMBIGUOUS_SEQUENCE",
    )


def test_domain_assertion_rejects_fixture_from_another_operation() -> None:
    adapter = TypeAdapter(DomainAssertion)

    with pytest.raises(ValidationError, match="fixture_id"):
        adapter.validate_json(
            json.dumps({
                "kind": "CANDIDATE_SCORE",
                "fixture_id": "S02_PULLBACK",
                "expected_fields": {
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "total_score": 70,
                    "plan_status": "DRAFT",
                },
            })
        )


def test_domain_assertion_rejects_unknown_or_wrongly_typed_outputs() -> None:
    adapter = TypeAdapter(DomainAssertion)
    base = {
        "kind": "REGIME_CALCULATION",
        "fixture_id": "S04_DEFENSIVE_REGIME",
        "expected_fields": {
            "regime": "defensive",
            "score": "42.5",
            "unavailable_reasons": [],
        },
    }
    assert adapter.validate_json(json.dumps(base)).expected_fields.score == 42.5

    with pytest.raises(ValidationError, match="untrusted_output"):
        adapter.validate_json(
            json.dumps(
                {**base, "expected_fields": {**base["expected_fields"], "untrusted_output": True}}
            )
        )
    with pytest.raises(ValidationError):
        adapter.validate_json(
            json.dumps(
                {**base, "expected_fields": {**base["expected_fields"], "score": "not-a-number"}}
            )
        )
