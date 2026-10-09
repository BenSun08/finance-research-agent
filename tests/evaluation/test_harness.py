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


def test_s05_global_outage_current_scope_observes_operational_publication(
    tmp_path: Path,
) -> None:
    from finance_research_agent.domain.enums import (
        DataQualityStatus,
        DeliveryStatus,
        ExecutionStatus,
    )
    from finance_research_agent.domain.errors import ErrorCode
    from finance_research_agent.domain.models import ProviderFailure

    class GlobalOutage(MarketData):
        def __init__(self) -> None:
            super().__init__(available=False)

        def fetch_instruments(self, symbols, **kwargs):
            self.calls.append("instruments")
            return {
                symbol: ProviderFailure(
                    provider="alpaca",
                    symbol=symbol,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                )
                for symbol in symbols
            }

        def fetch_daily_bars(self, symbols, start, end, **kwargs):
            self.calls.append("bars")
            return {
                symbol: ProviderFailure(
                    provider="alpaca",
                    symbol=symbol,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                )
                for symbol in symbols
            }

        def fetch_premarket_observations(self, symbols, as_of, **kwargs):
            self.calls.append("prices")
            return {
                symbol: ProviderFailure(
                    provider="alpaca",
                    symbol=symbol,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                )
                for symbol in symbols
            }

    scenario = load_evaluation_scenarios()[4]
    expectation = scenario.current_scope_expectation.subcases[0]
    deps = dependencies(tmp_path, market=GlobalOutage())
    harness = EvaluationHarness(
        dependencies_factory=lambda current_scenario, selected: deps,
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
    )

    observation = execute_current_scope_scenario(scenario, harness, expectation)

    assert observation.current_scope.execution_status is ExecutionStatus.PUBLISHED
    assert observation.current_scope.data_quality_status is DataQualityStatus.FAIL
    assert observation.current_scope.delivery_status is DeliveryStatus.ON_TIME
    assert observation.current_scope.report_banner == "Brief origin: OPERATIONAL"
    assert observation.current_scope.error_codes == (ErrorCode.PROVIDER_UNAVAILABLE,)
    assert observation.current_scope == expectation
    assert all(not capability.available for capability in observation.current_scope.capabilities)
    assert all(
        ErrorCode.PROVIDER_UNAVAILABLE in capability.reason_codes
        for capability in observation.current_scope.capabilities
    )
    assert observation.replay_json_matches is True
    assert observation.replay_markdown_matches is True
    assert observation.provider_call_count > 0
    assert observation.watchlist_exclusions_visible is True


def test_s25_before_close_missed_window_observes_operational_publication(
    tmp_path: Path,
) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    class MissedWindowClock:
        def now_utc(self):
            return datetime(2026, 9, 28, 13, 30, tzinfo=UTC)

    scenario = load_evaluation_scenarios()[24]
    expectation = scenario.current_scope_expectation.primary
    deps = replace(dependencies(tmp_path), clock=MissedWindowClock())
    harness = EvaluationHarness(
        dependencies_factory=lambda current_scenario, selected: deps,
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
    )

    observation = execute_current_scope_scenario(scenario, harness, expectation)

    assert observation.current_scope == expectation
    assert observation.provider_call_count == 0
    assert observation.missed_run_record_durable is False
    assert observation.replay_json_matches is True
    assert observation.replay_markdown_matches is True


def test_s25_after_close_missed_run_observes_skipped_without_publication(
    tmp_path: Path,
) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    class AfterCloseClock:
        def now_utc(self):
            return datetime(2026, 9, 28, 20, 0, tzinfo=UTC)

    scenario = load_evaluation_scenarios()[24]
    expectation = scenario.current_scope_expectation.subcases[0]
    deps = replace(dependencies(tmp_path), clock=AfterCloseClock())
    harness = EvaluationHarness(
        dependencies_factory=lambda current_scenario, selected: deps,
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
    )

    observation = execute_current_scope_scenario(scenario, harness, expectation)

    assert observation.current_scope == expectation
    assert observation.artifact_hashes == FrozenMap({})
    assert observation.replay_json_matches is None
    assert observation.replay_markdown_matches is None
    assert observation.provider_call_count == 0
    assert observation.missed_run_record_durable is True


def test_s25_evaluation_classifies_missed_window_assertions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from finance_research_agent.evaluation import harness as harness_module
    from finance_research_agent.evaluation.models import (
        CurrentScopeServiceObservation,
        DomainAssertionOutcome,
    )

    scenario = load_evaluation_scenarios()[24]
    before_close = scenario.current_scope_expectation.primary
    after_close = scenario.current_scope_expectation.subcases[0]
    observations = {
        before_close.case_id: CurrentScopeServiceObservation(
            scenario_id=scenario.id,
            current_scope=before_close,
            artifact_hashes=FrozenMap({}),
            replay_json_matches=True,
            replay_markdown_matches=True,
            source_limitations_adjacent=False,
            watchlist_exclusions_visible=False,
            provider_call_count=0,
            missed_run_record_durable=False,
        ),
        after_close.case_id: CurrentScopeServiceObservation(
            scenario_id=scenario.id,
            current_scope=after_close,
            artifact_hashes=FrozenMap({}),
            replay_json_matches=None,
            replay_markdown_matches=None,
            source_limitations_adjacent=None,
            watchlist_exclusions_visible=None,
            provider_call_count=0,
            missed_run_record_durable=True,
        ),
    }

    def observe(_scenario, _harness, selected):
        return observations[selected.case_id]

    def pass_domain_assertion(assertion, _fixtures):
        return DomainAssertionOutcome(
            kind=assertion.kind,
            fixture_id=assertion.fixture_id,
            status="PASS",
            matched_fields=("observed",),
            mismatched_fields=(),
        )

    def unused_dependencies_factory(_scenario, _selected):
        raise AssertionError("the observation stub should bypass application dependencies")

    monkeypatch.setattr(harness_module, "execute_current_scope_scenario", observe)
    monkeypatch.setattr(harness_module, "execute_domain_assertion", pass_domain_assertion)
    harness = EvaluationHarness(
        dependencies_factory=unused_dependencies_factory,
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
        domain_fixtures=DomainFixtureBank({}),
    )

    outcome = execute_evaluation_scenario(scenario, harness)

    assert outcome.assertions_failed == ()
    assert outcome.assertions_pending == ()
    assert ScenarioAssertionId.CURRENT_SCOPE_MATCHES in outcome.assertions_passed
    assert ScenarioAssertionId.NO_PROVIDER_READ_AFTER_WINDOW in outcome.assertions_passed
    assert ScenarioAssertionId.MISSED_RUN_RECORD_DURABLE in outcome.assertions_passed

    observations[after_close.case_id] = observations[after_close.case_id].model_copy(
        update={"provider_call_count": 1}
    )
    provider_read_outcome = execute_evaluation_scenario(scenario, harness)
    assert provider_read_outcome.assertions_failed == (
        ScenarioAssertionId.NO_PROVIDER_READ_AFTER_WINDOW,
    )
    assert provider_read_outcome.assertions_pending == ()

    observations[after_close.case_id] = observations[after_close.case_id].model_copy(
        update={"provider_call_count": 0, "missed_run_record_durable": False}
    )
    missing_record_outcome = execute_evaluation_scenario(scenario, harness)
    assert missing_record_outcome.assertions_failed == (
        ScenarioAssertionId.MISSED_RUN_RECORD_DURABLE,
    )
    assert missing_record_outcome.assertions_pending == ()


def test_s25_full_evaluation_runs_both_missed_window_service_outcomes(
    tmp_path: Path,
) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime, time

    from finance_research_agent.domain.enums import InvocationType
    from finance_research_agent.evaluation.domain_assertions import RunWindowFixture
    from finance_research_agent.evaluation.models import FixtureSetId

    class FixedClock:
        def __init__(self, value: datetime) -> None:
            self.value = value

        def now_utc(self) -> datetime:
            return self.value

    class MissedCalendar:
        def is_trading_day(self, market_date):
            return market_date == date(2026, 9, 28)

        def session_open_close(self, market_date):
            return (
                datetime.combine(market_date, time(13, 30), UTC),
                datetime.combine(market_date, time(20, 0), UTC),
            )

    scenario = load_evaluation_scenarios()[24]
    case_times = {
        "BEFORE_CLOSE_MISSED_WINDOW": datetime(2026, 9, 28, 13, 30, tzinfo=UTC),
        "AT_OR_AFTER_CLOSE": datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
    }

    def create_dependencies(current_scenario, selected):
        return replace(
            dependencies(tmp_path / selected.case_id),
            clock=FixedClock(case_times[selected.case_id]),
        )

    harness = EvaluationHarness(
        dependencies_factory=create_dependencies,
        market_date=date(2026, 9, 28),
        reduced_report_reason=ReducedReportReason.SYNTHESIS_UNAVAILABLE,
        domain_fixtures=DomainFixtureBank(
            {
                FixtureSetId.S25_MISSED_WINDOW: RunWindowFixture(
                    now_utc=case_times["AT_OR_AFTER_CLOSE"],
                    calendar=MissedCalendar(),
                    requested_market_date=date(2026, 9, 28),
                    invocation=InvocationType.SCHEDULED,
                )
            }
        ),
    )

    outcome = execute_evaluation_scenario(scenario, harness)

    assert outcome.current_scope_outcomes[0].current_scope == (
        scenario.current_scope_expectation.primary
    )
    assert outcome.current_scope_outcomes[1].current_scope == (
        scenario.current_scope_expectation.subcases[0]
    )
    assert outcome.current_scope_outcomes[0].missed_run_record_durable is False
    assert outcome.current_scope_outcomes[1].missed_run_record_durable is True
    assert outcome.current_scope_outcomes[0].provider_call_count == 0
    assert outcome.current_scope_outcomes[1].provider_call_count == 0
    assert outcome.assertions_failed == ()
    assert outcome.assertions_pending == ()
    assert outcome.assertions_passed == (
        ScenarioAssertionId.CURRENT_SCOPE_MATCHES,
        ScenarioAssertionId.NO_PROVIDER_READ_AFTER_WINDOW,
        ScenarioAssertionId.MISSED_RUN_RECORD_DURABLE,
    )


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
        provider_call_count=0,
        missed_run_record_durable=False,
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
            provider_call_count=0,
            missed_run_record_durable=False,
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
