"""Named, fully synthetic inputs for the closed Product A evaluation manifest."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from finance_research_agent.domain.enums import InvocationType, PlanStatus
from finance_research_agent.domain.models import EventRecord
from finance_research_agent.domain.plans import build_trade_plan
from finance_research_agent.domain.regime import Regime, RegimePolicy
from finance_research_agent.domain.scoring import CorrelationEvidence
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.evaluation.domain_assertions import (
    CandidateRankingFixture,
    CandidateScoreFixture,
    DomainFixtureBank,
    EventRiskFixture,
    InstrumentEligibilityFixture,
    PlanBuildFixture,
    PlanExpiryFixture,
    PositionSizingFixture,
    PriorObservationFixture,
    RegimeCalculationFixture,
    RunWindowFixture,
    SetupDetectionFixture,
)
from finance_research_agent.evaluation.models import FixtureSetId
from tests.support.synthetic_market import CUTOFF, make_regime_case
from tests.unit import test_eligibility as eligibility
from tests.unit import test_events as events
from tests.unit import test_observations as observations
from tests.unit import test_scoring as scoring
from tests.unit import test_setups as setups
from tests.unit import test_trade_plan as trade_plans


class _EvaluationCalendar:
    def is_trading_day(self, market_date: date) -> bool:
        return market_date == date(2026, 9, 28)

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(market_date, time(13, 30), UTC),
            datetime.combine(market_date, time(20), UTC),
        )


def build_domain_fixture_bank() -> DomainFixtureBank:
    """Build explicit inputs for every domain assertion in S01-S25."""

    policy = RegimePolicy()
    breakout = setups._load_context("valid-breakout.json")
    pullback = setups._load_context("valid-pullback.json")
    breakout_setup = setups.detect_setups(**breakout)[0]
    plan_inputs = trade_plans.inputs.__wrapped__()
    plan = build_trade_plan(**plan_inputs)
    defensive_plan_inputs = dict(plan_inputs)
    defensive_plan_inputs["regime"] = scoring._regime(Regime.DEFENSIVE)
    defensive_multipliers = dict(plan_inputs["risk_policy"].regime_risk_multipliers)
    defensive_multipliers[Regime.DEFENSIVE.name] = Decimal("0")
    defensive_plan_inputs["risk_policy"] = plan_inputs["risk_policy"].model_copy(
        update={"regime_risk_multipliers": FrozenMap(defensive_multipliers)}
    )
    defensive_plan_fixture = PlanBuildFixture(**defensive_plan_inputs)

    supported = events.INSTRUMENT
    future = events.NOW + timedelta(days=1)
    event_fixture = EventRiskFixture(
        instrument=supported,
        events=(
            EventRecord(
                event_id="earnings",
                event_type="EARNINGS",
                subject_symbol=supported.symbol,
                event_time=future,
                verified=True,
                materiality="HIGH",
                supporting_evidence_ids=("ev-earnings",),
                conflict_evidence_ids=(),
            ),
        ),
        plan_expires_at=events.NOW + timedelta(days=2),
        evidence_cutoff_at=events.NOW,
        source_health=events._healthy_macro_source_health(),
        event_evidence=(events._projection("earnings"),),
        unverified_material_status=PlanStatus.REVIEW_REQUIRED,
        alternate_plan_expires_at=events.NOW + timedelta(hours=12),
    )
    conflict_event = EventRecord(
        event_id="conflict",
        event_type="CORPORATE_ACTION",
        subject_symbol=supported.symbol,
        event_time=events.NOW,
        verified=True,
        materiality="HIGH",
        supporting_evidence_ids=("ev-official",),
        conflict_evidence_ids=("ev-media",),
    )

    records = {
        FixtureSetId.S01_BREAKOUT: CandidateScoreFixture(
            setup=breakout_setup,
            setup_policy=breakout["setup_policy"],
            eligibility_gates=breakout["eligibility_gates"],
            event_assessment=breakout["event_assessment"],
            data_quality=breakout["data_quality"],
            regime_policy_version=policy.version,
        ),
        FixtureSetId.S02_PULLBACK: SetupDetectionFixture(
            snapshot=pullback["snapshot"],
            benchmark=pullback["benchmark"],
            sector_proxy=pullback["sector_proxy"],
            setup_policy=pullback["setup_policy"],
            evidence_cutoff_at=pullback["evidence_cutoff_at"],
            eligibility_gates=pullback["eligibility_gates"],
            event_assessment=pullback["event_assessment"],
            data_quality=pullback["data_quality"],
        ),
        FixtureSetId.S03_NEUTRAL_THRESHOLD: CandidateRankingFixture(
            candidates=(scoring._candidate(quality="0.7999"),),
            regime=scoring._regime(Regime.NEUTRAL),
            regime_policy=policy,
            correlations=(),
        ),
        FixtureSetId.S04_DEFENSIVE_REGIME: RegimeCalculationFixture(
            snapshots=make_regime_case("risk-off").snapshots,
            policy=policy,
            cutoff_at=CUTOFF,
            plan_fixture=defensive_plan_fixture,
        ),
        FixtureSetId.S05_UNKNOWN_REGIME: RegimeCalculationFixture(
            snapshots={},
            policy=policy,
            cutoff_at=CUTOFF,
        ),
        FixtureSetId.S06_NO_ELIGIBLE_NAMES: InstrumentEligibilityFixture(
            instrument=eligibility._eligible_instrument(),
            watchlist_item=eligibility._watchlist_item().model_copy(
                update={"role": "RESEARCH_ONLY"}
            ),
            snapshot=eligibility._snapshot(eligibility._eligible_instrument()),
            setup_policy=eligibility._setup_policy(),
            direction="LONG",
            halted=False,
        ),
        FixtureSetId.S07_EARNINGS_WINDOW: event_fixture,
        FixtureSetId.S08_MATERIAL_REVISION: PlanExpiryFixture(
            plan=plan,
            now_utc=plan.valid_from,
            current_price=None,
            entry_trigger_satisfied=False,
            invalidation_observed=False,
            new_material_information=True,
            earnings_blackout=False,
            incompatible_regime=False,
            stale_or_conflicting_data=False,
            eligibility_changed=False,
        ),
        FixtureSetId.S09_SOURCE_CONFLICT: EventRiskFixture(
            instrument=supported,
            events=(conflict_event,),
            plan_expires_at=events.NOW + timedelta(days=2),
            evidence_cutoff_at=events.NOW,
            source_health=events._healthy_macro_source_health(),
            event_evidence=(events._projection("conflict"),),
            unverified_material_status=PlanStatus.REVIEW_REQUIRED,
        ),
        FixtureSetId.S10_STALE_PREMARKET_QUOTE: PositionSizingFixture(
            plan=plan,
            risk_policy=plan_inputs["risk_policy"],
            regime=plan.market_regime,
            current_price=plan_inputs["current_price"],
            now_utc=plan.generated_at + timedelta(hours=3),
        ),
        FixtureSetId.S11_IEX_LIMITATION: SetupDetectionFixture(
            snapshot=breakout["snapshot"],
            benchmark=breakout["benchmark"],
            sector_proxy=breakout["sector_proxy"],
            setup_policy=breakout["setup_policy"],
            evidence_cutoff_at=breakout["evidence_cutoff_at"],
            eligibility_gates=breakout["eligibility_gates"],
            event_assessment=breakout["event_assessment"],
            data_quality=breakout["data_quality"],
        ),
        FixtureSetId.S12_MISSING_MACRO_CALENDAR: EventRiskFixture(
            instrument=supported,
            events=(),
            plan_expires_at=events.NOW + timedelta(days=2),
            evidence_cutoff_at=events.NOW,
            source_health=(),
            event_evidence=(),
            unverified_material_status=PlanStatus.REVIEW_REQUIRED,
        ),
        FixtureSetId.S13_INSUFFICIENT_HISTORY: InstrumentEligibilityFixture(
            instrument=eligibility._eligible_instrument(),
            watchlist_item=eligibility._watchlist_item(),
            snapshot=eligibility._snapshot(eligibility._eligible_instrument()).model_copy(
                update={"completed_daily_bars": ()}
            ),
            setup_policy=eligibility._setup_policy(),
            direction="LONG",
            halted=False,
        ),
        FixtureSetId.S14_LEVERAGED_ETF: InstrumentEligibilityFixture(
            instrument=eligibility._eligible_instrument().model_copy(
                update={"instrument_type": "ETF", "is_leveraged": True}
            ),
            watchlist_item=eligibility._watchlist_item(),
            snapshot=eligibility._snapshot(
                eligibility._eligible_instrument().model_copy(
                    update={"instrument_type": "ETF", "is_leveraged": True}
                )
            ),
            setup_policy=eligibility._setup_policy(),
            direction="LONG",
            halted=False,
        ),
        FixtureSetId.S15_HALTED_OR_UNCERTAIN_IDENTITY: InstrumentEligibilityFixture(
            instrument=eligibility._eligible_instrument(),
            watchlist_item=eligibility._watchlist_item(),
            snapshot=eligibility._snapshot(eligibility._eligible_instrument()),
            setup_policy=eligibility._setup_policy(),
            direction="LONG",
            halted=True,
            alternate_instrument=eligibility._eligible_instrument().model_copy(
                update={"is_active": None}
            ),
            alternate_snapshot=eligibility._snapshot(
                eligibility._eligible_instrument().model_copy(update={"is_active": None})
            ),
        ),
        FixtureSetId.S16_EXCEEDED_ENTRY_ZONE: PlanExpiryFixture(
            plan=plan,
            now_utc=plan.valid_from,
            current_price=plan_inputs["current_price"].model_copy(
                update={"value": plan.entry_zone.upper.value + Decimal("10")}
            ),
            entry_trigger_satisfied=False,
            invalidation_observed=False,
            new_material_information=False,
            earnings_blackout=False,
            incompatible_regime=False,
            stale_or_conflicting_data=False,
            eligibility_changed=False,
        ),
        FixtureSetId.S17_INVALID_STOP: PositionSizingFixture(
            plan=plan.model_copy(
                update={
                    "candidate_stop": plan.candidate_stop.model_copy(
                        update={"value": plan.entry_zone.upper.value}
                    )
                }
            ),
            risk_policy=plan_inputs["risk_policy"],
            regime=plan.market_regime,
            current_price=plan_inputs["current_price"],
            now_utc=plan.generated_at,
        ),
        FixtureSetId.S18_MISSING_CAPITAL: PositionSizingFixture(
            plan=plan,
            risk_policy=plan_inputs["risk_policy"].model_copy(
                update={"planning_capital_usd": None}
            ),
            regime=plan.market_regime,
            current_price=plan_inputs["current_price"],
            now_utc=plan.generated_at,
        ),
        FixtureSetId.S19_MISSING_PORTFOLIO_HEAT: PlanBuildFixture(
            candidate=plan_inputs["candidate"],
            run=plan_inputs["run"],
            watchlist_item=plan_inputs["watchlist_item"],
            regime=plan_inputs["regime"],
            event_assessment=plan_inputs["event_assessment"],
            gates=plan_inputs["gates"],
            current_price=plan_inputs["current_price"],
            capability_states=plan_inputs["capability_states"],
            setup_policy=plan_inputs["setup_policy"],
            risk_policy=plan_inputs["risk_policy"].model_copy(
                update={"existing_portfolio_heat_pct": None}
            ),
            generated_at=plan_inputs["generated_at"],
        ),
        FixtureSetId.S20_CORRELATED_CANDIDATES: CandidateRankingFixture(
            candidates=(scoring._candidate("BBB"), scoring._candidate("AAA")),
            regime=scoring._regime(),
            regime_policy=policy,
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
        ),
        FixtureSetId.S21_AMBIGUOUS_DAILY_BAR: PriorObservationFixture(
            plan=plan,
            completed_bars=(
                observations._bar(
                    plan,
                    date(2026, 9, 22),
                    plan.entry_zone.upper.value - 20,
                    plan.entry_zone.upper.value - 10,
                    plan.entry_zone.upper.value - 15,
                ),
                observations._bar(
                    plan,
                    date(2026, 9, 23),
                    plan.candidate_stop.value - 1,
                    plan.target_scenarios[0].price.value + 1,
                    plan.entry_zone.upper.value,
                ),
            ),
            observed_through=date(2026, 9, 23),
        ),
        FixtureSetId.S25_MISSED_WINDOW: RunWindowFixture(
            now_utc=datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
            calendar=_EvaluationCalendar(),
            requested_market_date=date(2026, 9, 28),
            invocation=InvocationType.SCHEDULED,
        ),
    }
    return DomainFixtureBank(records)
