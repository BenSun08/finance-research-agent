import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from inspect import signature

import pytest
from pydantic import TypeAdapter

from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.domain import (
    eligibility,
    events,
    market_calendar,
    observations,
    plans,
    regime,
    scoring,
    setups,
    sizing,
)
from finance_research_agent.domain.enums import InvocationType, PlanStatus
from finance_research_agent.domain.events import EventEvidenceProjection
from finance_research_agent.domain.models import EventRecord, InstrumentIdentity, SourceHealth
from finance_research_agent.domain.regime import RegimePolicy
from finance_research_agent.domain.scoring import CorrelationEvidence
from finance_research_agent.evaluation import domain_assertions as domain_assertions_module
from finance_research_agent.evaluation.domain_assertions import (
    CandidateRankingFixture,
    CandidateScoreFixture,
    DomainFixtureBank,
    EventRiskFixture,
    InstrumentEligibilityFixture,
    PlanBuildFixture,
    PositionSizingFixture,
    PriorObservationFixture,
    RegimeCalculationFixture,
    RunWindowFixture,
    SetupDetectionFixture,
    execute_domain_assertion,
)
from finance_research_agent.evaluation.models import DomainAssertion
from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
from tests.evaluation.fixture_bank import build_domain_fixture_bank
from tests.support.synthetic_market import CUTOFF, make_regime_case
from tests.unit import test_eligibility as eligibility_fixtures
from tests.unit import test_observations as observation_fixtures
from tests.unit import test_scoring as scoring_fixtures
from tests.unit import test_setups as setup_fixtures
from tests.unit import test_trade_plan as trade_plan_fixtures


def test_domain_assertion_calls_real_regime_function_and_compares_typed_output() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "REGIME_CALCULATION",
                "fixture_id": "S04_DEFENSIVE_REGIME",
                "expected_fields": {"regime": "defensive"},
            }
        )
    )
    fixture = make_regime_case("risk-off")
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: RegimeCalculationFixture(
                snapshots=fixture.snapshots,
                policy=regime.RegimePolicy(),
                cutoff_at=CUTOFF,
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.kind == assertion.kind
    assert outcome.fixture_id == assertion.fixture_id
    assert outcome.status == "PASS"
    assert outcome.matched_fields == ("regime",)
    assert outcome.mismatched_fields == ()


def test_domain_assertion_rejects_fixture_record_for_the_wrong_operation() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "REGIME_CALCULATION",
                "fixture_id": "S04_DEFENSIVE_REGIME",
                "expected_fields": {"regime": "defensive"},
            }
        )
    )
    bank = DomainFixtureBank({assertion.fixture_id: object()})

    with pytest.raises(TypeError, match="RegimeCalculationFixture"):
        execute_domain_assertion(assertion, bank)


def test_event_risk_assertion_calls_real_verified_earnings_rule() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "EVENT_RISK",
                "fixture_id": "S07_EARNINGS_WINDOW",
                "expected_fields": {
                    "plan_status": "BLOCKED",
                    "gate_reason_codes": ["UNSUPPORTED_INSTRUMENT"],
                },
            }
        )
    )
    instrument = InstrumentIdentity(
        instrument_id="MSFT",
        symbol="MSFT",
        name="Microsoft",
        instrument_type="COMMON_STOCK",
        primary_exchange="NASDAQ",
        listing_country="US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: EventRiskFixture(
                instrument=instrument,
                events=(
                    EventRecord(
                        event_id="earnings",
                        event_type="EARNINGS",
                        subject_symbol="MSFT",
                        event_time=CUTOFF + timedelta(days=1),
                        verified=True,
                        materiality="HIGH",
                        supporting_evidence_ids=("ev-earnings",),
                        conflict_evidence_ids=(),
                    ),
                ),
                plan_expires_at=CUTOFF + timedelta(days=2),
                evidence_cutoff_at=CUTOFF,
                source_health=(
                    SourceHealth(provider="macro-calendar", available=True, required=True),
                ),
                event_evidence=(
                    EventEvidenceProjection(
                        event_id="earnings",
                        retrieved_at=CUTOFF,
                        supporting_authority_tier=1,
                    ),
                ),
                unverified_material_status=PlanStatus.REVIEW_REQUIRED,
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.status == "PASS"
    assert outcome.matched_fields == ("plan_status", "gate_reason_codes")


class _TradingCalendar:
    def is_trading_day(self, market_date: date) -> bool:
        return market_date == date(2026, 9, 28)

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(market_date, time(13, 30), UTC),
            datetime.combine(market_date, time(20), UTC),
        )


def test_run_window_assertion_calls_real_after_close_decision() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "RUN_WINDOW",
                "fixture_id": "S25_MISSED_WINDOW",
                "expected_fields": {
                    "missed_record_only": True,
                    "reason_code": "MISSED_WINDOW",
                },
            }
        )
    )
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: RunWindowFixture(
                now_utc=datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
                calendar=_TradingCalendar(),
                requested_market_date=date(2026, 9, 28),
                invocation=InvocationType.SCHEDULED,
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.status == "PASS"
    assert outcome.matched_fields == ("missed_record_only", "reason_code")


def test_instrument_eligibility_assertion_calls_real_eligibility_gates() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "INSTRUMENT_ELIGIBILITY",
                "fixture_id": "S14_LEVERAGED_ETF",
                "expected_fields": {
                    "gate_statuses": ["BLOCK"],
                    "reason_codes": ["UNSUPPORTED_INSTRUMENT"],
                },
            }
        )
    )
    instrument = eligibility_fixtures._eligible_instrument().model_copy(
        update={"instrument_type": "ETF", "is_leveraged": True}
    )
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: InstrumentEligibilityFixture(
                instrument=instrument,
                watchlist_item=eligibility_fixtures._watchlist_item(),
                snapshot=eligibility_fixtures._snapshot(instrument),
                setup_policy=eligibility_fixtures._setup_policy(),
                direction="LONG",
                halted=False,
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.status == "PASS"
    assert outcome.matched_fields == ("gate_statuses", "reason_codes")


def test_setup_detection_assertion_calls_real_pullback_detection() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "SETUP_DETECTION",
                "fixture_id": "S02_PULLBACK",
                "expected_fields": {"setup_types": ["TREND_PULLBACK"]},
            }
        )
    )
    context = setup_fixtures._load_context("valid-pullback.json")
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: SetupDetectionFixture(
                snapshot=context["snapshot"],
                benchmark=context["benchmark"],
                sector_proxy=context["sector_proxy"],
                setup_policy=context["setup_policy"],
                evidence_cutoff_at=context["evidence_cutoff_at"],
                eligibility_gates=context["eligibility_gates"],
                event_assessment=context["event_assessment"],
                data_quality=context["data_quality"],
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.status == "PASS"
    assert outcome.matched_fields == ("setup_types",)


def test_candidate_score_assertion_calls_real_score_calculation() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "CANDIDATE_SCORE",
                "fixture_id": "S01_BREAKOUT",
                "expected_fields": {
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "total_score": "79.65965732087227414330218068",
                    "plan_status": "DRAFT",
                },
            }
        )
    )
    context = setup_fixtures._load_context("valid-breakout.json")
    setup = setups.detect_setups(**context)[0]
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: CandidateScoreFixture(
                setup=setup,
                setup_policy=context["setup_policy"],
                eligibility_gates=context["eligibility_gates"],
                event_assessment=context["event_assessment"],
                data_quality=context["data_quality"],
                regime_policy_version=RegimePolicy().version,
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.status == "PASS"
    assert outcome.matched_fields == ("setup_type", "total_score", "plan_status")


def test_candidate_ranking_assertion_compares_exact_order_and_selection() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "CANDIDATE_RANKING",
                "fixture_id": "S03_NEUTRAL_THRESHOLD",
                "expected_fields": {
                    "ranked_symbols": ["AAA"],
                    "selected_for_plan": [False],
                    "selection_reasons": [["SCORE_BELOW_THRESHOLD"]],
                },
            }
        )
    )
    candidate = scoring_fixtures._candidate(quality="0.7999")
    policy = RegimePolicy()
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: CandidateRankingFixture(
                candidates=(candidate,),
                regime=scoring_fixtures._regime(
                    scoring_fixtures.Regime.NEUTRAL
                ),
                regime_policy=policy,
                correlations=(),
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.status == "PASS"
    assert outcome.matched_fields == (
        "ranked_symbols",
        "selection_reasons",
        "selected_for_plan",
    )


def test_candidate_ranking_assertion_calls_real_correlation_selection() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "CANDIDATE_RANKING",
                "fixture_id": "S20_CORRELATED_CANDIDATES",
                "expected_fields": {
                    "ranked_symbols": ["AAA", "BBB"],
                    "selected_for_plan": [True, False],
                    "secondary_alternative": [False, True],
                    "primary_symbols": [None, "AAA"],
                },
            }
        )
    )
    first = scoring_fixtures._candidate("BBB")
    second = scoring_fixtures._candidate("AAA")
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: CandidateRankingFixture(
                candidates=(first, second),
                regime=scoring_fixtures._regime(),
                regime_policy=RegimePolicy(),
                correlations=(
                    CorrelationEvidence(
                        left_symbol="AAA",
                        right_symbol="BBB",
                        coefficient=Decimal("0.95"),
                        evidence_ids=("ev-correlation",),
                        observed_at=CUTOFF,
                        method_version="fixture-1",
                        exposure_description="Shared technology factor exposure",
                    ),
                ),
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.status == "PASS"
    assert outcome.matched_fields == (
        "ranked_symbols",
        "selected_for_plan",
        "secondary_alternative",
        "primary_symbols",
    )


def test_s08_material_revision_assertion_covers_filing_packet_and_new_plan_gate() -> None:
    scenario = load_evaluation_scenarios()[7]
    assertion = scenario.domain_assertions[0]

    assert {
        "filing_available_only_in_r2",
        "new_plan_status",
        "new_plan_gate_reason_codes",
    } <= assertion.expected_fields.model_fields_set

    outcome = execute_domain_assertion(assertion, build_domain_fixture_bank())

    assert outcome.status == "PASS"
    assert {
        "filing_available_only_in_r2",
        "plan_status",
        "expiry_reasons",
        "new_plan_status",
        "new_plan_gate_reason_codes",
        "new_plan_matches_revision_2_packet",
    } == set(outcome.matched_fields)


def test_s08_filing_missing_from_revision_two_fails_expiry_and_replacement_plan() -> None:
    scenario = load_evaluation_scenarios()[7]
    assertion = scenario.domain_assertions[0]
    fixtures = build_domain_fixture_bank()
    fixture = fixtures.get(assertion.fixture_id)
    revision_two = fixture.revision_two_packet
    without_filing = build_research_packet(
        run=revision_two.run,
        evidence=tuple(
            item
            for item in revision_two.evidence
            if item.evidence_id != fixture.material_filing_evidence_id
        ),
        snapshots=revision_two.market,
        events=(),
        metrics=revision_two.metrics,
        gates=revision_two.gates,
        candidates=revision_two.candidates,
        exclusions=revision_two.candidate_exclusions,
        plans=revision_two.deterministic_plan_inputs,
        capabilities=revision_two.capability_states,
        observations=revision_two.prior_plan_observations,
        max_serialized_bytes=revision_two.synthesis_constraints.max_serialized_bytes,
    )
    records = dict(fixtures.records)
    records[assertion.fixture_id] = replace(
        fixture,
        revision_two_packet=without_filing,
    )

    outcome = execute_domain_assertion(assertion, DomainFixtureBank(records))

    assert outcome.status == "FAIL"
    assert outcome.mismatched_fields == (
        "plan_status",
        "expiry_reasons",
        "filing_available_only_in_r2",
        "new_plan_status",
        "new_plan_gate_reason_codes",
        "new_plan_matches_revision_2_packet",
    )


def test_s08_missing_event_evidence_reference_fails_closed() -> None:
    scenario = load_evaluation_scenarios()[7]
    assertion = scenario.domain_assertions[0]
    fixtures = build_domain_fixture_bank()
    fixture = fixtures.get(assertion.fixture_id)
    revision_two = fixture.revision_two_packet
    dangling_reference_packet = build_research_packet(
        run=revision_two.run,
        evidence=tuple(
            item
            for item in revision_two.evidence
            if item.evidence_id != fixture.material_filing_evidence_id
        ),
        snapshots=revision_two.market,
        events=revision_two.events,
        metrics=revision_two.metrics,
        gates=revision_two.gates,
        candidates=revision_two.candidates,
        exclusions=revision_two.candidate_exclusions,
        plans=revision_two.deterministic_plan_inputs,
        capabilities=revision_two.capability_states,
        observations=revision_two.prior_plan_observations,
        max_serialized_bytes=revision_two.synthesis_constraints.max_serialized_bytes,
    )
    records = dict(fixtures.records)
    records[assertion.fixture_id] = replace(
        fixture,
        revision_two_packet=dangling_reference_packet,
    )

    with pytest.raises(ValueError, match="references absent packet evidence"):
        execute_domain_assertion(assertion, DomainFixtureBank(records))


def test_plan_build_assertion_calls_real_plan_builder() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "PLAN_BUILD",
                "fixture_id": "S19_MISSING_PORTFOLIO_HEAT",
                "expected_fields": {
                    "plan_status": "REVIEW_REQUIRED",
                    "data_quality_flags": ["PORTFOLIO_HEAT_UNAVAILABLE"],
                },
            }
        )
    )
    plan_inputs = trade_plan_fixtures.inputs.__wrapped__()
    plan_inputs["risk_policy"] = plan_inputs["risk_policy"].model_copy(
        update={"existing_portfolio_heat_pct": None}
    )
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: PlanBuildFixture(
                candidate=plan_inputs["candidate"],
                run=plan_inputs["run"],
                watchlist_item=plan_inputs["watchlist_item"],
                regime=plan_inputs["regime"],
                event_assessment=plan_inputs["event_assessment"],
                gates=plan_inputs["gates"],
                current_price=plan_inputs["current_price"],
                capability_states=plan_inputs["capability_states"],
                setup_policy=plan_inputs["setup_policy"],
                risk_policy=plan_inputs["risk_policy"],
                generated_at=plan_inputs["generated_at"],
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.status == "PASS"
    assert outcome.matched_fields == ("plan_status", "data_quality_flags")


def test_prior_observation_assertion_calls_real_completed_bar_analysis() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "PRIOR_OBSERVATION",
                "fixture_id": "S21_AMBIGUOUS_DAILY_BAR",
                "expected_fields": {
                    "outcomes": ["ENTRY_ZONE_OBSERVED", "AMBIGUOUS_SEQUENCE"],
                },
            }
        )
    )
    plan = plans.build_trade_plan(**trade_plan_fixtures.inputs.__wrapped__())
    entry = plan.entry_zone.upper.value
    bars = (
        observation_fixtures._bar(
            plan, date(2026, 9, 22), entry - 20, entry - 10, entry - 15
        ),
        observation_fixtures._bar(
            plan,
            date(2026, 9, 23),
            plan.candidate_stop.value - 1,
            plan.target_scenarios[0].price.value + 1,
            entry,
        ),
    )
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: PriorObservationFixture(
                plan=plan,
                completed_bars=bars,
                observed_through=date(2026, 9, 23),
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.status == "PASS"
    assert outcome.matched_fields == ("outcomes",)


@pytest.mark.parametrize(
    ("fixture_id", "expected", "mutate"),
    [
        (
            "S10_STALE_PREMARKET_QUOTE",
            {"status": "SIZING_UNAVAILABLE"},
            "stale_price",
        ),
        (
            "S17_INVALID_STOP",
            {
                "status": "SIZING_UNAVAILABLE",
                "unavailable_reasons": ["STOP_NOT_BELOW_ENTRY"],
            },
            "invalid_stop",
        ),
        (
            "S18_MISSING_CAPITAL",
            {
                "status": "SIZING_UNAVAILABLE",
                "unavailable_reasons": ["PLANNING_CAPITAL_MISSING"],
            },
            "missing_capital",
        ),
    ],
)
def test_position_sizing_assertion_calls_real_calculation(
    fixture_id: str, expected: dict[str, object], mutate: str
) -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "POSITION_SIZING",
                "fixture_id": fixture_id,
                "expected_fields": expected,
            }
        )
    )
    plan_inputs = trade_plan_fixtures.inputs.__wrapped__()
    plan = plans.build_trade_plan(**plan_inputs)
    now_utc = plan.generated_at + timedelta(minutes=5)
    current_price = plan_inputs["current_price"]
    risk_policy = plan_inputs["risk_policy"]
    if mutate == "stale_price":
        now_utc += timedelta(hours=3)
    elif mutate == "invalid_stop":
        plan = plan.model_copy(
            update={
                "candidate_stop": plan.candidate_stop.model_copy(
                    update={"value": plan.entry_zone.upper.value}
                )
            }
        )
    elif mutate == "missing_capital":
        risk_policy = risk_policy.model_copy(update={"planning_capital_usd": None})
    bank = DomainFixtureBank(
        {
            assertion.fixture_id: PositionSizingFixture(
                plan=plan,
                risk_policy=risk_policy,
                regime=scoring_fixtures._regime().regime,
                current_price=current_price,
                now_utc=now_utc,
            )
        }
    )

    outcome = execute_domain_assertion(assertion, bank)

    assert outcome.status == "PASS"
    assert outcome.matched_fields == tuple(
        field
        for field in ("status", "unavailable_reasons")
        if field in expected
    )


def test_closed_domain_function_signatures_remain_pinned() -> None:
    expected_parameters = {
        regime.calculate_regime: ("snapshots", "policy", "cutoff_at"),
        eligibility.evaluate_instrument_eligibility: (
            "instrument", "watchlist_item", "snapshot", "setup_policy", "direction", "halted"
        ),
        setups.detect_setups: (
            "snapshot", "benchmark", "sector_proxy", "setup_policy", "evidence_cutoff_at",
            "eligibility_gates", "event_assessment", "data_quality"
        ),
        scoring.score_candidate: (
            "setup", "setup_policy", "eligibility_gates", "event_assessment", "data_quality",
            "regime_policy_version", "component_evaluator"
        ),
        scoring.rank_candidates: ("candidates", "regime", "regime_policy", "correlations"),
        events.assess_event_risk: (
            "instrument", "events", "plan_expires_at", "evidence_cutoff_at", "source_health",
            "event_evidence", "unverified_material_status"
        ),
        plans.build_trade_plan: (
            "candidate", "run", "watchlist_item", "regime", "event_assessment", "gates",
            "current_price", "capability_states", "setup_policy", "risk_policy", "generated_at"
        ),
        plans.expire_plan: (
            "plan", "now_utc", "current_price", "entry_trigger_satisfied", "invalidation_observed",
            "new_material_information", "earnings_blackout", "incompatible_regime",
            "stale_or_conflicting_data", "eligibility_changed"
        ),
        sizing.calculate_position_sizing: (
            "plan", "risk_policy", "regime", "current_price", "now_utc"
        ),
        observations.observe_prior_plan: ("plan", "completed_bars", "observed_through"),
        market_calendar.resolve_run_window: (
            "now_utc", "calendar", "requested_market_date", "invocation"
        ),
    }

    assert {
        function.__name__: tuple(signature(function).parameters)
        for function in expected_parameters
    } == {function.__name__: names for function, names in expected_parameters.items()}


def test_every_manifest_domain_assertion_runs_against_its_named_real_fixture() -> None:
    fixture_bank = build_domain_fixture_bank()
    scenarios = load_evaluation_scenarios()
    declared_fixture_ids = {
        assertion.fixture_id
        for scenario in scenarios
        for assertion in scenario.domain_assertions
    }

    assert set(fixture_bank.records) == declared_fixture_ids

    outcomes = tuple(
        (scenario.id, assertion, execute_domain_assertion(assertion, fixture_bank))
        for scenario in scenarios
        for assertion in scenario.domain_assertions
    )

    assert len(outcomes) == 23
    assert all(
        outcome.status == "PASS"
        for scenario_id, assertion, outcome in outcomes
    ), tuple(
        (scenario_id.value, assertion.kind.value, outcome.mismatched_fields)
        for scenario_id, assertion, outcome in outcomes
        if outcome.status != "PASS"
    )


def test_s01_manifest_asserts_score_floor_and_metric_evidence_bindings() -> None:
    scenario = load_evaluation_scenarios()[0]
    assertion = scenario.domain_assertions[0]
    fixture_bank = build_domain_fixture_bank()

    outcome = execute_domain_assertion(assertion, fixture_bank)

    assert assertion.expected_fields.score_at_least_70 is True
    assert assertion.expected_fields.numeric_citation_bindings_match is True
    assert outcome.status == "PASS"
    assert "score_at_least_70" in outcome.matched_fields
    assert "numeric_citation_bindings_match" in outcome.matched_fields


def test_s01_metric_evidence_binding_mismatch_fails_the_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_evaluation_scenarios()[0]
    assertion = scenario.domain_assertions[0]
    original_score_candidate = domain_assertions_module.score_candidate

    def misbound_candidate(*args, **kwargs):
        candidate = original_score_candidate(*args, **kwargs)
        first = candidate.components[0].model_copy(update={"evidence_ids": ("unbound",)})
        return candidate.model_copy(
            update={"components": (first, *candidate.components[1:])}
        )

    monkeypatch.setattr(domain_assertions_module, "score_candidate", misbound_candidate)
    outcome = execute_domain_assertion(assertion, build_domain_fixture_bank())

    assert outcome.status == "FAIL"
    assert outcome.mismatched_fields == ("numeric_citation_bindings_match",)


def test_s01_numeric_component_mismatch_fails_the_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_evaluation_scenarios()[0]
    assertion = scenario.domain_assertions[0]
    original_score_candidate = domain_assertions_module.score_candidate

    def miscalculated_candidate(*args, **kwargs):
        candidate = original_score_candidate(*args, **kwargs)
        first = candidate.components[0]
        changed_first = first.model_copy(update={"points": first.points + Decimal("1")})
        return candidate.model_copy(
            update={
                "components": (changed_first, *candidate.components[1:]),
                "positive_score": candidate.positive_score + Decimal("1"),
                "total_score": candidate.total_score + Decimal("1"),
            }
        )

    monkeypatch.setattr(domain_assertions_module, "score_candidate", miscalculated_candidate)
    outcome = execute_domain_assertion(assertion, build_domain_fixture_bank())

    assert outcome.status == "FAIL"
    assert "numeric_citation_bindings_match" in outcome.mismatched_fields


def test_s01_score_below_floor_fails_the_assertion(monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = load_evaluation_scenarios()[0]
    assertion = scenario.domain_assertions[0]
    original_score_candidate = domain_assertions_module.score_candidate

    def under_threshold_candidate(*args, **kwargs):
        candidate = original_score_candidate(*args, **kwargs)
        return candidate.model_copy(update={"total_score": Decimal("69")})

    monkeypatch.setattr(domain_assertions_module, "score_candidate", under_threshold_candidate)
    outcome = execute_domain_assertion(assertion, build_domain_fixture_bank())

    assert outcome.status == "FAIL"
    assert "score_at_least_70" in outcome.mismatched_fields


def test_s02_pullback_manifest_asserts_restrengthening_gates_and_draft_score() -> None:
    scenario = load_evaluation_scenarios()[1]
    assertion = scenario.domain_assertions[0]

    outcome = execute_domain_assertion(assertion, build_domain_fixture_bank())

    assert assertion.expected_fields.restrengthening_conditions_satisfied == (True,)
    assert assertion.expected_fields.scored_plan_statuses == (PlanStatus.DRAFT,)
    assert outcome.status == "PASS"
    assert "restrengthening_conditions_satisfied" in outcome.matched_fields
    assert "scored_plan_statuses" in outcome.matched_fields


def test_s04_defensive_plan_stays_blocked_with_zero_regime_multiplier() -> None:
    scenario = load_evaluation_scenarios()[3]
    assertion = next(
        assertion
        for assertion in scenario.domain_assertions
        if assertion.kind.value == "PLAN_BUILD"
    )

    outcome = execute_domain_assertion(assertion, build_domain_fixture_bank())

    assert assertion.expected_fields.plan_status is PlanStatus.BLOCKED
    assert assertion.expected_fields.candidate_score_at_least_70 is True
    assert assertion.expected_fields.position_sizing_regime_multiplier == Decimal(0)
    assert outcome.status == "PASS"


def test_s04_plan_assertion_requires_its_defensive_plan_fixture() -> None:
    scenario = load_evaluation_scenarios()[3]
    assertion = scenario.domain_assertions[1]
    fixture_bank = build_domain_fixture_bank()
    fixture = fixture_bank.get(assertion.fixture_id)
    assert isinstance(fixture, RegimeCalculationFixture)
    incomplete = replace(fixture, plan_fixture=None)
    records = dict(fixture_bank.records)
    records[assertion.fixture_id] = incomplete

    with pytest.raises(TypeError, match="PLAN_BUILD requires PlanBuildFixture"):
        execute_domain_assertion(assertion, DomainFixtureBank(records))


def test_s07_event_after_expiry_does_not_block_the_plan() -> None:
    scenario = load_evaluation_scenarios()[6]
    assertion = scenario.domain_assertions[0]

    outcome = execute_domain_assertion(assertion, build_domain_fixture_bank())

    assert assertion.expected_fields.plan_status is PlanStatus.BLOCKED
    assert assertion.expected_fields.alternate_plan_status is PlanStatus.DRAFT
    assert outcome.status == "PASS"
    assert "alternate_plan_status" in outcome.matched_fields


def test_s07_alternate_case_requires_its_expiry_fixture() -> None:
    scenario = load_evaluation_scenarios()[6]
    assertion = scenario.domain_assertions[0]
    fixture_bank = build_domain_fixture_bank()
    fixture = fixture_bank.get(assertion.fixture_id)
    assert isinstance(fixture, EventRiskFixture)
    incomplete = replace(fixture, alternate_plan_expires_at=None)
    records = dict(fixture_bank.records)
    records[assertion.fixture_id] = incomplete

    with pytest.raises(TypeError, match="alternate event-risk assertion requires"):
        execute_domain_assertion(assertion, DomainFixtureBank(records))


def test_s15_uncertain_identity_is_also_excluded_before_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_evaluation_scenarios()[14]
    assertion = scenario.domain_assertions[0]
    fixtures = build_domain_fixture_bank()
    fixture = fixtures.get(assertion.fixture_id)

    assert isinstance(fixture, InstrumentEligibilityFixture)
    assert fixture.instrument.is_active is True
    assert fixture.halted is True
    assert fixture.alternate_instrument is not None
    assert fixture.alternate_instrument.is_active is None
    assert fixture.alternate_halted is False
    monkeypatch.setattr(
        domain_assertions_module,
        "score_candidate",
        lambda *args, **kwargs: pytest.fail("eligibility must run before scoring"),
    )

    outcome = execute_domain_assertion(assertion, fixtures)

    assert assertion.expected_fields.gate_statuses
    assert assertion.expected_fields.alternate_gate_statuses
    assert assertion.expected_fields.alternate_reason_codes == ("UNSUPPORTED_INSTRUMENT",)
    assert outcome.status == "PASS"
    assert "alternate_reason_codes" in outcome.matched_fields


def test_s15_alternate_case_requires_identity_and_snapshot() -> None:
    scenario = load_evaluation_scenarios()[14]
    assertion = scenario.domain_assertions[0]
    fixture_bank = build_domain_fixture_bank()
    fixture = fixture_bank.get(assertion.fixture_id)
    assert isinstance(fixture, InstrumentEligibilityFixture)
    incomplete = replace(
        fixture,
        alternate_instrument=None,
        alternate_snapshot=None,
    )
    records = dict(fixture_bank.records)
    records[assertion.fixture_id] = incomplete

    with pytest.raises(TypeError, match="alternate eligibility assertion requires"):
        execute_domain_assertion(assertion, DomainFixtureBank(records))


def test_every_domain_operation_rejects_an_untyped_fixture_record() -> None:
    scenarios = load_evaluation_scenarios()
    assertions_by_kind = {
        assertion.kind: assertion
        for scenario in scenarios
        for assertion in scenario.domain_assertions
    }

    assert len(assertions_by_kind) == 11
    for assertion in assertions_by_kind.values():
        with pytest.raises(TypeError):
            execute_domain_assertion(
                assertion,
                DomainFixtureBank({assertion.fixture_id: object()}),
            )


def test_domain_assertion_reports_exact_output_mismatch() -> None:
    assertion = TypeAdapter(DomainAssertion).validate_json(
        json.dumps(
            {
                "kind": "CANDIDATE_SCORE",
                "fixture_id": "S01_BREAKOUT",
                "expected_fields": {"total_score": "70"},
            }
        )
    )

    outcome = execute_domain_assertion(assertion, build_domain_fixture_bank())

    assert outcome.status == "FAIL"
    assert outcome.matched_fields == ()
    assert outcome.mismatched_fields == ("total_score",)
