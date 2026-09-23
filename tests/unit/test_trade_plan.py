from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from finance_research_agent.domain.enums import (
    Capability,
    DataQualityStatus,
    DeliveryStatus,
    GateStatus,
    PlanStatus,
)
from finance_research_agent.domain.models import CapabilityState, GateResult, PriceObservation
from finance_research_agent.domain.plans import TradePlanDraft, _expiry_time, build_trade_plan
from finance_research_agent.domain.policies import RiskPolicy, WatchlistItem
from finance_research_agent.domain.regime import Regime
from tests.unit.test_filesystem_store import _context
from tests.unit.test_scoring import _candidate, _regime


@pytest.fixture
def inputs():
    candidate = _candidate().model_copy(update={"selected_for_plan": True})
    run = _context().model_copy(
        update={
            "run_id": "premarket-2026-09-22-r1",
            "market_date": date(2026, 9, 22),
            "invoked_at": candidate.evidence_cutoff_at,
            "evidence_cutoff_at": candidate.evidence_cutoff_at,
        }
    )
    price = PriceObservation(
        instrument_id=candidate.levels.entry_zone.upper.instrument_id,
        value=candidate.levels.entry_zone.upper.value,
        currency="USD",
        session=candidate.levels.entry_zone.upper.session,
        provider="fixture",
        feed="sip",
        coverage=candidate.levels.entry_zone.upper.coverage,
        observed_at=candidate.evidence_cutoff_at,
        retrieved_at=candidate.evidence_cutoff_at,
        evidence_id="current-price",
        quality_flags=(),
    )
    risk = RiskPolicy(
        version="1",
        sizing_enabled=True,
        planning_capital_usd="10000",
        max_risk_per_trade_pct="0.01",
        max_position_pct="0.10",
        minimum_reward_risk_ratio="2",
        max_total_portfolio_heat_pct="0.06",
        existing_portfolio_heat_pct="0.01",
        quantity_increment="1",
        regime_risk_multipliers={name: "0.5" for name in Regime.__members__},
    )
    return dict(
        candidate=candidate,
        run=run,
        watchlist_item=WatchlistItem(
            symbol=candidate.symbol, role="SATELLITE_ELIGIBLE", research_rationale="Fixture"
        ),
        regime=_regime(),
        event_assessment=candidate.event_assessment,
        gates=(),
        current_price=price,
        capability_states=tuple(
            CapabilityState(capability=c, available=True, reason_codes=(), evidence_ids=())
            for c in Capability
        ),
        setup_policy=_setup_policy(candidate),
        risk_policy=risk,
    )


def _setup_policy(candidate):
    from tests.unit.test_scoring import _setup

    return _setup().policy


def test_builder_projects_all_inputs_into_conditional_plan(inputs):
    plan = build_trade_plan(**inputs)
    assert isinstance(plan, TradePlanDraft)
    assert plan.plan_id.startswith(inputs["run"].run_id)
    assert plan.run_id == inputs["run"].run_id
    assert plan.symbol == inputs["candidate"].symbol
    assert plan.watchlist_role == inputs["watchlist_item"].role
    assert plan.market_regime is inputs["regime"].regime
    assert plan.candidate_score == inputs["candidate"].total_score
    assert plan.entry_zone == inputs["candidate"].levels.entry_zone
    assert plan.candidate_stop == inputs["candidate"].levels.candidate_stop
    assert plan.plan_status is PlanStatus.DRAFT
    assert plan.position_sizing.suggested_units is not None
    assert all(
        t.distance > 0 and t.potential_reward > 0 and t.r_multiple > 0
        for t in plan.target_scenarios
    )
    assert plan.supporting_evidence and plan.no_trade_conditions and plan.counter_thesis
    assert plan.expires_at > plan.valid_from
    assert not hasattr(plan, "order")


def test_builder_requires_selection(inputs):
    inputs["candidate"] = inputs["candidate"].model_copy(update={"selected_for_plan": False})
    with pytest.raises(ValueError, match="selected"):
        build_trade_plan(**inputs)


def test_r5_block_cannot_be_compensated_by_selection(inputs):
    inputs["gates"] = (
        GateResult(
            gate_id="eligibility-block",
            status=GateStatus.BLOCK,
            reason_code="UNSUPPORTED_INSTRUMENT",
            message="Blocked by R5",
            evidence_ids=("r5-evidence",),
            capability=Capability.PLAN_DRAFT_AVAILABLE,
            rule_version="r5",
        ),
    )
    plan = build_trade_plan(**inputs)
    assert plan.plan_status is PlanStatus.BLOCKED
    assert plan.position_sizing.status.value == "SIZING_UNAVAILABLE"


def test_missing_heat_requires_review(inputs):
    risk = inputs["risk_policy"]
    inputs["risk_policy"] = risk.model_copy(update={"existing_portfolio_heat_pct": None})
    plan = build_trade_plan(**inputs)
    assert plan.plan_status is PlanStatus.REVIEW_REQUIRED
    assert "PORTFOLIO_HEAT_UNAVAILABLE" in plan.data_quality_flags


def test_missing_heat_limit_cannot_be_draft(inputs):
    risk = inputs["risk_policy"]
    inputs["risk_policy"] = risk.model_copy(update={"max_total_portfolio_heat_pct": None})
    plan = build_trade_plan(**inputs)
    assert plan.plan_status is PlanStatus.BLOCKED


def test_missing_capability_decision_is_rejected(inputs):
    inputs["capability_states"] = inputs["capability_states"][:-1]
    with pytest.raises(ValueError, match="capability"):
        build_trade_plan(**inputs)


@pytest.mark.parametrize("role", ["CORE_MONITOR", "RESEARCH_ONLY"])
def test_non_satellite_watchlist_roles_cannot_receive_plans(inputs, role):
    inputs["watchlist_item"] = inputs["watchlist_item"].model_copy(update={"role": role})
    with pytest.raises(ValueError, match="SATELLITE_ELIGIBLE"):
        build_trade_plan(**inputs)


def test_missed_run_or_failed_quality_cannot_create_normal_plan(inputs):
    inputs["run"] = inputs["run"].model_copy(
        update={"delivery_status": DeliveryStatus.MISSED_WINDOW}
    )
    with pytest.raises(ValueError, match="missed"):
        build_trade_plan(**inputs)
    inputs["run"] = inputs["run"].model_copy(
        update={
            "delivery_status": DeliveryStatus.ON_TIME,
            "data_quality_status": DataQualityStatus.FAIL,
        }
    )
    with pytest.raises(ValueError, match="quality"):
        build_trade_plan(**inputs)


def test_lifetime_sessions_skip_xnys_holiday():
    start = datetime(2026, 11, 25, 13, tzinfo=UTC)
    assert _expiry_time(start, 1) == datetime(2026, 11, 27, 13, tzinfo=UTC)


def test_late_window_requires_review_and_uses_actual_generation_time(inputs):
    late = datetime(2026, 9, 22, 13, 27, tzinfo=UTC)
    inputs["run"] = inputs["run"].model_copy(
        update={
            "invoked_at": late,
            "delivery_status": DeliveryStatus.DELAYED,
        }
    )
    plan = build_trade_plan(**inputs)
    assert plan.plan_status is PlanStatus.REVIEW_REQUIRED
    assert plan.generated_at == late
    assert plan.valid_from == late


def test_after_open_window_rejects_new_plan(inputs):
    inputs["run"] = inputs["run"].model_copy(
        update={
            "invoked_at": datetime(2026, 9, 22, 13, 31, tzinfo=UTC),
            "delivery_status": DeliveryStatus.DELAYED,
        }
    )
    with pytest.raises(ValueError, match="missed"):
        build_trade_plan(**inputs)


@pytest.mark.parametrize(
    "change",
    [
        {"direction": "SHORT"},
        {"entry_condition": "100"},
        {"counter_thesis": ""},
        {"invalidation_condition": ""},
        {"no_trade_conditions": ()},
        {"supporting_evidence": ()},
        {"expires_at": None},
    ],
)
def test_plan_rejects_incomplete_or_non_long_record(inputs, change):
    plan = build_trade_plan(**inputs)
    with pytest.raises(ValidationError):
        TradePlanDraft.model_validate({**plan.model_dump(), **change})


def test_plan_rejects_stop_at_entry(inputs):
    plan = build_trade_plan(**inputs)
    stop = plan.candidate_stop.model_copy(update={"value": plan.entry_zone.lower.value})
    with pytest.raises(ValidationError):
        TradePlanDraft.model_validate({**plan.model_dump(), "candidate_stop": stop})


def test_blocked_plan_cannot_keep_available_sizing(inputs):
    plan = build_trade_plan(**inputs)
    with pytest.raises(ValidationError):
        TradePlanDraft.model_validate({**plan.model_dump(), "plan_status": PlanStatus.BLOCKED})
