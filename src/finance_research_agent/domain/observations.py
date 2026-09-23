"""Completed-bar path observations for prior conditional research plans."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from finance_research_agent.domain.enums import ObservationOutcome
from finance_research_agent.domain.models import CompletedDailyBar, Identifier, StrictModel
from finance_research_agent.domain.plans import TradePlanDraft
from finance_research_agent.domain.types import UtcDatetime


class PlanObservation(StrictModel):
    """Market path evidence only; no fill, position, or realized return fields."""

    plan_id: Identifier
    observed_through: date
    outcomes: tuple[ObservationOutcome, ...] = Field(min_length=1)
    observation_reference_price: Decimal
    observation_reference_evidence_id: Identifier
    entry_zone_observed_at: UtcDatetime | None
    invalidation_observed_at: UtcDatetime | None
    target_observed_at: UtcDatetime | None
    mfe: Decimal | None
    mae: Decimal | None
    evidence_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def coherent_path(self) -> Self:
        entry_seen = ObservationOutcome.ENTRY_ZONE_OBSERVED in self.outcomes
        if entry_seen != (self.entry_zone_observed_at is not None):
            raise ValueError("entry outcome and timestamp must agree")
        if not entry_seen and (self.mfe is not None or self.mae is not None):
            raise ValueError("excursions require an observed entry zone")
        if self.mfe is not None and self.mfe < 0:
            raise ValueError("MFE must be nonnegative")
        if self.mae is not None and self.mae > 0:
            raise ValueError("MAE must be nonpositive")
        return self


def observe_prior_plan(
    plan: TradePlanDraft,
    completed_bars: Sequence[CompletedDailyBar],
    observed_through: date,
) -> PlanObservation:
    """Observe completed sessions through a declared regular close, without path inference."""

    bars = tuple(completed_bars)
    if any(bar.instrument_id != plan.entry_zone.upper.instrument_id for bar in bars):
        raise ValueError("completed bar instrument differs from plan")
    dates = tuple(bar.session_date for bar in bars)
    if dates != tuple(sorted(set(dates))):
        raise ValueError("completed bars require unique increasing dates")
    if any(day > observed_through for day in dates):
        raise ValueError("completed bar is after observed-through close")
    if any(bar.quality_flags for bar in bars):
        raise ValueError("uncertain completed bars cannot establish plan path")
    reference = plan.entry_zone.upper.value
    entry_lower = plan.entry_zone.lower.value
    stop = plan.candidate_stop.value
    targets = tuple(target.price.value for target in plan.target_scenarios)
    entry_at = None
    stop_at = None
    target_at = None
    mfe = None
    mae = None
    ambiguous = False
    evidence: list[str] = []
    for bar in bars:
        if bar.session_date <= plan.valid_from.date():
            continue
        if bar.session_date > plan.expires_at.date():
            break
        entry_this_bar = bar.low <= reference and bar.high >= entry_lower
        if entry_at is None and not entry_this_bar:
            continue
        if entry_at is None:
            entry_at = bar.source_timestamp
        evidence.append(bar.evidence_id)
        high_excursion = max(Decimal(0), bar.high - reference)
        low_excursion = min(Decimal(0), bar.low - reference)
        mfe = high_excursion if mfe is None else max(mfe, high_excursion)
        mae = low_excursion if mae is None else min(mae, low_excursion)
        stop_hit = bar.low <= stop
        target_hit = any(bar.high >= target for target in targets)
        if (entry_this_bar and (stop_hit or target_hit)) or (stop_hit and target_hit):
            ambiguous = True
            continue
        if stop_hit and stop_at is None:
            stop_at = bar.source_timestamp
        if target_hit and target_at is None:
            target_at = bar.source_timestamp
    outcomes: list[ObservationOutcome] = []
    if entry_at is None:
        outcomes.append(ObservationOutcome.ENTRY_ZONE_NOT_OBSERVED)
    else:
        outcomes.append(ObservationOutcome.ENTRY_ZONE_OBSERVED)
    if stop_at is not None:
        outcomes.append(ObservationOutcome.INVALIDATION_OBSERVED)
    if target_at is not None:
        outcomes.append(ObservationOutcome.TARGET_OBSERVED)
    if ambiguous:
        outcomes.append(ObservationOutcome.AMBIGUOUS_SEQUENCE)
    if observed_through >= plan.expires_at.date():
        outcomes.append(ObservationOutcome.OBSERVATION_WINDOW_ENDED)
    return PlanObservation(
        plan_id=plan.plan_id,
        observed_through=observed_through,
        outcomes=tuple(outcomes),
        observation_reference_price=reference,
        observation_reference_evidence_id=plan.entry_zone.upper.evidence_id,
        entry_zone_observed_at=entry_at,
        invalidation_observed_at=stop_at,
        target_observed_at=target_at,
        mfe=mfe,
        mae=mae,
        evidence_ids=tuple(evidence),
    )
