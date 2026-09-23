"""Decimal-only, conditional research sizing. No quantity is an order instruction."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Context, Decimal, localcontext
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Self

from pydantic import Field, model_validator

from finance_research_agent.domain.enums import PlanStatus
from finance_research_agent.domain.models import PriceObservation, StrictModel
from finance_research_agent.domain.policies import RiskPolicy
from finance_research_agent.domain.regime import Regime
from finance_research_agent.domain.types import UtcDatetime

if TYPE_CHECKING:
    from finance_research_agent.domain.plans import TradePlanDraft

_CONTEXT = Context(prec=28)


class SizingStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    SIZING_UNAVAILABLE = "SIZING_UNAVAILABLE"


class PositionSizing(StrictModel):
    """Auditable per-plan calculation, never a position or execution record."""

    status: SizingStatus
    unavailable_reasons: tuple[str, ...] = ()
    planning_capital_usd: Decimal | None = None
    max_risk_per_trade_pct: Decimal | None = None
    regime_risk_multiplier: Decimal | None = None
    max_position_pct: Decimal | None = None
    entry_reference_price: Decimal | None = None
    candidate_stop_price: Decimal | None = None
    base_risk_budget: Decimal | None = None
    adjusted_risk_budget: Decimal | None = None
    risk_per_unit: Decimal | None = None
    units_by_risk: Decimal | None = None
    units_by_position_cap: Decimal | None = None
    suggested_units: Decimal | None = None
    estimated_plan_risk_pct: Decimal | None = None
    estimated_post_plan_heat_pct: Decimal | None = None
    calculated_at: UtcDatetime
    quantity_increment: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def coherent_status(self) -> Self:
        if self.status is SizingStatus.AVAILABLE:
            if (
                self.unavailable_reasons
                or self.suggested_units is None
                or self.suggested_units <= 0
            ):
                raise ValueError(
                    "available sizing requires positive units and no unavailable reasons"
                )
            if any(
                value is None
                for value in (
                    self.base_risk_budget,
                    self.adjusted_risk_budget,
                    self.risk_per_unit,
                    self.units_by_risk,
                    self.units_by_position_cap,
                )
            ):
                raise ValueError("available sizing requires every intermediate")
        elif not self.unavailable_reasons or self.suggested_units is not None:
            raise ValueError("unavailable sizing requires reasons and no suggested units")
        return self


def _fresh(price: PriceObservation | None, now_utc: datetime, max_age_seconds: int | None) -> bool:
    return (
        price is not None
        and max_age_seconds is not None
        and max_age_seconds > 0
        and price.observed_at <= now_utc
        and price.retrieved_at <= now_utc
        and now_utc - price.observed_at <= timedelta(seconds=max_age_seconds)
        and not any(
            any(problem in flag.upper() for problem in ("STALE", "CONFLICT", "UNRELIABLE"))
            for flag in price.quality_flags
        )
    )


def calculate_position_sizing(
    plan: TradePlanDraft,
    risk_policy: RiskPolicy,
    regime: Regime,
    current_price: PriceObservation | None,
    now_utc: datetime,
) -> PositionSizing:
    """Apply exact policy caps with downward rounding and explicit unavailability."""

    if now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
        raise ValueError("now_utc must be UTC")
    entry = plan.entry_zone.upper.value
    stop = plan.candidate_stop.value
    capital = risk_policy.planning_capital_usd
    risk_pct = risk_policy.max_risk_per_trade_pct
    position_pct = risk_policy.max_position_pct
    minimum_rr = risk_policy.minimum_reward_risk_ratio
    multiplier = risk_policy.regime_risk_multipliers.get(regime.name)
    reasons: list[str] = []
    if plan.plan_status is PlanStatus.BLOCKED:
        reasons.append("PLAN_BLOCKED")
    elif plan.plan_status is PlanStatus.EXPIRED or now_utc >= plan.expires_at:
        reasons.append("PLAN_EXPIRED")
    if not risk_policy.sizing_enabled:
        reasons.append("SIZING_DISABLED")
    if capital is None:
        reasons.append("PLANNING_CAPITAL_MISSING")
    if any(value is None for value in (risk_pct, position_pct, minimum_rr, multiplier)):
        reasons.append("RISK_POLICY_INCOMPLETE")
    if stop >= entry:
        reasons.append("STOP_NOT_BELOW_ENTRY")
    if plan.current_price_freshness_seconds is None:
        reasons.append("CURRENT_PRICE_FRESHNESS_UNAVAILABLE")
    if plan.stop_freshness_seconds is None:
        reasons.append("STOP_FRESHNESS_UNAVAILABLE")
    if not _fresh(current_price, now_utc, plan.current_price_freshness_seconds) or (
        current_price is not None
        and current_price.instrument_id != plan.entry_zone.upper.instrument_id
    ):
        reasons.append("CURRENT_PRICE_STALE")
    if not _fresh(plan.candidate_stop, now_utc, plan.stop_freshness_seconds):
        reasons.append("STOP_EVIDENCE_STALE")
    if minimum_rr is not None and max(plan.reward_risk_by_target, default=Decimal(0)) < minimum_rr:
        reasons.append("REWARD_RISK_BELOW_POLICY")
    if reasons:
        return PositionSizing(
            status=SizingStatus.SIZING_UNAVAILABLE,
            unavailable_reasons=tuple(dict.fromkeys(reasons)),
            planning_capital_usd=capital,
            max_risk_per_trade_pct=risk_pct,
            regime_risk_multiplier=multiplier,
            max_position_pct=position_pct,
            entry_reference_price=entry,
            candidate_stop_price=stop,
            calculated_at=now_utc,
            quantity_increment=risk_policy.quantity_increment,
        )
    assert capital is not None and risk_pct is not None and position_pct is not None
    assert multiplier is not None
    with localcontext(_CONTEXT):
        base = capital * risk_pct
        adjusted = base * multiplier
        per_unit = entry - stop
        if per_unit <= 0:
            return PositionSizing(
                status=SizingStatus.SIZING_UNAVAILABLE,
                unavailable_reasons=("NONPOSITIVE_RISK_PER_UNIT",),
                planning_capital_usd=capital,
                max_risk_per_trade_pct=risk_pct,
                regime_risk_multiplier=multiplier,
                max_position_pct=position_pct,
                entry_reference_price=entry,
                candidate_stop_price=stop,
                base_risk_budget=base,
                adjusted_risk_budget=adjusted,
                risk_per_unit=per_unit,
                calculated_at=now_utc,
                quantity_increment=risk_policy.quantity_increment,
            )
        increment = (
            risk_policy.quantity_increment if risk_policy.allow_fractional_units else Decimal(1)
        )
        by_risk = (adjusted / per_unit / increment).to_integral_value(
            rounding=ROUND_FLOOR
        ) * increment
        by_position = (capital * position_pct / entry / increment).to_integral_value(
            rounding=ROUND_FLOOR
        ) * increment
        suggested = min(by_risk, by_position)
        if suggested <= 0:
            return PositionSizing(
                status=SizingStatus.SIZING_UNAVAILABLE,
                unavailable_reasons=("NO_POSITIVE_UNITS",),
                planning_capital_usd=capital,
                max_risk_per_trade_pct=risk_pct,
                regime_risk_multiplier=multiplier,
                max_position_pct=position_pct,
                entry_reference_price=entry,
                candidate_stop_price=stop,
                base_risk_budget=base,
                adjusted_risk_budget=adjusted,
                risk_per_unit=per_unit,
                units_by_risk=by_risk,
                units_by_position_cap=by_position,
                calculated_at=now_utc,
                quantity_increment=increment,
            )
        plan_risk_pct = suggested * per_unit / capital
        existing_heat = risk_policy.existing_portfolio_heat_pct
        post_heat = existing_heat + plan_risk_pct if existing_heat is not None else None
        return PositionSizing(
            status=SizingStatus.AVAILABLE,
            planning_capital_usd=capital,
            max_risk_per_trade_pct=risk_pct,
            regime_risk_multiplier=multiplier,
            max_position_pct=position_pct,
            entry_reference_price=entry,
            candidate_stop_price=stop,
            base_risk_budget=base,
            adjusted_risk_budget=adjusted,
            risk_per_unit=per_unit,
            units_by_risk=by_risk,
            units_by_position_cap=by_position,
            suggested_units=suggested,
            estimated_plan_risk_pct=plan_risk_pct,
            estimated_post_plan_heat_pct=post_heat,
            calculated_at=now_utc,
            quantity_increment=increment,
        )
