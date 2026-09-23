from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finance_research_agent.domain.enums import PlanStatus
from finance_research_agent.domain.sizing import (
    PositionSizing,
    SizingStatus,
    calculate_position_sizing,
)

pytest_plugins = ("tests.unit.test_trade_plan",)


def test_decimal_sizing_uses_risk_and_position_caps(inputs):
    from finance_research_agent.domain.plans import build_trade_plan

    plan = build_trade_plan(**inputs)
    price = plan.entry_zone.upper.value
    stop = plan.candidate_stop.value
    risk = inputs["risk_policy"]
    result = calculate_position_sizing(
        plan, risk, plan.market_regime, inputs["current_price"], plan.generated_at
    )
    assert result.base_risk_budget == Decimal("100")
    assert result.adjusted_risk_budget == Decimal("50")
    assert result.risk_per_unit == price - stop
    assert result.units_by_risk == (Decimal("50") / (price - stop)).to_integral_value(
        rounding="ROUND_FLOOR"
    )
    assert result.units_by_position_cap == (Decimal("1000") / price).to_integral_value(
        rounding="ROUND_FLOOR"
    )
    assert result.suggested_units == min(result.units_by_risk, result.units_by_position_cap)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"planning_capital_usd": None}, "PLANNING_CAPITAL_MISSING"),
        ({"max_risk_per_trade_pct": None}, "RISK_POLICY_INCOMPLETE"),
        ({"minimum_reward_risk_ratio": Decimal("100")}, "REWARD_RISK_BELOW_POLICY"),
    ],
)
def test_unavailable_reasons_are_explicit(inputs, change, reason):
    from finance_research_agent.domain.plans import build_trade_plan

    plan = build_trade_plan(**inputs)
    risk = inputs["risk_policy"].model_copy(update=change)
    result = calculate_position_sizing(
        plan, risk, plan.market_regime, inputs["current_price"], plan.generated_at
    )
    assert result.status.value == "SIZING_UNAVAILABLE"
    assert reason in result.unavailable_reasons
    assert result.suggested_units is None


def test_stale_current_price_and_blocked_plan_are_unavailable(inputs):
    from finance_research_agent.domain.plans import build_trade_plan

    plan = build_trade_plan(**inputs)
    stale = inputs["current_price"].model_copy(
        update={"observed_at": plan.generated_at - timedelta(days=2)}
    )
    result = calculate_position_sizing(
        plan, inputs["risk_policy"], plan.market_regime, stale, plan.generated_at
    )
    assert "CURRENT_PRICE_STALE" in result.unavailable_reasons
    blocked = plan.model_copy(update={"plan_status": PlanStatus.BLOCKED})
    result = calculate_position_sizing(
        blocked,
        inputs["risk_policy"],
        plan.market_regime,
        inputs["current_price"],
        plan.generated_at,
    )
    assert "PLAN_BLOCKED" in result.unavailable_reasons


def test_fractional_quantity_rounds_down_to_increment(inputs):
    from finance_research_agent.domain.plans import build_trade_plan

    plan = build_trade_plan(**inputs)
    risk = inputs["risk_policy"].model_copy(
        update={"allow_fractional_units": True, "quantity_increment": Decimal("0.25")}
    )
    result = calculate_position_sizing(
        plan, risk, plan.market_regime, inputs["current_price"], plan.generated_at
    )
    assert result.suggested_units is not None
    assert result.suggested_units % Decimal("0.25") == 0


def test_zero_units_are_unavailable_instead_of_suggesting_zero(inputs):
    from finance_research_agent.domain.plans import build_trade_plan

    plan = build_trade_plan(**inputs)
    risk = inputs["risk_policy"].model_copy(update={"planning_capital_usd": Decimal("1")})
    result = calculate_position_sizing(
        plan, risk, plan.market_regime, inputs["current_price"], plan.generated_at
    )
    assert result.status.value == "SIZING_UNAVAILABLE"
    assert "NO_POSITIVE_UNITS" in result.unavailable_reasons
    assert result.suggested_units is None


def test_sizing_model_rejects_available_without_positive_quantity(inputs):
    from finance_research_agent.domain.plans import build_trade_plan

    plan = build_trade_plan(**inputs)
    with pytest.raises(ValidationError):
        PositionSizing.model_validate(
            {
                **plan.position_sizing.model_dump(),
                "status": SizingStatus.AVAILABLE,
                "suggested_units": None,
            }
        )


def test_twenty_hour_quote_is_unavailable_under_frozen_price_policy(inputs):
    from finance_research_agent.domain.plans import build_trade_plan

    plan = build_trade_plan(**inputs)
    old = plan.generated_at - timedelta(hours=20)
    old_quote = inputs["current_price"].model_copy(
        update={
            "observed_at": old,
            "retrieved_at": old,
        }
    )
    sizing = calculate_position_sizing(
        plan, inputs["risk_policy"], plan.market_regime, old_quote, plan.generated_at
    )
    assert sizing.status is SizingStatus.SIZING_UNAVAILABLE
    assert "CURRENT_PRICE_STALE" in sizing.unavailable_reasons
