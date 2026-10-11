"""Evidence-backed personal shadow evaluation summaries."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, computed_field, model_validator

from finance_research_agent.domain.enums import BriefOrigin, Capability, ReducedReportReason
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    Identifier,
    PerformanceTelemetry,
    PublishedArtifact,
    PublishedRunBundle,
    Sha256,
    StoredRun,
    StrictModel,
)
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.regime import Regime
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.domain.validation import (
    ResearchBriefDraft,
    ValidationReport,
    validate_research_brief,
)
from finance_research_agent.evaluation.shadow_records import (
    ReleaseCheckConclusion,
    ReleaseCheckId,
    ReleaseCheckReceipt,
    ShadowExecutiveEventState,
    ShadowExecutiveIdentification,
    ShadowFailureOperationsCoverage,
)

if TYPE_CHECKING:
    from finance_research_agent.application.feedback_service import RecordedFeedback
    from finance_research_agent.application.operations import ExecutiveIdentification
    from finance_research_agent.application.replay_service import ArtifactReplayResult
    from finance_research_agent.domain.market_calendar import TradingCalendar
    from finance_research_agent.evaluation.shadow_records import (
        OperatorReleaseAttestation,
        ShadowDayObservation,
    )


_SHADOW_EVIDENCE_FACTORY_TOKEN = object()
_SAFETY_RELEASE_CHECKS = (
    ReleaseCheckId.SCENARIO_AND_FAILURE_MATRIX,
    ReleaseCheckId.SECRET_SCAN,
    ReleaseCheckId.NETWORK_BOUNDARY,
)
_DETERMINISTIC_RELEASE_CHECKS = (
    ReleaseCheckId.UNIT_AND_INTEGRATION_TESTS,
    # This fixed matrix includes the stale-current-price sizing fail-closed case.
    ReleaseCheckId.SCENARIO_AND_FAILURE_MATRIX,
)
_RELEASE_CHECK_PYTHON_VERSIONS = ("3.12", "3.13", "3.14")


@dataclass(frozen=True, slots=True)
class _ReleaseCheckEvidence:
    selected: tuple[ReleaseCheckReceipt, ...]
    missing: tuple[tuple[ReleaseCheckId, str], ...]
    failures: tuple[ReleaseCheckReceipt, ...]
    source_mismatches: tuple[ReleaseCheckReceipt, ...]
    source_identity_known: bool


@dataclass(frozen=True, slots=True)
class _DeterministicArtifactReview:
    failures: tuple[str, ...]
    missing: tuple[str, ...]
    stale_sizing_count: int | None


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


def _executive_identification_gate(
    report_dates: Sequence[date],
    selected_publications: dict[date, VerifiedShadowRunEvidence],
    evidence: ShadowEvaluationEvidence,
) -> EvaluationGate:
    evidence_ids: set[str] = set()
    completed_review_days = 0
    missing_review = False
    mismatch = False

    for market_date in report_dates:
        verified_run = selected_publications[market_date]
        bundle = verified_run.published_bundle
        artifact = verified_run.published_artifact
        if bundle is None or artifact is None:
            missing_review = True
            continue
        run_id = verified_run.stored_run.run_id
        evidence_ids.add(run_id)
        reviews = tuple(
            record
            for record in evidence.feedback_records
            if record.run_id == run_id
            and record.bundle_sha256 == artifact.bundle_sha256
            and record.schema_version == "0.2"
        )
        evidence_ids.update(record.feedback_id for record in reviews)
        identifications = tuple(
            record.executive_identification
            for record in reviews
            if record.executive_identification is not None
        )

        superseded_observation_ids = {
            item.corrects_observation_id
            for item in evidence.observations
            if item.corrects_observation_id is not None
        }
        day_observations = tuple(
            item
            for item in evidence.observations
            if item.market_date == market_date
            and run_id in item.observed_run_ids
            and item.id not in superseded_observation_ids
        )
        evidence_ids.update(item.id for item in day_observations)
        day_identifications = tuple(
            item.executive_identification
            for item in day_observations
            if item.executive_identification is not None
        )

        expected = _expected_executive_identification(bundle)
        if expected is None:
            missing_review = True
            continue
        if not identifications and not day_identifications:
            missing_review = True
        else:
            completed_review_days += 1
        if any(not _executive_identification_matches(item, expected) for item in identifications):
            mismatch = True
        if any(
            not _executive_identification_matches(item, expected) for item in day_identifications
        ):
            mismatch = True

    if mismatch:
        status = EvaluationGateStatus.FAIL
        reasons: tuple[str, ...] = ("EXECUTIVE_IDENTIFICATION_MISMATCH",)
    elif missing_review or not report_dates:
        status = EvaluationGateStatus.PENDING
        reasons = ("DAILY_EXECUTIVE_IDENTIFICATION_EVIDENCE_MISSING",)
    else:
        status = EvaluationGateStatus.PASS
        reasons = ()
    return EvaluationGate(
        id=ShadowGateId.EXECUTIVE_IDENTIFICATION,
        status=status,
        evidence_ids=tuple(sorted(evidence_ids)),
        reason_codes=reasons,
        measured_value=completed_review_days,
        required_value=len(report_dates),
    )


def _expected_executive_identification(
    bundle: PublishedRunBundle,
) -> ShadowExecutiveIdentification | None:
    packet_data = bundle.model_dump(mode="json")["bundle"].get("research_packet")
    if not isinstance(packet_data, Mapping):
        return None
    try:
        packet_bytes = json.dumps(
            packet_data,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        packet = ResearchPacket.model_validate_json(packet_bytes)
    except (TypeError, ValueError):
        return None

    event_capability = next(
        (
            state
            for state in packet.capability_states
            if state.capability is Capability.EVENT_RISK_CHECK_AVAILABLE
        ),
        None,
    )
    if event_capability is None:
        return None

    if not event_capability.available:
        material_event_ids: tuple[str, ...] = ()
        event_state = ShadowExecutiveEventState.UNAVAILABLE
    else:
        material_event_ids = tuple(
            dict.fromkeys(
                event.event_id
                for event in packet.events
                if event.materiality in {"MEDIUM", "HIGH", "UNKNOWN"}
            )
        )
        event_state = (
            ShadowExecutiveEventState.MATERIAL_EVENTS
            if material_event_ids
            else ShadowExecutiveEventState.NO_MATERIAL_EVENTS
        )

    return ShadowExecutiveIdentification(
        market_posture=(
            packet.regime_result.regime if packet.regime_result is not None else Regime.UNKNOWN
        ),
        event_state=event_state,
        event_ids=material_event_ids,
        priority_symbols=tuple(
            dict.fromkeys(
                candidate.symbol for candidate in packet.candidates if candidate.executive_highlight
            )
        ),
        disabled_capabilities=tuple(
            dict.fromkeys(
                state.capability for state in packet.capability_states if not state.available
            )
        ),
    )


def _executive_identification_matches(
    observed: ExecutiveIdentification | ShadowExecutiveIdentification,
    expected: ShadowExecutiveIdentification,
) -> bool:
    return (
        observed.market_posture.value == expected.market_posture.value
        and observed.event_state.value == expected.event_state.value
        and set(observed.event_ids) == set(expected.event_ids)
        and observed.priority_symbols == expected.priority_symbols
        and set(observed.disabled_capabilities) == set(expected.disabled_capabilities)
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


def _citation_evidence_gate(evidence: ShadowEvaluationEvidence) -> EvaluationGate:
    from finance_research_agent.application.operations import CitationVerdict
    from finance_research_agent.evaluation.citation_sampling import (
        build_citation_entailment_review_sample,
    )

    published_runs = tuple(
        item
        for item in evidence.verified_runs
        if item.published_bundle is not None and item.published_artifact is not None
    )
    evidence_ids = set(evidence.evidence_hashes)
    if not published_runs:
        evidence_ids.update(item.stored_run.run_id for item in evidence.verified_runs)
        return EvaluationGate(
            id=ShadowGateId.EVIDENCE,
            status=EvaluationGateStatus.PENDING,
            evidence_ids=tuple(sorted(evidence_ids)),
            reason_codes=("NO_PUBLISHED_RUNS",),
        )

    incomplete_sample = False
    legacy_incomplete = False
    adverse_full_review = False
    adverse_legacy_review = False
    invalid_context = False
    no_material_claims = False
    artifact_failures: list[str] = []
    artifact_missing: list[str] = []

    feedback_by_publication: dict[str, list[RecordedFeedback]] = {}
    for record in evidence.feedback_records:
        feedback_by_publication.setdefault(record.run_id, []).append(record)

    for verified_run in published_runs:
        bundle = verified_run.published_bundle
        publication = verified_run.published_artifact
        assert bundle is not None and publication is not None
        run_id = verified_run.stored_run.run_id
        evidence_ids.add(run_id)
        artifact_review = _review_deterministic_artifact(verified_run)
        artifact_failures.extend(
            reason
            for reason in artifact_review.failures
            if reason != "STALE_CURRENT_PRICE_SIZING_OBSERVED"
        )
        artifact_missing.extend(artifact_review.missing)
        matching_feedback = tuple(
            record
            for record in feedback_by_publication.get(run_id, ())
            if record.bundle_sha256 == publication.bundle_sha256
        )
        evidence_ids.update(record.feedback_id for record in matching_feedback)
        for record in matching_feedback:
            for review in record.citation_reviews:
                if review.schema_version == "0.1":
                    legacy_incomplete = True
                    if not review.entails_claim:
                        adverse_legacy_review = True
        try:
            sample = build_citation_entailment_review_sample(
                bundle,
                publication=publication,
            )
        except (TypeError, ValueError):
            invalid_context = True
            continue

        if sample.empty_reason is not None:
            no_material_claims = True
            if bundle.bundle.get("brief_origin") == BriefOrigin.OPERATIONAL.value:
                artifact_missing.append("OPERATIONAL_REPORT_DISCLOSURES_UNVERIFIED")
            continue

        selected_claim_ids = set(sample.claim_ids)
        allowed_citations_by_claim = {
            context.claim.claim_id: {
                item.evidence_id
                for item in (
                    *context.cited_evidence,
                    *context.counter_evidence,
                    *context.metric_input_evidence,
                )
            }
            for context in sample.contexts
        }
        supported_claim_ids: set[str] = set()

        for record in matching_feedback:
            for review in record.citation_reviews:
                if review.schema_version == "0.1":
                    continue

                if (
                    review.claim_id in selected_claim_ids
                    and review.verdict is not None
                    and review.verdict is not CitationVerdict.SUPPORTED
                ):
                    adverse_full_review = True

                if (
                    review.claim_id not in selected_claim_ids
                    or review.citation_id
                    not in allowed_citations_by_claim.get(review.claim_id, set())
                    or review.verdict is None
                    or review.reviewed_at is None
                    or not publication.published_at <= review.reviewed_at <= record.recorded_at
                ):
                    incomplete_sample = True
                    continue
                if review.verdict is CitationVerdict.SUPPORTED:
                    supported_claim_ids.add(review.claim_id)

        if selected_claim_ids - supported_claim_ids:
            incomplete_sample = True

    failure_reasons = [
        reason
        for condition, reason in (
            (adverse_full_review, "MATERIAL_CITATION_REVIEW_NOT_SUPPORTED"),
            (adverse_legacy_review, "LEGACY_ADVERSE_CITATION_REVIEW"),
            (invalid_context, "CITATION_CONTEXT_INVALID"),
        )
        if condition
    ]
    failure_reasons.extend(artifact_failures)
    failure_reasons = list(dict.fromkeys(failure_reasons))
    if failure_reasons:
        reasons = tuple(failure_reasons)
        status = EvaluationGateStatus.FAIL
    elif incomplete_sample or artifact_missing:
        pending_reasons = []
        if incomplete_sample:
            pending_reasons.append("CITATION_ENTAILMENT_SAMPLE_INCOMPLETE")
        pending_reasons.extend(artifact_missing)
        reasons = tuple(dict.fromkeys(pending_reasons))
        status = EvaluationGateStatus.PENDING
    else:
        reasons = ()
        status = EvaluationGateStatus.PASS
    if legacy_incomplete:
        reasons = (*reasons, "LEGACY_REVIEW_INCOMPLETE")
    if no_material_claims:
        reasons = ("NO_MATERIAL_CLAIMS", *reasons)

    return EvaluationGate(
        id=ShadowGateId.EVIDENCE,
        status=status,
        evidence_ids=tuple(sorted(evidence_ids)),
        reason_codes=reasons,
        measured_value=0 if status is EvaluationGateStatus.PASS else None,
        required_value=0,
    )


def _safety_gate(
    evidence: ShadowEvaluationEvidence,
    eligible_dates: tuple[date, ...],
) -> EvaluationGate:
    checks = _release_check_evidence(evidence, _SAFETY_RELEASE_CHECKS)
    superseded_ids = {
        item.corrects_observation_id
        for item in evidence.observations
        if item.corrects_observation_id is not None
    }
    current_period_observations = tuple(
        item
        for item in evidence.observations
        if item.id not in superseded_ids
        and evidence.period.start_date <= item.market_date <= evidence.period.end_date
    )
    observations_by_date: dict[date, list[ShadowDayObservation]] = {}
    for item in current_period_observations:
        observations_by_date.setdefault(item.market_date, []).append(item)
    incidents = tuple(
        incident
        for item in current_period_observations
        if item.safety_incident_observation is not None
        for incident in item.safety_incident_observation.incidents
    )
    missing_observation_dates: list[date] = []
    source_mismatch_dates: list[date] = []
    for market_date in eligible_dates:
        day_observations = observations_by_date.get(market_date, [])
        if not day_observations or any(
            item.safety_incident_observation is None for item in day_observations
        ):
            missing_observation_dates.append(market_date)
        for item in day_observations:
            safety_observation = item.safety_incident_observation
            if safety_observation is None:
                continue
            if (
                evidence.source_commit_sha256 is None
                or evidence.source_tree_sha256 is None
                or safety_observation.source_commit_sha256 != evidence.source_commit_sha256
                or safety_observation.source_tree_sha256 != evidence.source_tree_sha256
            ):
                source_mismatch_dates.append(market_date)

    evidence_ids = tuple(
        sorted(
            {
                *evidence.evidence_hashes,
                *(item.id for item in checks.selected),
                *(item.id for item in checks.source_mismatches),
                *(item.id for item in current_period_observations),
                *(item.id for item in incidents),
                *(item.evidence_ref for item in incidents),
            }
        )
    )
    failure_reasons: list[str] = []
    if incidents:
        failure_reasons.append("SAFETY_INCIDENT_OBSERVED")
        failure_reasons.extend(
            f"SAFETY_INCIDENT_{incident.category.value}"
            for incident in sorted(incidents, key=lambda item: item.id)
        )
    if checks.failures:
        failure_reasons.append("SAFETY_RELEASE_CHECK_FAILED")
    if failure_reasons:
        return EvaluationGate(
            id=ShadowGateId.SAFETY,
            status=EvaluationGateStatus.FAIL,
            evidence_ids=evidence_ids,
            reason_codes=tuple(failure_reasons),
            measured_value=len(incidents) if incidents else len(checks.failures),
            required_value=0,
        )

    pending_reasons = []
    if not eligible_dates:
        pending_reasons.append("NO_ELIGIBLE_TRADING_DAYS")
    if not checks.source_identity_known:
        pending_reasons.append("SAFETY_SOURCE_IDENTITY_UNAVAILABLE")
    if checks.source_mismatches:
        pending_reasons.append("SAFETY_RELEASE_CHECK_SOURCE_MISMATCH")
    if checks.missing:
        pending_reasons.append("SAFETY_RELEASE_CHECK_MATRIX_INCOMPLETE")
    if missing_observation_dates:
        pending_reasons.append("SAFETY_INCIDENT_OBSERVATION_REQUIRED")
    if source_mismatch_dates:
        pending_reasons.append("SAFETY_INCIDENT_OBSERVATION_SOURCE_MISMATCH")
    observation_coverage_complete = bool(eligible_dates) and not (
        missing_observation_dates or source_mismatch_dates
    )
    return EvaluationGate(
        id=ShadowGateId.SAFETY,
        status=(EvaluationGateStatus.PENDING if pending_reasons else EvaluationGateStatus.PASS),
        evidence_ids=evidence_ids,
        reason_codes=tuple(pending_reasons),
        measured_value=0 if observation_coverage_complete else None,
        required_value=0,
    )


def _failure_operations_gate(
    evidence: ShadowEvaluationEvidence,
    eligible_dates: tuple[date, ...],
) -> EvaluationGate:
    superseded_ids = {
        item.corrects_observation_id
        for item in evidence.observations
        if item.corrects_observation_id is not None
    }
    current_observations = tuple(
        sorted(
            (
                item
                for item in evidence.observations
                if item.id not in superseded_ids
                and evidence.period.start_date <= item.market_date <= evidence.period.end_date
            ),
            key=lambda item: (item.market_date, item.recorded_at, item.id),
        )
    )
    observations_by_date: dict[date, list[ShadowDayObservation]] = {}
    for item in current_observations:
        observations_by_date.setdefault(item.market_date, []).append(item)

    evidence_ids = set(evidence.evidence_hashes)
    known_failure_refs: set[str] = set()
    failure_records: dict[str, tuple[ErrorCode, frozenset[Capability]]] = {}
    missing_detail = False
    recovery_not_actionable = False
    recovery_reference_missing = False
    missing_capability = False
    conflicting_records = False
    missing_coverage = False
    incomplete_coverage = False

    for item in current_observations:
        evidence_ids.add(item.id)
        known_failure_refs.update(item.production_failure_refs)
        evidence_ids.update(item.production_failure_refs)
        evidence_ids.update(item.actionable_recovery_refs)
        observation = item.failure_operations_observation
        if observation is None:
            if item.market_date in eligible_dates:
                missing_coverage = True
            continue
        if observation.coverage is ShadowFailureOperationsCoverage.INCOMPLETE:
            if item.market_date in eligible_dates:
                incomplete_coverage = True
        for failure in observation.failures:
            evidence_ids.add(failure.failure_ref)
            known_failure_refs.add(failure.failure_ref)
            stable_identity = (
                failure.failure_code,
                frozenset(failure.affected_capabilities),
            )
            prior = failure_records.get(failure.failure_ref)
            if prior is not None and prior != stable_identity:
                conflicting_records = True
            else:
                failure_records[failure.failure_ref] = stable_identity
            if not failure.affected_capabilities:
                missing_capability = True
            recovery = failure.recovery
            if recovery is None:
                recovery_not_actionable = True
                continue
            evidence_ids.add(recovery.recovery_ref)
            if (
                not recovery.action
                or not recovery.validation_plan
                or not recovery.action.strip()
                or not recovery.validation_plan.strip()
            ):
                recovery_not_actionable = True
            if recovery.recovery_ref not in item.actionable_recovery_refs:
                recovery_reference_missing = True

    for market_date in eligible_dates:
        day_observations = observations_by_date.get(market_date, [])
        if not day_observations:
            missing_coverage = True
            continue
        if any(item.failure_operations_observation is None for item in day_observations):
            missing_coverage = True
        if any(
            item.failure_operations_observation is not None
            and item.failure_operations_observation.coverage
            is ShadowFailureOperationsCoverage.INCOMPLETE
            for item in day_observations
        ):
            incomplete_coverage = True

    operation_refs = set(failure_records)
    missing_detail = bool(known_failure_refs - operation_refs)
    reasons: list[str] = []
    if missing_detail:
        reasons.append("FAILURE_OPERATION_DETAILS_MISSING")
    if missing_capability:
        reasons.append("FAILURE_OPERATION_CAPABILITY_MISSING")
    if recovery_not_actionable:
        reasons.append("FAILURE_RECOVERY_NOT_ACTIONABLE")
    if recovery_reference_missing:
        reasons.append("FAILURE_RECOVERY_REFERENCE_MISSING")
    if conflicting_records:
        reasons.append("CONFLICTING_FAILURE_OPERATION_RECORDS")
    if reasons:
        status = EvaluationGateStatus.FAIL
    else:
        if missing_coverage:
            reasons.append("FAILURE_OPERATIONS_COVERAGE_UNKNOWN")
        if incomplete_coverage:
            reasons.append("FAILURE_OPERATIONS_COVERAGE_INCOMPLETE")
        if not eligible_dates:
            reasons.append("NO_ELIGIBLE_TRADING_DAYS")
        status = EvaluationGateStatus.PENDING if reasons else EvaluationGateStatus.PASS

    return EvaluationGate(
        id=ShadowGateId.FAILURE_OPERATIONS,
        status=status,
        evidence_ids=tuple(sorted(evidence_ids)),
        reason_codes=tuple(reasons),
        measured_value=(
            len(known_failure_refs) if not (missing_coverage or incomplete_coverage) else None
        ),
        required_value="all observed failures have an actionable recovery record",
    )


def _release_check_evidence(
    evidence: ShadowEvaluationEvidence,
    required_checks: tuple[ReleaseCheckId, ...],
) -> _ReleaseCheckEvidence:
    selected: list[ReleaseCheckReceipt] = []
    missing: list[tuple[ReleaseCheckId, str]] = []
    failures: list[ReleaseCheckReceipt] = []
    source_mismatches: list[ReleaseCheckReceipt] = []
    source_identity_known = (
        evidence.source_commit_sha256 is not None and evidence.source_tree_sha256 is not None
    )

    for check_id in required_checks:
        for python_version in _RELEASE_CHECK_PYTHON_VERSIONS:
            candidates = tuple(
                item
                for item in evidence.release_check_receipts
                if item.check_id is check_id and item.python_version == python_version
            )
            if not source_identity_known:
                missing.append((check_id, python_version))
                continue
            exact_source_receipts = tuple(
                item
                for item in candidates
                if item.source_commit_sha256 == evidence.source_commit_sha256
                and item.source_tree_sha256 == evidence.source_tree_sha256
            )
            if not exact_source_receipts:
                missing.append((check_id, python_version))
                source_mismatches.extend(candidates)
                continue
            selected.extend(exact_source_receipts)
            failures.extend(
                item
                for item in exact_source_receipts
                if item.conclusion is not ReleaseCheckConclusion.SUCCESS
            )

    return _ReleaseCheckEvidence(
        selected=tuple(selected),
        missing=tuple(missing),
        failures=tuple(failures),
        source_mismatches=tuple(source_mismatches),
        source_identity_known=source_identity_known,
    )


def _review_deterministic_artifact(
    verified_run: VerifiedShadowRunEvidence,
) -> _DeterministicArtifactReview:
    bundle = verified_run.published_bundle
    if bundle is None:
        return _DeterministicArtifactReview(failures=(), missing=(), stale_sizing_count=None)

    contents = bundle.model_dump(mode="json")["bundle"]
    if contents.get("brief_origin") == BriefOrigin.OPERATIONAL.value:
        return _DeterministicArtifactReview(failures=(), missing=(), stale_sizing_count=0)

    packet_value = contents.get("research_packet")
    if packet_value is None:
        return _DeterministicArtifactReview(
            failures=(), missing=("FROZEN_RESEARCH_PACKET_REQUIRED",), stale_sizing_count=None
        )
    try:
        packet = ResearchPacket.model_validate_json(_deterministic_json_bytes(packet_value))
    except (TypeError, ValueError):
        return _DeterministicArtifactReview(
            failures=("FROZEN_RESEARCH_PACKET_INVALID",), missing=(), stale_sizing_count=None
        )

    failures: list[str] = []
    missing: list[str] = []
    if contents.get("brief_origin") == BriefOrigin.DETERMINISTIC_REDUCED.value:
        from finance_research_agent.application.reduced_report import render_reduced_report

        try:
            expected_report = render_reduced_report(
                packet, ReducedReportReason(contents["reduced_report_reason"])
            )
        except (KeyError, TypeError, ValueError):
            missing.append("DETERMINISTIC_REDUCED_DISCLOSURES_UNVERIFIED")
        else:
            if expected_report != bundle.report_markdown:
                missing.append("DETERMINISTIC_REDUCED_DISCLOSURES_UNVERIFIED")

    if "brief_draft" in contents:
        validation_value = contents.get("validation_report")
        if validation_value is None:
            missing.append("NUMERIC_VALIDATION_EVIDENCE_MISSING")
        else:
            try:
                draft = ResearchBriefDraft.model_validate_json(
                    _deterministic_json_bytes(contents["brief_draft"])
                )
                recorded_validation = ValidationReport.model_validate_json(
                    _deterministic_json_bytes(validation_value)
                )
                current_validation = validate_research_brief(
                    packet,
                    draft,
                    validation_attempt=recorded_validation.validation_attempt,
                )
            except (KeyError, TypeError, ValueError):
                failures.append("PUBLISHED_BRIEF_VALIDATION_INVALID")
            else:
                if (
                    not recorded_validation.is_valid
                    or not current_validation.is_valid
                    or current_validation != recorded_validation
                ):
                    failures.append("PUBLISHED_NUMERIC_OR_CLAIM_VALIDATION_FAILED")

    stale_symbols = {
        str(item.structured_fields["requested_symbol"])
        for item in packet.evidence
        if item.source.provider == "alpaca"
        and item.structured_fields.get("outcome") == "PREMARKET_PRICE"
        and item.structured_fields.get("error_code") == "STALE_DATA"
        and isinstance(item.structured_fields.get("requested_symbol"), str)
    }
    stale_sizing_count = sum(
        plan.symbol in stale_symbols and plan.position_sizing.suggested_units is not None
        for plan in packet.deterministic_plan_inputs
    )
    if stale_sizing_count:
        failures.append("STALE_CURRENT_PRICE_SIZING_OBSERVED")

    return _DeterministicArtifactReview(
        failures=tuple(failures),
        missing=tuple(missing),
        stale_sizing_count=stale_sizing_count,
    )


def _deterministic_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _deterministic_correctness_gate(evidence: ShadowEvaluationEvidence) -> EvaluationGate:
    checks = _release_check_evidence(evidence, _DETERMINISTIC_RELEASE_CHECKS)
    published_runs = tuple(
        item for item in evidence.verified_runs if item.published_bundle is not None
    )
    same_version_runs = tuple(
        item
        for item in published_runs
        if item.replay_result is not None and not item.replay_result.component_version_mismatches
    )
    replay_failures = tuple(
        item
        for item in same_version_runs
        if item.replay_result is None
        or not item.replay_result.json_matches
        or not item.replay_result.markdown_matches
        or item.replay_result.stored_json_sha256 != item.replay_result.replayed_json_sha256
        or item.replay_result.replayed_markdown_sha256 is None
        or item.replay_result.stored_markdown_sha256 != item.replay_result.replayed_markdown_sha256
    )
    artifact_reviews = tuple(_review_deterministic_artifact(item) for item in same_version_runs)
    artifact_failures = tuple(
        dict.fromkeys(reason for review in artifact_reviews for reason in review.failures)
    )
    artifact_missing = tuple(
        dict.fromkeys(reason for review in artifact_reviews for reason in review.missing)
    )
    stale_counts = tuple(review.stale_sizing_count for review in artifact_reviews)
    stale_sizing_count = (
        sum(count for count in stale_counts if count is not None)
        if same_version_runs and all(count is not None for count in stale_counts)
        else None
    )
    evidence_ids = tuple(
        sorted(
            {
                *evidence.evidence_hashes,
                *(item.id for item in checks.selected),
                *(item.id for item in checks.source_mismatches),
                *(item.stored_run.run_id for item in same_version_runs),
            }
        )
    )

    reasons = []
    if checks.failures:
        reasons.append("DETERMINISTIC_RELEASE_CHECK_FAILED")
    if replay_failures:
        reasons.append("SAME_VERSION_ARTIFACT_REPLAY_MISMATCH")
    reasons.extend(artifact_failures)
    reasons = list(dict.fromkeys(reasons))
    if reasons:
        return EvaluationGate(
            id=ShadowGateId.DETERMINISTIC_CORRECTNESS,
            status=EvaluationGateStatus.FAIL,
            evidence_ids=evidence_ids,
            reason_codes=tuple(reasons),
            measured_value=stale_sizing_count,
            required_value=0,
        )

    pending_reasons = []
    if not checks.source_identity_known:
        pending_reasons.append("DETERMINISTIC_SOURCE_IDENTITY_UNAVAILABLE")
    if checks.source_mismatches:
        pending_reasons.append("DETERMINISTIC_RELEASE_CHECK_SOURCE_MISMATCH")
    if checks.missing:
        pending_reasons.append("DETERMINISTIC_RELEASE_CHECK_MATRIX_INCOMPLETE")
    pending_reasons.extend(artifact_missing)
    if not same_version_runs:
        pending_reasons.append("DETERMINISTIC_SAME_VERSION_REPLAY_REQUIRED")
    pending_reasons = list(dict.fromkeys(pending_reasons))
    if pending_reasons:
        return EvaluationGate(
            id=ShadowGateId.DETERMINISTIC_CORRECTNESS,
            status=EvaluationGateStatus.PENDING,
            evidence_ids=evidence_ids,
            reason_codes=tuple(pending_reasons),
            measured_value=stale_sizing_count,
            required_value=0,
        )

    return EvaluationGate(
        id=ShadowGateId.DETERMINISTIC_CORRECTNESS,
        status=EvaluationGateStatus.PASS,
        evidence_ids=evidence_ids,
        measured_value=stale_sizing_count,
        required_value=0,
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
    source_commit_sha256: Sha256 | None = None
    source_tree_sha256: Sha256 | None = None
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
        if (self.source_commit_sha256 is None) != (self.source_tree_sha256 is None):
            raise ValueError("scorecard source identity requires both commit and tree digests")
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
        ShadowGateId.SAFETY: _safety_gate(evidence, eligible_dates),
        ShadowGateId.FAILURE_OPERATIONS: _failure_operations_gate(evidence, eligible_dates),
        ShadowGateId.DETERMINISTIC_CORRECTNESS: _deterministic_correctness_gate(evidence),
        ShadowGateId.EVIDENCE: _citation_evidence_gate(evidence),
        ShadowGateId.REVIEW_TIME: review_time_gate,
        ShadowGateId.NORMAL_PROVIDER_DURATION: duration_gate,
        ShadowGateId.TIMELY_PUBLICATION: timely_gate,
        ShadowGateId.USEFULNESS: usefulness_gate,
        ShadowGateId.EXECUTIVE_IDENTIFICATION: _executive_identification_gate(
            report_dates,
            selected_publications,
            evidence,
        ),
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
        source_commit_sha256=evidence.source_commit_sha256,
        source_tree_sha256=evidence.source_tree_sha256,
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
