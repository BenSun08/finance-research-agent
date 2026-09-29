"""Tests for immutable Product A run feedback."""

from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

import pytest
from pydantic import ValidationError

from finance_research_agent.application.feedback_service import (
    FeedbackRepository,
    RecordedFeedback,
    RunFeedbackService,
)
from finance_research_agent.application.operations import (
    CitationEntailmentReview,
    RecordRunFeedbackRequest,
)
from finance_research_agent.application.ports import Clock, PublishedArtifactReader
from finance_research_agent.domain.models import PublishedArtifact, PublishedRunBundle, RunContext
from finance_research_agent.domain.types import FrozenMap, canonical_bytes

RUN_ID = "premarket-2026-09-29-r1"
RECORDED_AT = datetime(2026, 9, 29, 13, 0, tzinfo=UTC)


class _Clock:
    def now_utc(self) -> datetime:
        return RECORDED_AT


class _PublishedReader:
    def __init__(self, *, published: bool = True) -> None:
        self.published = published
        self.bundle = PublishedRunBundle.model_construct(
            run=RunContext.model_construct(run_id=RUN_ID),
            bundle=FrozenMap(
                {
                    "brief_draft": {
                        "claims": [
                            {
                                "claim_id": "claim-1",
                                "evidence_ids": ["evidence-1"],
                                "counter_evidence_ids": [],
                                "metric_ids": [],
                            }
                        ]
                    }
                }
            ),
            report_markdown="published report",
            markdown_sha256=None,
        )

    def load_published_bundle(self, run_id: str) -> PublishedRunBundle | None:
        return self.bundle if self.published and run_id == RUN_ID else None

    def get_report(self, run_id: str) -> str | None:
        return "published report" if self.published and run_id == RUN_ID else None

    def get_published_artifact(self, run_id: str) -> PublishedArtifact | None:
        if not self.published or run_id != RUN_ID:
            return None
        return PublishedArtifact(
            run_id=RUN_ID,
            bundle_sha256=sha256(canonical_bytes(self.bundle)).hexdigest(),
            markdown_sha256="b" * 64,
            published_at=RECORDED_AT,
        )


class _FeedbackStore:
    def __init__(self) -> None:
        self.records: list[RecordedFeedback] = []

    def append_feedback(self, feedback: RecordedFeedback) -> None:
        self.records.append(feedback)

    def list_feedback(self) -> tuple[RecordedFeedback, ...]:
        return tuple(self.records)


def _service(
    published: bool = True,
) -> tuple[RunFeedbackService, _PublishedReader, _FeedbackStore]:
    reader = _PublishedReader(published=published)
    store = _FeedbackStore()
    service = RunFeedbackService(
        cast(PublishedArtifactReader, reader),
        cast(FeedbackRepository, store),
        cast(Clock, _Clock()),
        feedback_id_factory=lambda: f"feedback-{len(store.records) + 1}",
    )
    return service, reader, store


def _request(**updates: object) -> RecordRunFeedbackRequest:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "clarity_score": 3,
        "evidence_score": 4,
        "usefulness_score": 5,
    }
    values.update(updates)
    return RecordRunFeedbackRequest.model_validate(values)


def test_feedback_accepts_bounded_scores_and_notes() -> None:
    request = _request(notes="Clear and useful.")
    assert request.clarity_score == 3
    assert request.notes == "Clear and useful."
    with pytest.raises(ValidationError):
        _request(clarity_score=0)
    with pytest.raises(ValidationError):
        _request(usefulness_score=6)
    with pytest.raises(ValidationError):
        _request(notes="x" * 1001)


def test_recorded_feedback_rejects_invalid_run_identifiers() -> None:
    with pytest.raises(ValidationError):
        RecordedFeedback(
            feedback_id="feedback-1",
            run_id="../outside",
            bundle_sha256="a" * 64,
            recorded_at=RECORDED_AT,
            clarity_score=3,
            evidence_score=4,
            usefulness_score=5,
            citation_reviews=(),
        )


def test_feedback_rejects_unknown_or_unpublished_runs_before_append() -> None:
    service, _, store = _service(published=False)

    with pytest.raises(ValueError, match="published run"):
        service.record(_request())
    with pytest.raises(ValueError, match="published run"):
        service.record(_request(run_id="premarket-2026-09-28-r1"))

    assert store.records == []


def test_feedback_rejects_citation_reviews_not_selected_for_exact_bundle() -> None:
    service, _, store = _service()
    request = _request(
        citation_reviews=(
            CitationEntailmentReview(
                citation_id="foreign-evidence", claim_id="claim-1", entails_claim=True
            ),
        )
    )

    with pytest.raises(ValueError, match="published bundle"):
        service.record(request)

    assert store.records == []


def test_feedback_rejects_duplicate_citation_claim_pairs() -> None:
    service, _, store = _service()
    review = CitationEntailmentReview(
        citation_id="evidence-1", claim_id="claim-1", entails_claim=True
    )
    request = _request(citation_reviews=(review, review))

    with pytest.raises(ValueError, match="duplicate citation review"):
        service.record(request)

    assert store.records == []


def test_feedback_rejects_published_bundle_hash_mismatch() -> None:
    service, reader, store = _service()
    artifact = reader.get_published_artifact(RUN_ID)
    assert artifact is not None
    reader.get_published_artifact = lambda run_id: artifact.model_copy(
        update={"bundle_sha256": "c" * 64}
    )

    with pytest.raises(ValueError, match="verified published run bundle"):
        service.record(_request())

    assert store.records == []


def test_feedback_appends_distinct_frozen_records_without_changing_publication() -> None:
    service, reader, store = _service()
    before_bundle = reader.bundle.model_dump(mode="json")
    request = _request(
        citation_reviews=(
            CitationEntailmentReview(
                citation_id="evidence-1", claim_id="claim-1", entails_claim=True
            ),
        )
    )

    first = service.record(request)
    second = service.record(request)

    assert first.feedback_id != second.feedback_id
    assert first.run_id == second.run_id == RUN_ID
    assert first.recorded_at == second.recorded_at == RECORDED_AT
    assert len(store.records) == 2
    assert reader.bundle.model_dump(mode="json") == before_bundle
    with pytest.raises((AttributeError, TypeError, ValidationError)):
        first.clarity_score = 1


def test_feedback_list_is_sorted_by_recorded_at_then_feedback_id() -> None:
    service, _, store = _service()
    service.record(_request())
    service.record(_request())
    store.records.reverse()

    records = service.list_feedback()

    assert tuple(record.feedback_id for record in records) == (
        "feedback-1",
        "feedback-2",
    )
