from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from finance_research_agent.domain.enums import ReducedReportReason
from finance_research_agent.domain.regime import RegimePolicy
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.evaluation import (
    EvaluationHarness,
    EvaluationOutcome,
    execute_evaluation_scenario,
)
from finance_research_agent.evaluation.domain_assertions import (
    CandidateScoreFixture,
    DomainFixtureBank,
)
from finance_research_agent.evaluation.harness import execute_current_scope_scenario
from finance_research_agent.evaluation.models import (
    SCENARIO_IDS,
    CurrentScopeExpectation,
    CurrentScopeServiceObservation,
    DomainAssertion,
    ScenarioAssertionId,
)
from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
from tests.application.test_premarket_preparation import MarketData, dependencies
from tests.unit import test_setups as setup_fixtures


def test_current_scope_harness_runs_real_services_and_verifies_frozen_replay(
    tmp_path: Path,
) -> None:
    scenario = load_evaluation_scenarios()[0]
    assert scenario.id is SCENARIO_IDS[0]
    harness = EvaluationHarness(
        dependencies_factory=lambda scenario, expectation: dependencies(tmp_path),
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


def _evaluation_harness(tmp_path: Path, assertion) -> EvaluationHarness:
    context = setup_fixtures._load_context("valid-breakout.json")
    fixture = CandidateScoreFixture(
        setup=setup_fixtures.detect_setups(**context)[0],
        setup_policy=context["setup_policy"],
        eligibility_gates=context["eligibility_gates"],
        event_assessment=context["event_assessment"],
        data_quality=context["data_quality"],
        regime_policy_version=RegimePolicy().version,
    )
    return EvaluationHarness(
        dependencies_factory=lambda scenario, expectation: dependencies(tmp_path),
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
        domain_fixtures=DomainFixtureBank({assertion.fixture_id: fixture}),
    )


def test_evaluation_runner_records_current_scope_and_domain_results(tmp_path: Path) -> None:
    scenario = load_evaluation_scenarios()[0]
    assertion = scenario.domain_assertions[0]

    outcome = execute_evaluation_scenario(
        scenario, _evaluation_harness(tmp_path, assertion)
    )

    assert isinstance(outcome, EvaluationOutcome)
    assert outcome.scenario_id is scenario.id
    assert len(outcome.current_scope_outcomes) == 1
    assert outcome.current_scope_outcomes[0].current_scope == (
        scenario.current_scope_expectation.primary
    )
    assert outcome.domain_assertion_outcomes[0].status == "PASS"
    assert outcome.assertions_passed == (
        ScenarioAssertionId.CURRENT_SCOPE_MATCHES,
        ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS,
        ScenarioAssertionId.SOURCE_LIMITATIONS_ADJACENT,
    )
    assert outcome.assertions_failed == ()
    assert outcome.assertions_pending == ()
    assert outcome.artifact_hashes["PRIMARY.published_bundle"]
    assert outcome.current_scope_outcomes[0].source_limitations_adjacent is True


def test_current_scope_harness_checks_watchlist_exclusions_in_rendered_report(
    tmp_path: Path,
) -> None:
    scenario = load_evaluation_scenarios()[2]
    harness = EvaluationHarness(
        dependencies_factory=lambda current_scenario, expectation: dependencies(tmp_path),
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
    )

    observation = execute_current_scope_scenario(scenario, harness)

    assert observation.watchlist_exclusions_visible is True


def test_s05_unknown_regime_current_scope_projection_uses_missing_broad_evidence(
    tmp_path: Path,
) -> None:
    from finance_research_agent.domain.errors import ErrorCode
    from finance_research_agent.domain.models import ProviderFailure

    class MissingBroadData(MarketData):
        def fetch_daily_bars(self, symbols, start, end, **kwargs):
            outcomes = dict(super().fetch_daily_bars(symbols, start, end, **kwargs))
            outcomes["SPY"] = ProviderFailure(
                provider="alpaca",
                symbol="SPY",
                error_code=ErrorCode.PROVIDER_NO_DATA,
                retryable=False,
            )
            return outcomes

    scenario = load_evaluation_scenarios()[4]
    deps = dependencies(tmp_path, market=MissingBroadData())
    harness = EvaluationHarness(
        dependencies_factory=lambda current_scenario, expectation: deps,
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
    )

    observation = execute_current_scope_scenario(scenario, harness)

    assert observation.current_scope == scenario.current_scope_expectation.primary
    assert observation.source_limitations_adjacent is True
    assert observation.watchlist_exclusions_visible is True
    run_id = deps.run_repository.get_latest(date(2026, 9, 28))
    assert run_id is not None
    report = deps.run_repository.get_report(run_id)
    assert report is not None
    assert "Regime classification is UNKNOWN" in report
    assert "PROVIDER_NO_DATA" in report


@pytest.mark.parametrize(
    ("scenario_index", "assertion", "evidence_field"),
    [
        (0, ScenarioAssertionId.SOURCE_LIMITATIONS_ADJACENT, "source_limitations_adjacent"),
        (2, ScenarioAssertionId.ALL_WATCHLIST_EXCLUSIONS_VISIBLE, "watchlist_exclusions_visible"),
    ],
)
def test_evaluation_runner_fails_report_assertion_without_observed_report_evidence(
    monkeypatch: pytest.MonkeyPatch,
    scenario_index: int,
    assertion: ScenarioAssertionId,
    evidence_field: str,
) -> None:
    scenario = load_evaluation_scenarios()[scenario_index].model_copy(
        update={"assertions": (assertion,), "domain_assertions": ()}
    )
    expected = scenario.current_scope_expectation.primary
    observed = expected.model_copy(update={"case_id": expected.case_id})
    observation = CurrentScopeServiceObservation(
        scenario_id=scenario.id,
        current_scope=observed,
        artifact_hashes=FrozenMap({}),
        replay_json_matches=True,
        replay_markdown_matches=True,
        source_limitations_adjacent=evidence_field != "source_limitations_adjacent",
        watchlist_exclusions_visible=evidence_field != "watchlist_exclusions_visible",
    )

    monkeypatch.setattr(
        "finance_research_agent.evaluation.harness.execute_current_scope_scenario",
        lambda current_scenario, harness, expectation: observation,
    )
    harness = EvaluationHarness(
        dependencies_factory=lambda current_scenario, expectation: pytest.fail(
            "report assertion tests use the recorded observation"
        ),
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
    )

    outcome = execute_evaluation_scenario(scenario, harness)

    assert outcome.assertions_failed == (assertion,)
    assert outcome.assertions_pending == ()


def test_evaluation_runner_records_mismatched_domain_assertion(tmp_path: Path) -> None:
    scenario = load_evaluation_scenarios()[0]
    original = scenario.domain_assertions[0]
    mismatched = TypeAdapter(DomainAssertion).validate_python(
        original.model_dump()
        | {"expected_fields": {"total_score": Decimal(70)}}
    )
    scenario = scenario.model_copy(update={"domain_assertions": (mismatched,)})

    outcome = execute_evaluation_scenario(
        scenario, _evaluation_harness(tmp_path, mismatched)
    )

    assert outcome.domain_assertion_outcomes[0].status == "FAIL"
    assert outcome.assertions_failed == (ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS,)


def test_evaluation_runner_requires_domain_fixtures_before_running_services(
    tmp_path: Path,
) -> None:
    scenario = load_evaluation_scenarios()[0]
    harness = EvaluationHarness(
        dependencies_factory=lambda current_scenario, expectation: pytest.fail(
            "missing domain fixtures must fail before service execution"
        ),
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
    )

    with pytest.raises(ValueError, match="explicit DomainFixtureBank"):
        execute_evaluation_scenario(scenario, harness)


def test_evaluation_runner_executes_every_declared_case_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_evaluation_scenarios()[0]
    primary = scenario.current_scope_expectation.primary
    subcase = primary.model_copy(update={"case_id": "SECOND_CASE"})
    scenario = scenario.model_copy(
        update={
            "assertions": (ScenarioAssertionId.CURRENT_SCOPE_MATCHES,),
            "current_scope_expectation": CurrentScopeExpectation(
                primary=primary,
                subcases=(subcase,),
            ),
            "domain_assertions": (),
        }
    )
    observed_cases = []

    def execute_case(current_scenario, harness, expectation):
        observed_cases.append(expectation.case_id)
        observed = (
            expectation
            if expectation.case_id != "SECOND_CASE"
            else expectation.model_copy(update={"recoverability": False})
        )
        return CurrentScopeServiceObservation(
            scenario_id=current_scenario.id,
            current_scope=observed,
            artifact_hashes=FrozenMap({}),
            replay_json_matches=True,
            replay_markdown_matches=True,
            source_limitations_adjacent=True,
            watchlist_exclusions_visible=True,
        )

    monkeypatch.setattr(
        "finance_research_agent.evaluation.harness.execute_current_scope_scenario",
        execute_case,
    )
    harness = EvaluationHarness(
        dependencies_factory=lambda current_scenario, expectation: pytest.fail(
            "case runner is replaced by the isolated observation stub"
        ),
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
    )

    outcome = execute_evaluation_scenario(scenario, harness)

    assert observed_cases == ["PRIMARY", "SECOND_CASE"]
    assert outcome.assertions_failed == (ScenarioAssertionId.CURRENT_SCOPE_MATCHES,)
    assert len(outcome.current_scope_outcomes) == 2
    assert dict(outcome.artifact_hashes) == {}
