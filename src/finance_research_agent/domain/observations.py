"""Completed-bar path observations for prior conditional research plans."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, localcontext
from typing import Self

from pydantic import Field, model_validator

from finance_research_agent.domain.enums import ObservationOutcome
from finance_research_agent.domain.market_calendar import NEW_YORK
from finance_research_agent.domain.models import CompletedDailyBar, Identifier, StrictModel
from finance_research_agent.domain.plans import TradePlanDraft, _plan_calendar
from finance_research_agent.domain.types import UtcDatetime

_CONTEXT = Context(prec=28)
_DISCLOSURE_FLAGS = frozenset(
    {
        "ADJUSTMENT_RAW",
        "ADJUSTMENT_SPLIT",
        "ADJUSTMENT_DIVIDEND",
        "ADJUSTMENT_ALL",
        "FEED_IEX_SINGLE_EXCHANGE",
        "FEED_SIP_CONSOLIDATED_US",
    }
)


def _disqualifying_quality(flags: tuple[str, ...]) -> bool:
    return any(flag not in _DISCLOSURE_FLAGS for flag in flags)


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
    calendar = _plan_calendar()
    if not calendar.is_trading_day(observed_through):
        raise ValueError("observation coverage requires a completed trading session")
    start_date = plan.valid_from.astimezone(NEW_YORK).date()
    cutoff = plan.effective_expiry_at or plan.expires_at
    cutoff_date = cutoff.astimezone(NEW_YORK).date()
    if observed_through >= cutoff_date and calendar.is_trading_day(cutoff_date):
        opened, closed = calendar.session_open_close(cutoff_date)
        if opened < cutoff < closed:
            raise ValueError("observation coverage cannot resolve intraday expiry from daily bars")
    end_date = min(observed_through, cutoff_date)
    expected: list[date] = []
    session_close: dict[date, datetime] = {}
    day = start_date
    while day <= end_date:
        if calendar.is_trading_day(day):
            opened, closed = calendar.session_open_close(day)
            if opened >= plan.valid_from and closed <= cutoff:
                expected.append(day)
                session_close[day] = closed
        day += timedelta(days=1)
    if not expected:
        raise ValueError("observation coverage has no fully eligible completed sessions")
    by_date = {bar.session_date: bar for bar in bars}
    if any(day not in by_date for day in expected):
        raise ValueError("observation coverage has missing completed sessions")
    eligible_bars = tuple(by_date[day] for day in expected)
    if any(
        _disqualifying_quality(bar.quality_flags)
        or bar.retrieved_at < session_close[bar.session_date]
        or bar.evidence_cutoff_at < session_close[bar.session_date]
        for bar in eligible_bars
    ):
        raise ValueError("observation coverage has incomplete or unreliable bars")
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
    evidence: list[str] = [bar.evidence_id for bar in eligible_bars]
    with localcontext(_CONTEXT):
        for bar in eligible_bars:
            entry_this_bar = bar.low <= reference and bar.high >= entry_lower
            if entry_at is None and not entry_this_bar:
                continue
            confirmed_at = session_close[bar.session_date]
            if entry_at is None:
                entry_at = confirmed_at
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
                stop_at = confirmed_at
            if target_hit and target_at is None:
                target_at = confirmed_at
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
    if observed_through >= cutoff_date:
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
