from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, Inexact, localcontext

import pytest

from finance_research_agent.application.market_bridge import (
    historical_instrument_identity,
    historical_to_canonical_snapshot,
)
from finance_research_agent.domain.enums import Coverage, ObservationOutcome, PlanStatus, Session
from finance_research_agent.domain.market import DailyBar
from finance_research_agent.domain.models import CompletedDailyBar
from finance_research_agent.domain.observations import observe_prior_plan
from finance_research_agent.domain.plans import build_trade_plan, expire_plan
from finance_research_agent.market_data.historical import (
    BarAdjustment,
    DailyBarObservation,
    HistoricalBarsProvenance,
    HistoricalDailyBars,
    MarketDataFeed,
    coverage_for_feed,
)

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
    creation = _bar(plan, date(2026, 9, 22), entry - 20, entry - 10, entry - 15)
    bar = _bar(plan, date(2026, 9, 23), stop - 1, target + 1, entry)
    result = observe_prior_plan(plan, (creation, bar), date(2026, 9, 23))
    assert ObservationOutcome.AMBIGUOUS_SEQUENCE in result.outcomes
    assert result.observation_reference_price == entry
    assert result.observation_reference_evidence_id == plan.entry_zone.upper.evidence_id
    assert not hasattr(result, "realized_profit_loss")
    assert not hasattr(result, "fill")


def test_mfe_mae_begin_only_after_entry_zone_observation(inputs):
    plan = build_trade_plan(**inputs)
    entry = plan.entry_zone.upper.value
    before = _bar(plan, date(2026, 9, 22), entry - 20, entry - 10, entry - 15)
    after = _bar(plan, date(2026, 9, 23), entry - 4, entry + 5, entry)
    result = observe_prior_plan(plan, (before, after), date(2026, 9, 23))
    assert result.mfe == Decimal("5")
    assert result.mae == Decimal("-4")
    assert ObservationOutcome.ENTRY_ZONE_OBSERVED in result.outcomes
    assert before.evidence_id in result.evidence_ids


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


def test_creation_date_completed_session_is_observed_when_plan_precedes_open(inputs):
    plan = build_trade_plan(**inputs)
    entry = plan.entry_zone.upper.value
    creation_day = _bar(plan, date(2026, 9, 22), entry - 1, entry + 1, entry)
    result = observe_prior_plan(plan, (creation_day,), date(2026, 9, 22))
    assert ObservationOutcome.ENTRY_ZONE_OBSERVED in result.outcomes


def test_expiry_date_bar_after_premarket_expiry_is_excluded(inputs):
    plan = build_trade_plan(**inputs).model_copy(
        update={
            "expires_at": datetime(2026, 9, 23, 12, 45, tzinfo=UTC),
        }
    )
    entry = plan.entry_zone.upper.value
    creation = _bar(plan, date(2026, 9, 22), entry - 20, entry - 10, entry - 15)
    expiry_day = _bar(plan, date(2026, 9, 23), entry - 1, entry + 1, entry)
    result = observe_prior_plan(plan, (creation, expiry_day), date(2026, 9, 23))
    assert ObservationOutcome.ENTRY_ZONE_NOT_OBSERVED in result.outcomes
    assert ObservationOutcome.ENTRY_ZONE_OBSERVED not in result.outcomes


@pytest.mark.parametrize("dates", [(), (date(2026, 9, 22), date(2026, 9, 24))])
def test_empty_or_gapped_completed_sessions_cannot_claim_no_entry(inputs, dates):
    plan = build_trade_plan(**inputs)
    entry = plan.entry_zone.upper.value
    bars = tuple(_bar(plan, day, entry - 20, entry - 10, entry - 15) for day in dates)
    with pytest.raises(ValueError, match="coverage"):
        observe_prior_plan(plan, bars, date(2026, 9, 24))


@pytest.mark.parametrize(
    "quote_change",
    [
        {
            "observed_at": datetime(2026, 9, 23, 12, 46, tzinfo=UTC),
            "retrieved_at": datetime(2026, 9, 23, 12, 46, tzinfo=UTC),
        },
        {"quality_flags": ("SOURCE_CONFLICT",)},
        {
            "observed_at": datetime(2026, 9, 21, 12, tzinfo=UTC),
            "retrieved_at": datetime(2026, 9, 21, 12, tzinfo=UTC),
        },
    ],
)
def test_untrusted_quote_never_triggers_entry_zone_departure(inputs, quote_change):
    plan = build_trade_plan(**inputs)
    outside = inputs["current_price"].model_copy(
        update={
            "value": plan.entry_zone.upper.value + Decimal("10"),
            **quote_change,
        }
    )
    expired = expire_plan(plan, now_utc=plan.valid_from, current_price=outside)
    assert "DATA_STALE_OR_CONFLICTING" in expired.expiry_reasons
    assert "ENTRY_ZONE_DEPARTED" not in expired.expiry_reasons


def test_canonical_market_bridge_bar_with_disclosures_is_observable(inputs):
    plan = build_trade_plan(**inputs)
    day = date(2026, 9, 22)
    source_at = datetime(2026, 9, 22, 4, tzinfo=UTC)
    complete_at = datetime(2026, 9, 23, 12, tzinfo=UTC)
    entry = plan.entry_zone.upper.value
    history = HistoricalDailyBars.create(
        symbol=plan.symbol,
        observations=(
            DailyBarObservation(
                source_timestamp=source_at,
                bar=DailyBar(
                    session_date=day,
                    open=entry,
                    high=entry + 1,
                    low=entry - 1,
                    close=entry,
                    volume=1000,
                ),
            ),
        ),
        provenance=HistoricalBarsProvenance(
            provider="alpaca",
            feed=MarketDataFeed.IEX,
            coverage=coverage_for_feed(MarketDataFeed.IEX),
            adjustment=BarAdjustment.SPLIT,
            requested_start_at=source_at,
            requested_end_at=datetime(2026, 9, 23, 3, 59, 59, tzinfo=UTC),
            retrieved_at=complete_at,
            evidence_cutoff_at=complete_at,
            completed_through_session=day,
            adapter_version="alpaca-daily-bars-v1",
        ),
        quality_flags=("ADJUSTMENT_SPLIT", "FEED_IEX_SINGLE_EXCHANGE"),
    )
    snapshot = historical_to_canonical_snapshot(
        history, instrument=historical_instrument_identity(history)
    )
    bridge_bar = snapshot.completed_daily_bars[0].model_copy(
        update={"instrument_id": plan.entry_zone.upper.instrument_id}
    )
    result = observe_prior_plan(plan, (bridge_bar,), day)
    assert ObservationOutcome.ENTRY_ZONE_OBSERVED in result.outcomes
    assert bridge_bar.evidence_id in result.evidence_ids
    assert result.entry_zone_observed_at == datetime(2026, 9, 22, 20, tzinfo=UTC)
    assert bridge_bar.source_timestamp == source_at


def test_unreliable_completed_bar_cannot_support_path_observation(inputs):
    plan = build_trade_plan(**inputs)
    bar = _bar(
        plan,
        date(2026, 9, 22),
        plan.entry_zone.lower.value,
        plan.entry_zone.upper.value,
        plan.entry_zone.upper.value,
    ).model_copy(update={"quality_flags": ("SOURCE_CONFLICT",)})
    with pytest.raises(ValueError, match="unreliable"):
        observe_prior_plan(plan, (bar,), date(2026, 9, 22))


def test_premarket_early_expiry_excludes_same_day_bar(inputs):
    plan = build_trade_plan(**inputs)
    expired = expire_plan(plan, now_utc=plan.valid_from, new_material_information=True)
    bar = _bar(
        plan,
        date(2026, 9, 22),
        plan.entry_zone.lower.value,
        plan.entry_zone.upper.value,
        plan.entry_zone.upper.value,
    )
    with pytest.raises(ValueError, match="coverage"):
        observe_prior_plan(expired, (bar,), date(2026, 9, 22))


def test_intraday_early_expiry_excludes_straddling_bar(inputs):
    plan = build_trade_plan(**inputs)
    cutoff = datetime(2026, 9, 23, 16, tzinfo=UTC)
    expired = expire_plan(plan, now_utc=cutoff, new_material_information=True)
    expired = type(expired).model_validate_json(expired.model_dump_json())
    entry = plan.entry_zone.upper.value
    before = _bar(plan, date(2026, 9, 22), entry - 20, entry - 10, entry - 15)
    straddling = _bar(plan, date(2026, 9, 23), entry - 1, entry + 1, entry)
    assert expired.effective_expiry_at == cutoff
    with pytest.raises(ValueError, match="intraday expiry"):
        observe_prior_plan(expired, (before, straddling), date(2026, 9, 23))


def test_observation_excursions_ignore_ambient_decimal_context(inputs):
    plan = build_trade_plan(**inputs)
    entry = plan.entry_zone.upper.value
    bar = _bar(
        plan, date(2026, 9, 22), entry - Decimal("1.23456"), entry + Decimal("2.34567"), entry
    )
    baseline = observe_prior_plan(plan, (bar,), date(2026, 9, 22))
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        altered = observe_prior_plan(plan, (bar,), date(2026, 9, 22))
    assert altered == baseline
