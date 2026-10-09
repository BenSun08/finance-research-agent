from datetime import date
from pathlib import Path

from finance_research_agent.domain.enums import ReducedReportReason
from finance_research_agent.evaluation.harness import (
    EvaluationHarness,
    execute_current_scope_scenario,
)
from finance_research_agent.evaluation.models import SCENARIO_IDS
from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
from tests.application.test_premarket_preparation import dependencies


def test_current_scope_harness_runs_real_services_and_verifies_frozen_replay(
    tmp_path: Path,
) -> None:
    scenario = load_evaluation_scenarios()[0]
    assert scenario.id is SCENARIO_IDS[0]
    harness = EvaluationHarness(
        dependencies_factory=lambda: dependencies(tmp_path),
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
    )

    outcome = execute_current_scope_scenario(scenario, harness)

    assert outcome.scenario_id is scenario.id
    assert outcome.current_scope == scenario.current_scope_expectation.primary
    assert outcome.replay_json_matches is True
    assert outcome.replay_markdown_matches is True
    assert outcome.artifact_hashes["research_packet"]
    assert outcome.artifact_hashes["published_bundle"]
    assert outcome.artifact_hashes["report_markdown"]
