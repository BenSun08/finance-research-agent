import json
from datetime import UTC, date, datetime, time, timedelta
from inspect import signature

import pytest
from pydantic import TypeAdapter

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
from finance_research_agent.evaluation.domain_assertions import (
    DomainFixtureBank,
    EventRiskFixture,
    RegimeCalculationFixture,
    RunWindowFixture,
    execute_domain_assertion,
)
from finance_research_agent.evaluation.models import DomainAssertion
from tests.support.synthetic_market import CUTOFF, make_regime_case


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
