"""Append-only human feedback bound to an immutable published run bundle."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from finance_research_agent.application.operations import (
    CitationEntailmentReview,
    ExecutiveIdentification,
    FeedbackReceipt,
    RecordRunFeedbackRequest,
    RunId,
)
from finance_research_agent.application.ports import (
    Clock,
    FeedbackRepository,
    PublishedArtifactReader,
)
from finance_research_agent.domain.models import Identifier, PublishedRunBundle, StrictModel
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.types import UtcDatetime, canonical_bytes
from finance_research_agent.domain.validation import Claim, ResearchBriefDraft
from finance_research_agent.evaluation.citation_sampling import select_citation_entailment_sample


class RecordedFeedback(StrictModel):
    """Immutable snapshot of bounded rubric feedback for one published bundle."""

    schema_version: Literal["0.1", "0.2"] = Field(
        default="0.1", exclude_if=lambda value: value == "0.1"
    )  # type: ignore[assignment]
    feedback_id: Identifier
    run_id: RunId
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: UtcDatetime
    clarity_score: int = Field(ge=1, le=5)
    evidence_score: int = Field(ge=1, le=5)
    usefulness_score: int = Field(ge=1, le=5)
    notes: str | None = Field(default=None, max_length=1000)
    citation_reviews: tuple[CitationEntailmentReview, ...] = Field(max_length=5)
    executive_review_duration_seconds: int | None = Field(
        default=None, ge=0, le=86400, exclude_if=lambda value: value is None
    )
    detailed_review_duration_seconds: int | None = Field(
        default=None, ge=0, le=86400, exclude_if=lambda value: value is None
    )
    executive_identification: ExecutiveIdentification | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def stored_schema_matches_extensions(self) -> RecordedFeedback:
        if self.schema_version == "0.1":
            has_extensions = any(
                value is not None
                for value in (
                    self.executive_review_duration_seconds,
                    self.detailed_review_duration_seconds,
                    self.executive_identification,
                )
            ) or any(review.schema_version != "0.1" for review in self.citation_reviews)
            if has_extensions:
                raise ValueError("feedback record schema 0.1 cannot contain schema 0.2 fields")
        return self


class RunFeedbackService:
    """Validate and append feedback without changing published artifacts."""

    def __init__(
        self,
        published_reader: PublishedArtifactReader,
        feedback_repository: FeedbackRepository,
        clock: Clock,
        *,
        feedback_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._published_reader = published_reader
        self._feedback_repository = feedback_repository
        self._clock = clock
        self._feedback_id_factory = feedback_id_factory or (lambda: f"feedback-{uuid4().hex}")

    def record(self, request: RecordRunFeedbackRequest) -> FeedbackReceipt:
        """Append one separately identified record for an indexed publication."""
        request = RecordRunFeedbackRequest.model_validate(request, strict=True)
        artifact = self._published_reader.get_published_artifact(request.run_id)
        bundle = self._published_reader.load_published_bundle(request.run_id)
        if (
            artifact is None
            or bundle is None
            or artifact.run_id != request.run_id
            or bundle.run.run_id != request.run_id
            or sha256(canonical_bytes(bundle)).hexdigest() != artifact.bundle_sha256
        ):
            raise ValueError("feedback requires a verified published run bundle")

        recorded_at = self._clock.now_utc()
        offset = recorded_at.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("feedback clock must return a UTC timestamp")
        self._validate_citation_reviews(
            request.citation_reviews,
            bundle,
            published_at=artifact.published_at,
            recorded_at=recorded_at,
        )
        feedback = RecordedFeedback(
            schema_version=request.schema_version,
            feedback_id=self._feedback_id_factory(),
            run_id=request.run_id,
            bundle_sha256=artifact.bundle_sha256,
            recorded_at=recorded_at,
            clarity_score=request.clarity_score,
            evidence_score=request.evidence_score,
            usefulness_score=request.usefulness_score,
            notes=request.notes,
            citation_reviews=request.citation_reviews,
            executive_review_duration_seconds=request.executive_review_duration_seconds,
            detailed_review_duration_seconds=request.detailed_review_duration_seconds,
            executive_identification=request.executive_identification,
        )
        self._feedback_repository.append_feedback(feedback)
        return FeedbackReceipt(
            feedback_id=feedback.feedback_id,
            run_id=feedback.run_id,
            recorded_at=feedback.recorded_at,
        )

    def list_feedback(self) -> tuple[RecordedFeedback, ...]:
        """Return a deterministic chronological view of append-only records."""
        return tuple(
            sorted(
                self._feedback_repository.list_feedback(),
                key=lambda item: (item.recorded_at, item.feedback_id),
            )
        )

    @staticmethod
    def _validate_citation_reviews(
        reviews: tuple[CitationEntailmentReview, ...],
        bundle: PublishedRunBundle,
        *,
        published_at: UtcDatetime,
        recorded_at: UtcDatetime,
    ) -> None:
        seen: set[tuple[str, str]] = set()
        legacy_allowed: set[tuple[str, str]] = set()
        brief = bundle.bundle.get("brief_draft")
        if isinstance(brief, Mapping):
            claims = brief.get("claims", ())
            if isinstance(claims, (list, tuple)):
                for claim in claims:
                    if not isinstance(claim, Mapping):
                        continue
                    claim_id = claim.get("claim_id")
                    if not isinstance(claim_id, str):
                        continue
                    evidence_ids = claim.get("evidence_ids", ())
                    counter_evidence_ids = claim.get("counter_evidence_ids", ())
                    if not isinstance(evidence_ids, (list, tuple)):
                        continue
                    if not isinstance(counter_evidence_ids, (list, tuple)):
                        continue
                    citation_ids = (*evidence_ids, *counter_evidence_ids)
                    for citation_id in citation_ids:
                        if isinstance(citation_id, str):
                            legacy_allowed.add((citation_id, claim_id))

        full_reviews = tuple(review for review in reviews if review.schema_version == "0.2")
        full_allowed: set[tuple[str, str]] = set()
        selected_claim_ids: set[str] = set()
        if full_reviews:
            selected_claim_ids, full_allowed = RunFeedbackService._full_review_scope(bundle)

        for review in reviews:
            pair = (review.citation_id, review.claim_id)
            if pair in seen:
                raise ValueError("duplicate citation review is not allowed")
            seen.add(pair)
            if review.schema_version == "0.1":
                if pair not in legacy_allowed:
                    raise ValueError("citation review is not selected for this published bundle")
                continue
            if review.claim_id not in selected_claim_ids:
                raise ValueError(
                    "full citation review must target a selected citation sample claim"
                )
            if pair not in full_allowed:
                raise ValueError(
                    "full citation review must cite referenced evidence or a metric input"
                )
            if review.reviewed_at is None:
                raise ValueError("full citation review requires reviewed_at")
            if not published_at <= review.reviewed_at <= recorded_at:
                raise ValueError(
                    "citation review reviewed_at must be between publication and recording time"
                )

    @staticmethod
    def _full_review_scope(bundle: PublishedRunBundle) -> tuple[set[str], set[tuple[str, str]]]:
        selected_claim_ids = set(select_citation_entailment_sample(bundle))
        contents = bundle.model_dump(mode="json")["bundle"]
        try:
            packet_data = json.dumps(
                contents["research_packet"],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            brief_data = json.dumps(
                contents["brief_draft"],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            packet = ResearchPacket.model_validate_json(packet_data, strict=True)
            brief = ResearchBriefDraft.model_validate_json(brief_data, strict=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "full citation review requires a valid published research bundle"
            ) from exc
        if packet.run.run_id != bundle.run.run_id or brief.run_id != bundle.run.run_id:
            raise ValueError("full citation review inputs must share one run id")

        claims: dict[str, Claim] = {}
        for brief_claim in brief.claims:
            if brief_claim.claim_id in claims:
                raise ValueError("published brief has duplicate claim ids")
            claims[brief_claim.claim_id] = brief_claim
        metrics = {metric.metric_id: metric for metric in packet.metrics}
        allowed: set[tuple[str, str]] = set()
        for selected_claim_id in selected_claim_ids:
            pending = [selected_claim_id]
            visited: set[str] = set()
            while pending:
                current_id = pending.pop()
                claim = claims.get(current_id)
                if claim is None:
                    raise ValueError("published brief references a missing supporting claim")
                if current_id in visited:
                    continue
                visited.add(current_id)
                evidence_ids = (*claim.evidence_ids, *claim.counter_evidence_ids)
                for metric_id in claim.metric_ids:
                    metric = metrics.get(metric_id)
                    if metric is None:
                        raise ValueError("published claim references a missing metric")
                    evidence_ids = (*evidence_ids, *metric.input_evidence_ids)
                allowed.update((evidence_id, selected_claim_id) for evidence_id in evidence_ids)
                pending.extend(claim.supports_claim_ids)
        return selected_claim_ids, allowed


__all__ = ["RecordedFeedback", "RunFeedbackService"]
