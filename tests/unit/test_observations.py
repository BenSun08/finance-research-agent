from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from finance_research_agent.domain.enums import Coverage, ObservationOutcome, PlanStatus, Session
from finance_research_agent.domain.models import CompletedDailyBar
from finance_research_agent.domain.observations import observe_prior_plan
from finance_research_agent.domain.plans import build_trade_plan, expire_plan

pytest_plugins = ("tests.unit.test_trade_plan",)


def _bar(plan, when, low, high, close):
    return CompletedDailyBar(
        instrument_id=plan.entry_zone.upper.instrument_id,
        session_date=when,
        source_timestamp=datetime.combine(when, datetime.min.time(), tzinfo=UTC)
        + timedelta(hours=21),
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=1000,
        session=Session.COMPLETED_SESSION,
        provider="fixture",
        feed="sip",
        coverage=Coverage.CONSOLIDATED,
        adjustment="split",
        retrieved_at=datetime.combine(when, datetime.min.time(), tzinfo=UTC) + timedelta(hours=22),
        evidence_cutoff_at=datetime.combine(when, datetime.min.time(), tzinfo=UTC)
        + timedelta(hours=22),
        evidence_id=f"bar-{when.isoformat()}",
        quality_flags=(),
    )


def test_same_bar_entry_stop_and_target_is_ambiguous(inputs):
    plan = build_trade_plan(**inputs)
    entry = plan.entry_zone.upper.value
    stop = plan.candidate_stop.value
    target = plan.target_scenarios[0].price.value
    bar = _bar(plan, date(2026, 9, 23), stop - 1, target + 1, entry)
    result = observe_prior_plan(plan, (bar,), date(2026, 9, 23))
    assert ObservationOutcome.AMBIGUOUS_SEQUENCE in result.outcomes
    assert result.observation_reference_price == entry
    assert result.observation_reference_evidence_id == plan.entry_zone.upper.evidence_id
    assert not hasattr(result, "realized_profit_loss")
    assert not hasattr(result, "fill")


def test_mfe_mae_begin_only_after_entry_zone_observation(inputs):
    plan = build_trade_plan(**inputs)
    entry = plan.entry_zone.upper.value
    before = _bar(plan, date(2026, 9, 23), entry - 20, entry - 10, entry - 15)
    after = _bar(plan, date(2026, 9, 24), entry - 4, entry + 5, entry)
    result = observe_prior_plan(plan, (before, after), date(2026, 9, 24))
    assert result.mfe == Decimal("5")
    assert result.mae == Decimal("-4")
    assert ObservationOutcome.ENTRY_ZONE_OBSERVED in result.outcomes
    assert before.evidence_id not in result.evidence_ids


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({}, "EXPIRES_AT_PASSED"),
        ({"invalidation_observed": True}, "INVALIDATION_OBSERVED"),
        ({"new_material_information": True}, "NEW_MATERIAL_INFORMATION"),
        ({"earnings_blackout": True}, "EARNINGS_BLACKOUT"),
        ({"incompatible_regime": True}, "INCOMPATIBLE_REGIME"),
        ({"stale_or_conflicting_data": True}, "DATA_STALE_OR_CONFLICTING"),
        ({"eligibility_changed": True}, "ELIGIBILITY_CHANGED"),
    ],
)
def test_expiry_reasons_are_explicit(inputs, kwargs, reason):
    plan = build_trade_plan(**inputs)
    now = plan.expires_at if not kwargs else plan.valid_from
    result = expire_plan(plan, now_utc=now, **kwargs)
    assert result.plan_status is PlanStatus.EXPIRED
    assert reason in result.expiry_reasons
    assert result.position_sizing.suggested_units is None


def test_price_departure_before_trigger_expires(inputs):
    plan = build_trade_plan(**inputs)
    outside = inputs["current_price"].model_copy(
        update={"value": plan.entry_zone.upper.value + Decimal("10")}
    )
    result = expire_plan(plan, now_utc=plan.valid_from, current_price=outside)
    assert result.plan_status is PlanStatus.EXPIRED
    assert "ENTRY_ZONE_DEPARTED" in result.expiry_reasons
