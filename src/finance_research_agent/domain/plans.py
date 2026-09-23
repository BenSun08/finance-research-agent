"""Conditional Product A research plans and deterministic expiry."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, time, timedelta
from decimal import Context, Decimal, localcontext
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from finance_research_agent.domain.enums import (
    Capability,
    DataQualityStatus,
    DeliveryStatus,
    GateStatus,
    PlanStatus,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.events import EventAssessment
from finance_research_agent.domain.market_calendar import NEW_YORK
from finance_research_agent.domain.models import (
    CapabilityState,
    GateResult,
    Identifier,
    PriceObservation,
    RunContext,
    StrictModel,
    Symbol,
)
from finance_research_agent.domain.policies import (
    RiskPolicy,
    SetupPolicy,
    WatchlistItem,
    canonical_model_hash,
)
from finance_research_agent.domain.regime import Regime, RegimeResult
from finance_research_agent.domain.scoring import ScoreComponent, ScorePenalty, SetupCandidate
from finance_research_agent.domain.setups import CalculatedPrice, EntryZone, SetupType
from finance_research_agent.domain.sizing import (
    PositionSizing,
    SizingStatus,
    calculate_position_sizing,
)
from finance_research_agent.domain.types import UtcDatetime

_CONTEXT = Context(prec=28)
_NUMERIC_ONLY = re.compile(r"\s*[$+\-]?\d+(?:\.\d+)?\s*")


class PlanScoreBreakdown(StrictModel):
    components: tuple[ScoreComponent, ...]
    penalties: tuple[ScorePenalty, ...]
    positive_score: Decimal
    total_score: Decimal


class PlanTargetScenario(StrictModel):
    price: CalculatedPrice
    distance: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
    potential_reward: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
    r_multiple: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]


class TradePlanDraft(StrictModel):
    """A complete long research hypothesis; no approval or execution state."""

    plan_id: Identifier
    run_id: Identifier
    candidate_id: Identifier
    symbol: Symbol
    instrument_name: Annotated[str, Field(min_length=1, max_length=256)]
    watchlist_role: Literal["CORE_MONITOR", "SATELLITE_ELIGIBLE", "RESEARCH_ONLY"]
    direction: Literal["LONG"]
    setup_type: SetupType
    plan_status: PlanStatus
    generated_at: UtcDatetime
    evidence_cutoff_at: UtcDatetime
    valid_from: UtcDatetime
    expires_at: UtcDatetime
    market_regime: Regime
    candidate_score: Decimal
    score_breakdown: PlanScoreBreakdown
    thesis: Annotated[str, Field(min_length=1, max_length=1000)]
    counter_thesis: Annotated[str, Field(min_length=1, max_length=1000)]
    catalysts: tuple[str, ...]
    event_risks: tuple[str, ...]
    entry_condition: Annotated[str, Field(min_length=1, max_length=1000)]
    entry_zone: EntryZone
    invalidation_condition: Annotated[str, Field(min_length=1, max_length=1000)]
    candidate_stop: CalculatedPrice
    candidate_stop_role: Literal["ANALYTICAL_INVALIDATION"] = "ANALYTICAL_INVALIDATION"
    target_scenarios: tuple[PlanTargetScenario, ...] = Field(min_length=1)
    risk_per_unit: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
    reward_risk_by_target: tuple[Decimal, ...] = Field(min_length=1)
    position_sizing: PositionSizing
    no_trade_conditions: tuple[str, ...] = Field(min_length=1)
    supporting_evidence: tuple[Identifier, ...] = Field(min_length=1)
    counter_evidence: tuple[Identifier, ...]
    data_quality_flags: tuple[Identifier, ...]
    review_checklist: tuple[str, ...] = Field(min_length=1)
    gate_results: tuple[GateResult, ...]
    capability_states: tuple[CapabilityState, ...]
    setup_policy_version: str
    risk_policy_version: str
    regime_policy_version: str
    expiry_reasons: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def valid_conditional_plan(self) -> Self:
        if self.expires_at <= self.valid_from or self.generated_at < self.evidence_cutoff_at:
            raise ValueError("plan requires ordered generation, validity, and expiry times")
        if self.plan_status in (PlanStatus.BLOCKED, PlanStatus.EXPIRED) and (
            self.position_sizing.status is not SizingStatus.SIZING_UNAVAILABLE
        ):
            raise ValueError("blocked and expired plans cannot expose available sizing")
        if _NUMERIC_ONLY.fullmatch(self.entry_condition):
            raise ValueError("entry condition must contain a conditional trigger")
        if not self.counter_thesis.strip() or not self.invalidation_condition.strip():
            raise ValueError("counter-thesis and invalidation are required")
        if any(not value.strip() for value in self.no_trade_conditions):
            raise ValueError("no-trade conditions must be nonblank")
        if self.candidate_stop.value >= self.entry_zone.lower.value:
            raise ValueError("analytical stop must be below long entry zone")
        if self.risk_per_unit != self.entry_zone.upper.value - self.candidate_stop.value:
            raise ValueError("risk per unit must use the upper entry reference")
        if len(self.target_scenarios) != len(self.reward_risk_by_target):
            raise ValueError("every target requires a reward-risk value")
        for target, ratio in zip(self.target_scenarios, self.reward_risk_by_target):
            if target.price.value <= self.entry_zone.upper.value:
                raise ValueError("long targets must exceed the entry zone")
            if target.distance != target.price.value - self.entry_zone.upper.value:
                raise ValueError("target distance must use upper entry reference")
            if target.potential_reward != target.distance or target.r_multiple != ratio:
                raise ValueError("target reward and R multiple must match calculated values")
            if ratio != target.distance / self.risk_per_unit:
                raise ValueError("target R multiple must match risk per unit")
        return self


def _expiry_time(start: datetime, sessions: int) -> datetime:
    try:
        import exchange_calendars as xcals  # type: ignore[import-untyped]

        calendar = xcals.get_calendar("XNYS")
    except Exception as error:
        raise RuntimeError(
            f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: calendar unavailable"
        ) from error
    current = start
    remaining = sessions
    while remaining:
        current += timedelta(days=1)
        try:
            if calendar.is_session(current.date().isoformat()):
                remaining -= 1
        except Exception as error:
            raise RuntimeError(
                f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: calendar unavailable"
            ) from error
    return current


def _status(
    candidate: SetupCandidate,
    event: EventAssessment,
    gates: tuple[GateResult, ...],
    capabilities: tuple[CapabilityState, ...],
    risk: RiskPolicy,
) -> PlanStatus:
    if (
        candidate.plan_status is PlanStatus.BLOCKED
        or event.plan_status is PlanStatus.BLOCKED
        or candidate.data_quality.status is DataQualityStatus.FAIL
        or any(gate.status is GateStatus.BLOCK for gate in gates)
        or any(
            not state.available and state.capability is Capability.PLAN_DRAFT_AVAILABLE
            for state in capabilities
        )
        or risk.max_total_portfolio_heat_pct is None
    ):
        return PlanStatus.BLOCKED
    if (
        candidate.plan_status is PlanStatus.REVIEW_REQUIRED
        or event.plan_status is PlanStatus.REVIEW_REQUIRED
        or any(gate.status is GateStatus.WARNING for gate in gates)
        or any(not state.available for state in capabilities)
        or risk.existing_portfolio_heat_pct is None
    ):
        return PlanStatus.REVIEW_REQUIRED
    return PlanStatus.DRAFT


def build_trade_plan(
    candidate: SetupCandidate,
    run: RunContext,
    watchlist_item: WatchlistItem,
    regime: RegimeResult,
    event_assessment: EventAssessment,
    gates: Sequence[GateResult],
    current_price: PriceObservation | None,
    capability_states: Sequence[CapabilityState],
    setup_policy: SetupPolicy,
    risk_policy: RiskPolicy,
) -> TradePlanDraft:
    """Project a selected, evidence-bounded candidate into one conditional plan."""

    if not candidate.selected_for_plan:
        raise ValueError("candidate must be selected for a plan")
    if candidate.symbol != watchlist_item.symbol:
        raise ValueError("candidate and watchlist symbol differ")
    if watchlist_item.role != "SATELLITE_ELIGIBLE":
        raise ValueError("only SATELLITE_ELIGIBLE watchlist items may receive plans")
    local_invoked = run.invoked_at.astimezone(NEW_YORK).time()
    if run.delivery_status is DeliveryStatus.MISSED_WINDOW or local_invoked >= time(9, 30):
        raise ValueError("missed premarket run cannot create a plan")
    if run.data_quality_status is DataQualityStatus.FAIL:
        raise ValueError("failed run data quality cannot create a plan")
    if candidate.event_assessment != event_assessment:
        raise ValueError("event assessment differs from selected candidate")
    if candidate.policy_hash != canonical_model_hash(setup_policy):
        raise ValueError("setup policy differs from selected candidate")
    if candidate.regime_policy_version != regime.policy_version:
        raise ValueError("regime policy differs from selected candidate")
    if candidate.evidence_cutoff_at != run.evidence_cutoff_at:
        raise ValueError("candidate and run evidence cutoffs differ")
    if current_price is not None and current_price.retrieved_at > run.evidence_cutoff_at:
        raise ValueError("current price is after the evidence cutoff")
    gate_results = tuple(dict.fromkeys((*candidate.event_assessment.gates, *gates)))
    states = tuple(capability_states)
    if len(states) != len(Capability) or {state.capability for state in states} != set(Capability):
        raise ValueError("capability states must cover each Product A capability exactly once")
    generated_at = max(run.invoked_at, run.evidence_cutoff_at)
    late_review = local_invoked >= time(9, 25)
    with localcontext(_CONTEXT):
        entry = candidate.levels.entry_zone.upper.value
        stop = candidate.levels.candidate_stop.value
        per_unit = entry - stop
        if per_unit <= 0:
            raise ValueError("candidate stop must be below long entry reference")
        targets = tuple(
            PlanTargetScenario(
                price=target.price,
                distance=target.price.value - entry,
                potential_reward=target.price.value - entry,
                r_multiple=(target.price.value - entry) / per_unit,
            )
            for target in candidate.levels.target_scenarios
        )
        evidence = tuple(
            dict.fromkeys(
                (
                    candidate.levels.entry_zone.lower.evidence_id,
                    candidate.levels.entry_zone.upper.evidence_id,
                    candidate.levels.candidate_stop.evidence_id,
                    *(target.price.evidence_id for target in targets),
                    *(eid for component in candidate.components for eid in component.evidence_ids),
                )
            )
        )
        flags = tuple(
            dict.fromkeys(
                (
                    *(code.value for code in candidate.data_quality.global_reason_codes),
                    *event_assessment.quality_flags,
                    *(
                        flag
                        for level in (
                            candidate.levels.entry_zone.upper,
                            candidate.levels.candidate_stop,
                        )
                        for flag in level.quality_flags
                    ),
                    *(
                        ("PORTFOLIO_HEAT_UNAVAILABLE",)
                        if risk_policy.existing_portfolio_heat_pct is None
                        else ()
                    ),
                )
            )
        )
        status = _status(candidate, event_assessment, gate_results, states, risk_policy)
        if status is PlanStatus.DRAFT and (
            late_review or run.data_quality_status is DataQualityStatus.DEGRADED
        ):
            status = PlanStatus.REVIEW_REQUIRED
        checklist = ["Independent human review of evidence, conditions, and risk policy"]
        if late_review:
            checklist.append("Confirm sufficient time remains for independent review")
        if risk_policy.existing_portfolio_heat_pct is None:
            checklist.append("Verify current portfolio heat manually")
        checklist.extend(g.message for g in gate_results if g.status is GateStatus.WARNING)
        draft = TradePlanDraft(
            plan_id=f"{run.run_id}-{candidate.candidate_id}",
            run_id=run.run_id,
            candidate_id=candidate.candidate_id,
            symbol=candidate.symbol,
            instrument_name=candidate.symbol,
            watchlist_role=watchlist_item.role,
            direction="LONG",
            setup_type=candidate.setup_type,
            plan_status=status,
            generated_at=generated_at,
            evidence_cutoff_at=run.evidence_cutoff_at,
            valid_from=generated_at,
            expires_at=_expiry_time(generated_at, setup_policy.plan_lifetime_sessions),
            market_regime=regime.regime,
            candidate_score=candidate.total_score,
            score_breakdown=PlanScoreBreakdown(
                components=candidate.components,
                penalties=candidate.penalties,
                positive_score=candidate.positive_score,
                total_score=candidate.total_score,
            ),
            thesis=(
                f"Conditional {candidate.setup_type.value} hypothesis: {candidate.entry_condition}"
            ),
            counter_thesis=f"The hypothesis weakens if {candidate.invalidation_condition}",
            catalysts=(),
            event_risks=event_assessment.event_risks,
            entry_condition=candidate.entry_condition,
            entry_zone=candidate.levels.entry_zone,
            invalidation_condition=candidate.invalidation_condition,
            candidate_stop=candidate.levels.candidate_stop,
            target_scenarios=targets,
            risk_per_unit=per_unit,
            reward_risk_by_target=tuple(target.r_multiple for target in targets),
            position_sizing=PositionSizing(
                status=SizingStatus.SIZING_UNAVAILABLE,
                unavailable_reasons=("CALCULATION_PENDING",),
                calculated_at=generated_at,
            ),
            no_trade_conditions=tuple(
                dict.fromkeys(
                    (
                        *event_assessment.no_trade_conditions,
                        "Do not proceed if the conditional entry trigger or evidence gates fail",
                    )
                )
            ),
            supporting_evidence=evidence,
            counter_evidence=tuple(
                dict.fromkeys(
                    eid for penalty in candidate.penalties for eid in penalty.evidence_ids
                )
            ),
            data_quality_flags=flags,
            review_checklist=tuple(checklist),
            gate_results=gate_results,
            capability_states=states,
            setup_policy_version=setup_policy.version,
            risk_policy_version=risk_policy.version,
            regime_policy_version=regime.policy_version,
        )
        sizing = calculate_position_sizing(
            draft, risk_policy, regime.regime, current_price, generated_at
        )
        if sizing.status is SizingStatus.SIZING_UNAVAILABLE:
            status = PlanStatus.BLOCKED
        elif (
            sizing.estimated_post_plan_heat_pct is not None
            and risk_policy.max_total_portfolio_heat_pct is not None
            and sizing.estimated_post_plan_heat_pct > risk_policy.max_total_portfolio_heat_pct
        ):
            status = PlanStatus.BLOCKED
            sizing = sizing.model_copy(
                update={
                    "status": SizingStatus.SIZING_UNAVAILABLE,
                    "unavailable_reasons": ("PORTFOLIO_HEAT_LIMIT_EXCEEDED",),
                    "suggested_units": None,
                }
            )
        return draft.model_copy(update={"plan_status": status, "position_sizing": sizing})


def expire_plan(
    plan: TradePlanDraft,
    *,
    now_utc: datetime,
    current_price: PriceObservation | None = None,
    entry_trigger_satisfied: bool = False,
    invalidation_observed: bool = False,
    new_material_information: bool = False,
    earnings_blackout: bool = False,
    incompatible_regime: bool = False,
    stale_or_conflicting_data: bool = False,
    eligibility_changed: bool = False,
) -> TradePlanDraft:
    """End a plan's validity from explicit time, price, evidence, or gate changes."""

    if now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
        raise ValueError("now_utc must be UTC")
    reasons: list[str] = []
    if now_utc >= plan.expires_at:
        reasons.append("EXPIRES_AT_PASSED")
    if current_price is not None:
        if current_price.instrument_id != plan.entry_zone.upper.instrument_id:
            raise ValueError("price instrument differs from plan")
        if not entry_trigger_satisfied and not (
            plan.entry_zone.lower.value <= current_price.value <= plan.entry_zone.upper.value
        ):
            reasons.append("ENTRY_ZONE_DEPARTED")
    for condition, reason in (
        (invalidation_observed, "INVALIDATION_OBSERVED"),
        (new_material_information, "NEW_MATERIAL_INFORMATION"),
        (earnings_blackout, "EARNINGS_BLACKOUT"),
        (incompatible_regime, "INCOMPATIBLE_REGIME"),
        (stale_or_conflicting_data, "DATA_STALE_OR_CONFLICTING"),
        (eligibility_changed, "ELIGIBILITY_CHANGED"),
    ):
        if condition:
            reasons.append(reason)
    if not reasons:
        return plan
    return plan.model_copy(
        update={
            "plan_status": PlanStatus.EXPIRED,
            "expiry_reasons": tuple(dict.fromkeys((*plan.expiry_reasons, *reasons))),
            "position_sizing": plan.position_sizing.model_copy(
                update={
                    "status": SizingStatus.SIZING_UNAVAILABLE,
                    "unavailable_reasons": ("PLAN_EXPIRED",),
                    "suggested_units": None,
                }
            ),
        }
    )
