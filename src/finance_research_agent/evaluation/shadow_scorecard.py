"""Evidence-backed personal shadow evaluation summaries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated
from zoneinfo import ZoneInfo

from pydantic import Field, computed_field, model_validator

from finance_research_agent.domain.enums import BriefOrigin
from finance_research_agent.domain.models import (
    Identifier,
    PerformanceTelemetry,
    PublishedArtifact,
    PublishedRunBundle,
    Sha256,
    StoredRun,
    StrictModel,
)
from finance_research_agent.domain.types import FrozenMap

if TYPE_CHECKING:
    from finance_research_agent.application.feedback_service import RecordedFeedback
    from finance_research_agent.application.replay_service import ArtifactReplayResult
    from finance_research_agent.domain.market_calendar import TradingCalendar
    from finance_research_agent.evaluation.shadow_records import (
        OperatorReleaseAttestation,
        ReleaseCheckReceipt,
        ShadowDayObservation,
    )


_SHADOW_EVIDENCE_FACTORY_TOKEN = object()


@dataclass(frozen=True, slots=True)
class VerifiedShadowRunEvidence:
    """Run attempt together with its independently checked publication, if any."""

    stored_run: StoredRun
    published_bundle: PublishedRunBundle | None = None
    published_artifact: PublishedArtifact | None = None
    replay_result: ArtifactReplayResult | None = None
    telemetry: PerformanceTelemetry | None = None
    normal_provider: bool | None = None
    terminal_at: datetime | None = None
    duration_seconds: NonNegativeSeconds | None = None

    def __post_init__(self) -> None:
        publication_values = (
            self.published_bundle,
            self.published_artifact,
            self.replay_result,
        )
        if self.stored_run.published != all(value is not None for value in publication_values):
            raise ValueError("published run evidence requires a complete verified publication")
        if not self.stored_run.published and any(value is not None for value in publication_values):
            raise ValueError("unpublished run evidence cannot include publication values")
        if self.duration_seconds is not None and self.terminal_at is None:
            raise ValueError("run duration requires a verified terminal time")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("run duration must not be negative")


@dataclass(frozen=True, slots=True, init=False)
class ShadowEvaluationEvidence:
    """Immutable snapshot that can only be constructed by the verified loader."""

    period: ShadowPeriod
    origin: ShadowEvaluationOrigin | None
    verified_runs: tuple[VerifiedShadowRunEvidence, ...]
    feedback_records: tuple[RecordedFeedback, ...]
    observations: tuple[ShadowDayObservation, ...]
    operator_attestations: tuple[OperatorReleaseAttestation, ...]
    release_check_receipts: tuple[ReleaseCheckReceipt, ...]
    source_commit_sha256: Sha256 | None
    source_tree_sha256: Sha256 | None
    evidence_hashes: tuple[Sha256, ...]
    _loader_token: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        loader_token: object,
        period: ShadowPeriod,
        origin: ShadowEvaluationOrigin | None,
        verified_runs: tuple[VerifiedShadowRunEvidence, ...],
        feedback_records: tuple[RecordedFeedback, ...],
        observations: tuple[ShadowDayObservation, ...],
        operator_attestations: tuple[OperatorReleaseAttestation, ...],
        release_check_receipts: tuple[ReleaseCheckReceipt, ...],
        source_commit_sha256: Sha256 | None,
        source_tree_sha256: Sha256 | None,
        evidence_hashes: tuple[Sha256, ...],
    ) -> None:
        if loader_token is not _SHADOW_EVIDENCE_FACTORY_TOKEN:
            raise ValueError("shadow evaluation evidence must come from its verified loader")
        for name, value in (
            ("verified_runs", verified_runs),
            ("feedback_records", feedback_records),
            ("observations", observations),
            ("operator_attestations", operator_attestations),
            ("release_check_receipts", release_check_receipts),
            ("evidence_hashes", evidence_hashes),
        ):
            if type(value) is not tuple:
                raise TypeError(f"{name} must be an immutable tuple")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "period", period)
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "source_commit_sha256", source_commit_sha256)
        object.__setattr__(self, "source_tree_sha256", source_tree_sha256)
        object.__setattr__(self, "_loader_token", loader_token)

    @property
    def published_runs(self) -> tuple[PublishedRunBundle, ...]:
        return tuple(
            item.published_bundle
            for item in self.verified_runs
            if item.published_bundle is not None
        )


def _create_shadow_evaluation_evidence(
    *,
    period: ShadowPeriod,
    origin: ShadowEvaluationOrigin | None,
    verified_runs: tuple[VerifiedShadowRunEvidence, ...],
    feedback_records: tuple[RecordedFeedback, ...],
    observations: tuple[ShadowDayObservation, ...],
    operator_attestations: tuple[OperatorReleaseAttestation, ...],
    release_check_receipts: tuple[ReleaseCheckReceipt, ...],
    source_commit_sha256: Sha256 | None,
    source_tree_sha256: Sha256 | None,
    evidence_hashes: tuple[Sha256, ...],
) -> ShadowEvaluationEvidence:
    """Internal constructor reserved for the application evidence loader."""
    return ShadowEvaluationEvidence(
        loader_token=_SHADOW_EVIDENCE_FACTORY_TOKEN,
        period=period,
        origin=origin,
        verified_runs=verified_runs,
        feedback_records=feedback_records,
        observations=observations,
        operator_attestations=operator_attestations,
        release_check_receipts=release_check_receipts,
        source_commit_sha256=source_commit_sha256,
        source_tree_sha256=source_tree_sha256,
        evidence_hashes=evidence_hashes,
    )


NonNegativeCount = Annotated[int, Field(ge=0)]
NonNegativeSeconds = Annotated[int, Field(ge=0)]


class ShadowEvaluationOrigin(StrEnum):
    PERSONAL_SHADOW = "PERSONAL_SHADOW"
    SYNTHETIC_EVALUATION = "SYNTHETIC_EVALUATION"


class ShadowPeriod(StrictModel):
    """Inclusive market-date bounds selected for one shadow evaluation."""

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def date_bounds_are_ordered(self) -> ShadowPeriod:
        if self.start_date > self.end_date:
            raise ValueError("shadow period start_date must not follow end_date")
        return self


class ShadowGateId(StrEnum):
    AT_LEAST_TWENTY_TRADING_DAYS = "AT_LEAST_TWENTY_TRADING_DAYS"
    SAFETY = "SAFETY"
    DETERMINISTIC_CORRECTNESS = "DETERMINISTIC_CORRECTNESS"
    EVIDENCE = "EVIDENCE"
    TIMELY_PUBLICATION = "TIMELY_PUBLICATION"
    NORMAL_PROVIDER_DURATION = "NORMAL_PROVIDER_DURATION"
    FAILURE_OPERATIONS = "FAILURE_OPERATIONS"
    REVIEW_TIME = "REVIEW_TIME"
    EXECUTIVE_IDENTIFICATION = "EXECUTIVE_IDENTIFICATION"
    USEFULNESS = "USEFULNESS"


class EvaluationGateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"


class EvaluationGate(StrictModel):
    """One ordered, evidence-backed shadow gate result."""

    id: ShadowGateId
    status: EvaluationGateStatus
    evidence_ids: tuple[Identifier, ...] = ()
    reason_codes: tuple[Identifier, ...] = ()
    measured_value: str | int | Decimal | None = None
    required_value: str | int | Decimal | None = None

    @model_validator(mode="after")
    def pending_gate_explains_missing_evidence(self) -> EvaluationGate:
        if self.status is EvaluationGateStatus.PENDING and not self.reason_codes:
            raise ValueError("a pending evaluation gate requires a reason code")
        return self


class RunDurationObservation(StrictModel):
    run_id: Identifier
    market_date: date
    duration_seconds: NonNegativeSeconds


class DailyReviewTimeObservation(StrictModel):
    market_date: date
    feedback_id: Identifier
    executive_review_duration_seconds: NonNegativeSeconds | None = None
    detailed_review_duration_seconds: NonNegativeSeconds | None = None


class DailyUsefulnessObservation(StrictModel):
    market_date: date
    feedback_id: Identifier
    usefulness_score: Annotated[int, Field(ge=1, le=5)] | None = None
    corrective_work: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def usefulness_has_a_recorded_measure(self) -> DailyUsefulnessObservation:
        if self.usefulness_score is None and self.corrective_work is None:
            raise ValueError("usefulness observation requires a score or corrective work")
        return self


class ShadowScorecard(StrictModel):
    """Immutable non-P&L summary of shadow evidence and its required gates."""

    period_start: date | None = None
    period_end: date | None = None
    origin: ShadowEvaluationOrigin | None = None
    valid_trading_days: NonNegativeCount = 0
    eligible_online_days: NonNegativeCount = 0
    published_in_target_window: NonNegativeCount = 0
    duration_observations: tuple[RunDurationObservation, ...] = ()
    review_time_observations: tuple[DailyReviewTimeObservation, ...] = ()
    usefulness_observations: tuple[DailyUsefulnessObservation, ...] = ()
    plan_observation_counts: FrozenMap[Identifier, NonNegativeCount] = Field(
        default_factory=lambda: FrozenMap({})
    )
    block_reason_counts: FrozenMap[Identifier, NonNegativeCount] = Field(
        default_factory=lambda: FrozenMap({})
    )
    setup_distribution: FrozenMap[Identifier, NonNegativeCount] = Field(
        default_factory=lambda: FrozenMap({})
    )
    regime_distribution: FrozenMap[Identifier, NonNegativeCount] = Field(
        default_factory=lambda: FrozenMap({})
    )
    gate_results: tuple[EvaluationGate, ...]
    evidence_hashes: tuple[Sha256, ...] = ()

    @model_validator(mode="after")
    def gate_results_are_complete_and_ordered(self) -> ShadowScorecard:
        period_start = self.period_start
        period_end = self.period_end
        if (period_start is None) != (period_end is None):
            raise ValueError("scorecard period requires both start and end dates")
        if period_start is not None and period_end is not None and period_start > period_end:
            raise ValueError("scorecard period_start must not follow period_end")
        if tuple(gate.id for gate in self.gate_results) != tuple(ShadowGateId):
            raise ValueError("scorecard must contain every shadow gate exactly once in order")
        if self.valid_trading_days > self.eligible_online_days:
            raise ValueError("valid_trading_days cannot exceed eligible_online_days")
        if self.published_in_target_window > self.eligible_online_days:
            raise ValueError("target-window publications cannot exceed eligible_online_days")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed_gate_ids(self) -> tuple[ShadowGateId, ...]:
        return tuple(
            gate.id for gate in self.gate_results if gate.status is EvaluationGateStatus.FAIL
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pending_gate_ids(self) -> tuple[ShadowGateId, ...]:
        return tuple(
            gate.id for gate in self.gate_results if gate.status is EvaluationGateStatus.PENDING
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def graduation_ready(self) -> bool:
        return (
            self.origin is ShadowEvaluationOrigin.PERSONAL_SHADOW
            and self.valid_trading_days >= 20
            and not self.failed_gate_ids
            and not self.pending_gate_ids
        )


def build_shadow_scorecard(
    published_runs: Sequence[PublishedRunBundle],
    feedback: Sequence[RecordedFeedback],
    calendar: TradingCalendar,
    *,
    evidence: ShadowEvaluationEvidence | None = None,
) -> ShadowScorecard:
    """Aggregate day and publication evidence without trusting caller decisions."""
    if evidence is None:
        dates = tuple(sorted({bundle.run.market_date for bundle in published_runs}))
        gates = tuple(
            EvaluationGate(
                id=gate_id,
                status=EvaluationGateStatus.PENDING,
                reason_codes=("VERIFIED_SHADOW_EVIDENCE_REQUIRED",),
            )
            for gate_id in ShadowGateId
        )
        return ShadowScorecard(
            period_start=dates[0] if dates else None,
            period_end=dates[-1] if dates else None,
            gate_results=gates,
        )

    del published_runs, feedback
    period = evidence.period
    trading_dates: list[date] = []
    current = period.start_date
    while current <= period.end_date:
        if calendar.is_trading_day(current):
            trading_dates.append(current)
        if current == period.end_date:
            break
        current = date.fromordinal(current.toordinal() + 1)

    superseded_ids = {
        item.corrects_observation_id
        for item in evidence.observations
        if item.corrects_observation_id is not None
    }
    current_observations: dict[date, set[str]] = {}
    for item in evidence.observations:
        if item.id in superseded_ids:
            continue
        current_observations.setdefault(item.market_date, set()).add(item.availability.value)

    eligible_dates = tuple(
        market_date
        for market_date in trading_dates
        if current_observations.get(market_date) != {"EXCLUDED"}
    )
    complete_day_evidence = all(
        current_observations.get(market_date) in ({"ONLINE"}, {"EXCLUDED"})
        for market_date in trading_dates
    )

    research_publications: dict[date, list[VerifiedShadowRunEvidence]] = {}
    for verified_run in evidence.verified_runs:
        bundle = verified_run.published_bundle
        artifact = verified_run.published_artifact
        if bundle is None or artifact is None:
            continue
        contents = bundle.bundle
        if (
            "brief_draft" not in contents
            and contents.get("brief_origin") != BriefOrigin.DETERMINISTIC_REDUCED.value
        ):
            continue
        research_publications.setdefault(bundle.run.market_date, []).append(verified_run)

    selected_publications = {
        market_date: min(
            records,
            key=lambda item: (
                item.published_artifact.published_at,  # type: ignore[union-attr]
                item.stored_run.run.revision,
                item.stored_run.run.run_id,
            ),
        )
        for market_date, records in research_publications.items()
    }
    excluded_dates = {
        market_date
        for market_date in trading_dates
        if current_observations.get(market_date) == {"EXCLUDED"}
    }
    target_zone = ZoneInfo("America/New_York")
    published_in_target_window = 0
    if evidence.origin is ShadowEvaluationOrigin.PERSONAL_SHADOW:
        for market_date, verified_run in selected_publications.items():
            if market_date in excluded_dates:
                continue
            artifact = verified_run.published_artifact
            assert artifact is not None
            published_local = artifact.published_at.astimezone(target_zone)
            local_time = published_local.timetz().replace(tzinfo=None)
            if published_local.date() == market_date and time(8, 45) <= local_time <= time(9, 0):
                published_in_target_window += 1

    valid_trading_days = 0
    if evidence.origin is ShadowEvaluationOrigin.PERSONAL_SHADOW:
        valid_trading_days = sum(
            1
            for market_date in trading_dates
            if current_observations.get(market_date) == {"ONLINE"}
            and market_date in selected_publications
        )

    if valid_trading_days >= 20 and complete_day_evidence:
        day_gate = EvaluationGate(
            id=ShadowGateId.AT_LEAST_TWENTY_TRADING_DAYS,
            status=EvaluationGateStatus.PASS,
            evidence_ids=evidence.evidence_hashes,
            measured_value=valid_trading_days,
            required_value=20,
        )
    else:
        day_gate = EvaluationGate(
            id=ShadowGateId.AT_LEAST_TWENTY_TRADING_DAYS,
            status=EvaluationGateStatus.PENDING,
            evidence_ids=evidence.evidence_hashes,
            reason_codes=(
                "UNKNOWN_OR_MISSING_DAY_OBSERVATION"
                if not complete_day_evidence
                else "TWENTY_VALID_PERSONAL_TRADING_DAYS_REQUIRED",
            ),
            measured_value=valid_trading_days,
            required_value=20,
        )

    if not eligible_dates:
        timely_gate = EvaluationGate(
            id=ShadowGateId.TIMELY_PUBLICATION,
            status=EvaluationGateStatus.PENDING,
            evidence_ids=evidence.evidence_hashes,
            reason_codes=("NO_ELIGIBLE_TRADING_DAYS",),
        )
    elif not complete_day_evidence:
        timely_gate = EvaluationGate(
            id=ShadowGateId.TIMELY_PUBLICATION,
            status=EvaluationGateStatus.PENDING,
            evidence_ids=evidence.evidence_hashes,
            reason_codes=("UNKNOWN_OR_MISSING_DAY_OBSERVATION",),
            measured_value=Decimal(published_in_target_window) / Decimal(len(eligible_dates)),
            required_value=Decimal("0.95"),
        )
    else:
        publication_ratio = Decimal(published_in_target_window) / Decimal(len(eligible_dates))
        timely_gate = EvaluationGate(
            id=ShadowGateId.TIMELY_PUBLICATION,
            status=(
                EvaluationGateStatus.PASS
                if publication_ratio >= Decimal("0.95")
                else EvaluationGateStatus.FAIL
            ),
            evidence_ids=evidence.evidence_hashes,
            reason_codes=(
                ()
                if publication_ratio >= Decimal("0.95")
                else ("TARGET_WINDOW_PUBLICATION_RATE_BELOW_95_PERCENT",)
            ),
            measured_value=publication_ratio,
            required_value=Decimal("0.95"),
        )

    specific_gates = {
        ShadowGateId.AT_LEAST_TWENTY_TRADING_DAYS: day_gate,
        ShadowGateId.TIMELY_PUBLICATION: timely_gate,
    }
    gates = tuple(
        specific_gates.get(gate_id)
        or EvaluationGate(
            id=gate_id,
            status=EvaluationGateStatus.PENDING,
            evidence_ids=evidence.evidence_hashes,
            reason_codes=("GATE_EVIDENCE_NOT_YET_AGGREGATED",),
        )
        for gate_id in ShadowGateId
    )
    return ShadowScorecard(
        period_start=period.start_date,
        period_end=period.end_date,
        origin=evidence.origin,
        valid_trading_days=valid_trading_days,
        eligible_online_days=len(eligible_dates),
        published_in_target_window=published_in_target_window,
        gate_results=gates,
        evidence_hashes=evidence.evidence_hashes,
    )


__all__ = [
    "DailyReviewTimeObservation",
    "DailyUsefulnessObservation",
    "EvaluationGate",
    "EvaluationGateStatus",
    "RunDurationObservation",
    "ShadowEvaluationOrigin",
    "ShadowEvaluationEvidence",
    "ShadowGateId",
    "ShadowPeriod",
    "ShadowScorecard",
    "VerifiedShadowRunEvidence",
    "build_shadow_scorecard",
]
