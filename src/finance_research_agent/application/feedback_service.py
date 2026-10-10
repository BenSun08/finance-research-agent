"""Append-only human feedback bound to an immutable published run bundle."""

from __future__ import annotations

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
from finance_research_agent.domain.types import UtcDatetime, canonical_bytes


class RecordedFeedback(StrictModel):
    """Immutable snapshot of bounded rubric feedback for one published bundle."""

    schema_version: Literal["0.1", "0.2"] = "0.1"  # type: ignore[assignment]
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

        self._validate_citation_reviews(request.citation_reviews, bundle)
        recorded_at = self._clock.now_utc()
        offset = recorded_at.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("feedback clock must return a UTC timestamp")
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
        reviews: tuple[CitationEntailmentReview, ...], bundle: PublishedRunBundle
    ) -> None:
        seen: set[tuple[str, str]] = set()
        allowed: set[tuple[str, str]] = set()
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
                            allowed.add((citation_id, claim_id))

        for review in reviews:
            pair = (review.citation_id, review.claim_id)
            if pair in seen:
                raise ValueError("duplicate citation review is not allowed")
            seen.add(pair)
            if pair not in allowed:
                raise ValueError("citation review is not selected for this published bundle")


__all__ = ["RecordedFeedback", "RunFeedbackService"]
