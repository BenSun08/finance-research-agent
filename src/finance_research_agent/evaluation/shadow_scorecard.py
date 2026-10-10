"""Evidence-backed personal shadow evaluation summaries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, computed_field, model_validator

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
from finance_research_agent.domain.types import FrozenMap, canonical_bytes

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


def _canonical_content_index(
    records: Sequence[object], *, identity_field: str, record_kind: str
) -> dict[str, str]:
    content_by_id: dict[str, str] = {}
    for record in records:
        if not isinstance(record, BaseModel):
            raise TypeError(f"{record_kind} entries must be validated model records")
        identifier = getattr(record, identity_field, None)
        if not isinstance(identifier, str):
            raise TypeError(f"{record_kind} entries must expose {identity_field}")
        if identifier in content_by_id:
            raise ValueError(f"{record_kind} identifiers must be unique")
        content_by_id[identifier] = sha256(canonical_bytes(record)).hexdigest()
    return content_by_id


def _published_content_index(records: Sequence[object]) -> dict[str, str]:
    content_by_id: dict[str, str] = {}
    for record in records:
        if not isinstance(record, PublishedRunBundle):
            raise TypeError("published runs must be PublishedRunBundle values")
        identifier = record.run.run_id
        if identifier in content_by_id:
            raise ValueError("published run identifiers must be unique")
        content_by_id[identifier] = sha256(canonical_bytes(record)).hexdigest()
    return content_by_id


def _select_daily_feedback(
    evidence: ShadowEvaluationEvidence,
    selected_publications: dict[date, VerifiedShadowRunEvidence],
    eligible_dates: Sequence[date],
) -> tuple[tuple[date, ...], dict[date, RecordedFeedback]]:
    if evidence.origin is not ShadowEvaluationOrigin.PERSONAL_SHADOW:
        return (), {}
    report_dates = tuple(
        sorted(
            market_date for market_date in selected_publications if market_date in eligible_dates
        )
    )
    feedback_by_date: dict[date, RecordedFeedback] = {}
    for market_date in report_dates:
        verified_run = selected_publications[market_date]
        artifact = verified_run.published_artifact
        assert artifact is not None
        complete_feedback = tuple(
            item
            for item in evidence.feedback_records
            if item.run_id == verified_run.stored_run.run_id
            and item.bundle_sha256 == artifact.bundle_sha256
            and item.schema_version == "0.2"
            and item.executive_review_duration_seconds is not None
            and item.detailed_review_duration_seconds is not None
            and item.executive_identification is not None
        )
        if complete_feedback:
            feedback_by_date[market_date] = min(
                complete_feedback,
                key=lambda item: (item.recorded_at, item.feedback_id),
            )
    return report_dates, feedback_by_date


def _median(values: Sequence[int]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return Decimal(ordered[middle])
    return Decimal(ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _daily_review_time_gate(
    report_dates: Sequence[date],
    feedback_by_date: dict[date, RecordedFeedback],
    all_daily_feedback_ids: Sequence[str] = (),
) -> tuple[tuple[DailyReviewTimeObservation, ...], EvaluationGate]:
    observations = tuple(
        DailyReviewTimeObservation(
            market_date=market_date,
            feedback_id=feedback_record.feedback_id,
            executive_review_duration_seconds=feedback_record.executive_review_duration_seconds,
            detailed_review_duration_seconds=feedback_record.detailed_review_duration_seconds,
        )
        for market_date, feedback_record in sorted(feedback_by_date.items())
    )
    evidence_ids = tuple(
        sorted(
            {feedback.feedback_id for feedback in feedback_by_date.values()}
            | set(all_daily_feedback_ids)
            | {f"daily-report-{market_date.isoformat()}" for market_date in report_dates}
        )
    )
    if not report_dates or len(feedback_by_date) != len(report_dates):
        return observations, EvaluationGate(
            id=ShadowGateId.REVIEW_TIME,
            status=EvaluationGateStatus.PENDING,
            evidence_ids=evidence_ids,
            reason_codes=(
                "NO_ELIGIBLE_RESEARCH_REPORTS"
                if not report_dates
                else "COMPLETE_DAILY_FEEDBACK_MISSING",
            ),
        )

    executive_median = _median(
        tuple(
            int(item.executive_review_duration_seconds)
            for item in observations
            if item.executive_review_duration_seconds is not None
        )
    )
    detailed_median = _median(
        tuple(
            int(item.detailed_review_duration_seconds)
            for item in observations
            if item.detailed_review_duration_seconds is not None
        )
    )
    passed = executive_median <= 180 and detailed_median <= 900
    measured = f"executive={executive_median};detailed={detailed_median}"
    return observations, EvaluationGate(
        id=ShadowGateId.REVIEW_TIME,
        status=EvaluationGateStatus.PASS if passed else EvaluationGateStatus.FAIL,
        evidence_ids=evidence_ids,
        reason_codes=() if passed else ("REVIEW_TIME_LIMIT_EXCEEDED",),
        measured_value=measured,
        required_value="executive<=180;detailed<=900",
    )


def _daily_usefulness_gate(
    report_dates: Sequence[date],
    feedback_by_date: dict[date, RecordedFeedback],
    all_daily_feedback_ids: Sequence[str] = (),
) -> tuple[tuple[DailyUsefulnessObservation, ...], EvaluationGate]:
    observations = tuple(
        DailyUsefulnessObservation(
            market_date=market_date,
            feedback_id=feedback_record.feedback_id,
            usefulness_score=feedback_record.usefulness_score,
            corrective_work=feedback_record.notes,
        )
        for market_date, feedback_record in sorted(feedback_by_date.items())
    )
    evidence_ids = tuple(
        sorted(
            {feedback.feedback_id for feedback in feedback_by_date.values()}
            | set(all_daily_feedback_ids)
            | {f"daily-report-{market_date.isoformat()}" for market_date in report_dates}
        )
    )
    if not report_dates or len(feedback_by_date) != len(report_dates):
        return observations, EvaluationGate(
            id=ShadowGateId.USEFULNESS,
            status=EvaluationGateStatus.PENDING,
            evidence_ids=evidence_ids,
            reason_codes=(
                "NO_ELIGIBLE_RESEARCH_REPORTS"
                if not report_dates
                else "COMPLETE_DAILY_FEEDBACK_MISSING",
            ),
        )

    average_usefulness = Decimal(
        sum(item.usefulness_score or 0 for item in observations)
    ) / Decimal(len(observations))
    if average_usefulness >= Decimal("4"):
        status = EvaluationGateStatus.PASS
        reason_codes: tuple[str, ...] = ()
    elif any(item.corrective_work is not None for item in observations):
        status = EvaluationGateStatus.PENDING
        reason_codes = ("CORRECTIVE_WORK_REQUIRES_VALIDATION",)
    else:
        status = EvaluationGateStatus.FAIL
        reason_codes = ("AVERAGE_USEFULNESS_BELOW_4",)
    return observations, EvaluationGate(
        id=ShadowGateId.USEFULNESS,
        status=status,
        evidence_ids=evidence_ids,
        reason_codes=reason_codes,
        measured_value=average_usefulness,
        required_value=Decimal("4"),
    )


def _normal_provider_duration_evidence(
    evidence: ShadowEvaluationEvidence,
) -> tuple[tuple[RunDurationObservation, ...], EvaluationGate]:
    normal_attempts = tuple(item for item in evidence.verified_runs if item.normal_provider is True)
    unresolved_attempts = tuple(
        item for item in evidence.verified_runs if item.normal_provider is None
    )
    observations = tuple(
        sorted(
            (
                RunDurationObservation(
                    run_id=item.stored_run.run_id,
                    market_date=item.stored_run.run.market_date,
                    duration_seconds=item.duration_seconds,
                )
                for item in normal_attempts
                if item.duration_seconds is not None
            ),
            key=lambda item: (item.market_date, item.run_id),
        )
    )
    evidence_ids = tuple(
        sorted({item.stored_run.run_id for item in normal_attempts + unresolved_attempts})
    )
    if not normal_attempts or unresolved_attempts or len(observations) != len(normal_attempts):
        reason_code = (
            "NORMAL_PROVIDER_TELEMETRY_MISSING"
            if unresolved_attempts
            else (
                "NORMAL_PROVIDER_TERMINAL_EVIDENCE_MISSING"
                if normal_attempts
                else "NO_NORMAL_PROVIDER_ATTEMPTS"
            )
        )
        return observations, EvaluationGate(
            id=ShadowGateId.NORMAL_PROVIDER_DURATION,
            status=EvaluationGateStatus.PENDING,
            evidence_ids=evidence_ids,
            reason_codes=(reason_code,),
            required_value=900,
        )

    durations = sorted(item.duration_seconds for item in observations)
    nearest_rank_index = (95 * len(durations) + 99) // 100 - 1
    percentile_95 = durations[nearest_rank_index]
    status = EvaluationGateStatus.PASS if percentile_95 <= 900 else EvaluationGateStatus.FAIL
    return observations, EvaluationGate(
        id=ShadowGateId.NORMAL_PROVIDER_DURATION,
        status=status,
        evidence_ids=evidence_ids,
        reason_codes=(
            () if status is EvaluationGateStatus.PASS else ("P95_DURATION_EXCEEDS_900_SECONDS",)
        ),
        measured_value=percentile_95,
        required_value=900,
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
    corrective_work: str | None = Field(default=None, min_length=1, max_length=1000)

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

    if _published_content_index(published_runs) != _published_content_index(
        evidence.published_runs
    ):
        raise ValueError("published runs must exactly match verified evidence")
    if _canonical_content_index(
        feedback, identity_field="feedback_id", record_kind="feedback"
    ) != _canonical_content_index(
        evidence.feedback_records,
        identity_field="feedback_id",
        record_kind="feedback",
    ):
        raise ValueError("feedback must exactly match verified evidence")

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
    report_dates, daily_feedback = _select_daily_feedback(
        evidence,
        selected_publications,
        eligible_dates,
    )
    daily_run_ids = {
        selected_publications[market_date].stored_run.run_id for market_date in report_dates
    }
    all_daily_feedback_ids = tuple(
        sorted(
            item.feedback_id for item in evidence.feedback_records if item.run_id in daily_run_ids
        )
    )
    review_time_observations, review_time_gate = _daily_review_time_gate(
        report_dates, daily_feedback, all_daily_feedback_ids
    )
    usefulness_observations, usefulness_gate = _daily_usefulness_gate(
        report_dates, daily_feedback, all_daily_feedback_ids
    )
    duration_observations, duration_gate = _normal_provider_duration_evidence(evidence)

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
        ShadowGateId.REVIEW_TIME: review_time_gate,
        ShadowGateId.NORMAL_PROVIDER_DURATION: duration_gate,
        ShadowGateId.TIMELY_PUBLICATION: timely_gate,
        ShadowGateId.USEFULNESS: usefulness_gate,
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
        duration_observations=duration_observations,
        review_time_observations=review_time_observations,
        usefulness_observations=usefulness_observations,
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
