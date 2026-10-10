"""Tests for immutable Product A run feedback."""

from datetime import UTC, datetime, timedelta
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
    CitationVerdict,
    ExecutiveEventState,
    RecordRunFeedbackRequest,
)
from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.ports import Clock, PublishedArtifactReader
from finance_research_agent.domain.enums import Capability, ClaimType
from finance_research_agent.domain.models import PublishedArtifact, PublishedRunBundle, RunContext
from finance_research_agent.domain.regime import Regime
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.domain.validation import Claim
from finance_research_agent.evaluation.citation_sampling import select_citation_entailment_sample

RUN_ID = "premarket-2026-09-29-r1"
RECORDED_AT = datetime(2026, 9, 29, 13, 0, tzinfo=UTC)


class _Clock:
    def now_utc(self) -> datetime:
        return RECORDED_AT


class _PublishedReader:
    def __init__(
        self,
        *,
        published: bool = True,
        bundle: PublishedRunBundle | None = None,
        published_at: datetime = RECORDED_AT,
    ) -> None:
        self.published = published
        self.bundle = bundle or PublishedRunBundle.model_construct(
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
        self.published_at = published_at

    def load_published_bundle(self, run_id: str) -> PublishedRunBundle | None:
        return self.bundle if self.published and run_id == self.bundle.run.run_id else None

    def get_report(self, run_id: str) -> str | None:
        return (
            "published report"
            if self.published and run_id == self.bundle.run.run_id
            else None
        )

    def get_published_artifact(self, run_id: str) -> PublishedArtifact | None:
        if not self.published or run_id != self.bundle.run.run_id:
            return None
        return PublishedArtifact(
            run_id=self.bundle.run.run_id,
            bundle_sha256=sha256(canonical_bytes(self.bundle)).hexdigest(),
            markdown_sha256="b" * 64,
            published_at=self.published_at,
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
    *,
    bundle: PublishedRunBundle | None = None,
    published_at: datetime = RECORDED_AT,
) -> tuple[RunFeedbackService, _PublishedReader, _FeedbackStore]:
    reader = _PublishedReader(published=published, bundle=bundle, published_at=published_at)
    store = _FeedbackStore()
    service = RunFeedbackService(
        cast(PublishedArtifactReader, reader),
        cast(FeedbackRepository, store),
        cast(Clock, _Clock()),
        feedback_id_factory=lambda: f"feedback-{len(store.records) + 1}",
    )
    return service, reader, store


def _published_bundle(
    valid_packet, valid_brief_draft, *, outer_run: RunContext | None = None
) -> PublishedRunBundle:
    return PublishedRunBundle.model_construct(
        run=outer_run or valid_packet.run,
        bundle=FrozenMap(
            {
                "research_packet": valid_packet.model_dump(mode="json"),
                "brief_draft": valid_brief_draft.model_dump(mode="json"),
            }
        ),
        report_markdown="published report",
        markdown_sha256=None,
    )


def _rebuild_packet(valid_packet, *, run=None, metrics=None):
    return build_research_packet(
        run=run or valid_packet.run,
        evidence=valid_packet.evidence,
        snapshots=valid_packet.market,
        events=valid_packet.events,
        metrics=valid_packet.metrics if metrics is None else metrics,
        gates=valid_packet.gates,
        candidates=valid_packet.candidates,
        exclusions=valid_packet.candidate_exclusions,
        plans=valid_packet.deterministic_plan_inputs,
        capabilities=valid_packet.capability_states,
        observations=valid_packet.prior_plan_observations,
        max_serialized_bytes=valid_packet.synthesis_constraints.max_serialized_bytes,
        regime_result=valid_packet.regime_result,
    )


def _full_review(
    citation_id: str,
    claim_id: str,
    reviewed_at: datetime,
) -> CitationEntailmentReview:
    return CitationEntailmentReview(
        schema_version="0.2",
        citation_id=citation_id,
        claim_id=claim_id,
        entails_claim=True,
        verdict=CitationVerdict.SUPPORTED,
        rationale="The frozen evidence supports the selected claim.",
        reviewed_at=reviewed_at,
    )


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


def test_record_feedback_persists_v02_durations_and_identification() -> None:
    service, _, store = _service()
    request = _request(
        schema_version="0.2",
        executive_review_duration_seconds=150,
        detailed_review_duration_seconds=840,
        executive_identification={
            "market_posture": Regime.UNKNOWN,
            "event_state": ExecutiveEventState.UNAVAILABLE,
            "event_ids": (),
            "priority_symbols": ("AAPL",),
            "disabled_capabilities": (Capability.EVENT_RISK_CHECK_AVAILABLE,),
        },
    )

    service.record(request)

    assert len(store.records) == 1
    [record] = store.records
    assert record.schema_version == "0.2"
    assert record.executive_review_duration_seconds == 150
    assert record.detailed_review_duration_seconds == 840
    assert record.executive_identification is not None
    assert record.executive_identification.market_posture is Regime.UNKNOWN


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


def test_recorded_feedback_requires_v02_for_additive_fields() -> None:
    with pytest.raises(ValidationError, match="schema 0.1"):
        RecordedFeedback(
            schema_version="0.1",
            feedback_id="feedback-legacy",
            run_id=RUN_ID,
            bundle_sha256="a" * 64,
            recorded_at=RECORDED_AT,
            clarity_score=3,
            evidence_score=4,
            usefulness_score=5,
            citation_reviews=(),
            executive_review_duration_seconds=120,
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


def test_full_review_accepts_metric_input_for_a_selected_claim(
    valid_packet, valid_brief_draft
) -> None:
    calculation = next(claim for claim in valid_brief_draft.claims if claim.claim_id == "claim-sma")
    draft = valid_brief_draft.model_copy(
        update={
            "claims": tuple(
                claim.model_copy(update={"evidence_ids": ()})
                if claim.claim_id == "claim-sma"
                else claim
                for claim in valid_brief_draft.claims
            )
        }
    )
    bundle = _published_bundle(valid_packet, draft)
    selected_claims = select_citation_entailment_sample(bundle)
    assert calculation.claim_id in selected_claims
    service, _, store = _service(
        bundle=bundle, published_at=RECORDED_AT - timedelta(minutes=5)
    )
    request = _request(
        run_id=bundle.run.run_id,
        schema_version="0.2",
        citation_reviews=(
            _full_review(
                "evidence-00", calculation.claim_id, RECORDED_AT - timedelta(minutes=1)
            ),
        ),
    )

    service.record(request)

    assert len(store.records) == 1
    assert store.records[0].citation_reviews == request.citation_reviews


@pytest.mark.parametrize("mismatch", ("packet", "brief"))
def test_full_review_rejects_packet_or_brief_from_another_run(
    valid_packet, valid_brief_draft, mismatch: str
) -> None:
    other_run_data = valid_packet.run.model_dump(mode="python")
    other_run_data.update(
        run_id=f"premarket-{valid_packet.run.market_date.isoformat()}-r2",
        revision=2,
    )
    other_run = RunContext.model_validate(other_run_data, strict=True)
    packet = (
        _rebuild_packet(valid_packet, run=other_run)
        if mismatch == "packet"
        else valid_packet
    )
    brief = (
        type(valid_brief_draft).model_validate(
            {
                **valid_brief_draft.model_dump(mode="python"),
                "run_id": other_run.run_id,
            },
            strict=True,
        )
        if mismatch == "brief"
        else valid_brief_draft
    )
    bundle = _published_bundle(packet, brief, outer_run=valid_packet.run)
    service, _, store = _service(
        bundle=bundle, published_at=RECORDED_AT - timedelta(minutes=5)
    )
    request = _request(
        run_id=bundle.run.run_id,
        schema_version="0.2",
        citation_reviews=(_full_review("evidence-00", "claim-headline", RECORDED_AT),),
    )

    with pytest.raises(ValueError, match="share one run id"):
        service.record(request)

    assert store.records == []


def test_full_review_rejects_a_claim_outside_the_deterministic_sample(
    valid_packet, valid_brief_draft
) -> None:
    orphan = Claim(
        claim_id="claim-orphan",
        claim_type=ClaimType.FACT,
        text="An unreferenced claim.",
        subject_symbol="AAPL",
        field="headline",
        evidence_ids=("evidence-01",),
    )
    draft = valid_brief_draft.model_copy(
        update={"claims": (*valid_brief_draft.claims, orphan)}
    )
    bundle = _published_bundle(valid_packet, draft)
    assert "claim-orphan" not in select_citation_entailment_sample(bundle)
    service, _, store = _service(
        bundle=bundle, published_at=RECORDED_AT - timedelta(minutes=5)
    )
    request = _request(
        run_id=bundle.run.run_id,
        schema_version="0.2",
        citation_reviews=(
            _full_review("evidence-01", "claim-orphan", RECORDED_AT - timedelta(minutes=1)),
        ),
    )

    with pytest.raises(ValueError, match="selected citation sample"):
        service.record(request)

    assert store.records == []


def test_full_review_rejects_unreferenced_evidence_for_a_selected_claim(
    valid_packet, valid_brief_draft
) -> None:
    bundle = _published_bundle(valid_packet, valid_brief_draft)
    service, _, store = _service(
        bundle=bundle, published_at=RECORDED_AT - timedelta(minutes=5)
    )
    request = _request(
        run_id=bundle.run.run_id,
        schema_version="0.2",
        citation_reviews=(
            _full_review("evidence-01", "claim-headline", RECORDED_AT - timedelta(minutes=1)),
        ),
    )

    with pytest.raises(ValueError, match="referenced evidence or a metric input"):
        service.record(request)

    assert store.records == []


@pytest.mark.parametrize(
    "reviewed_at",
    (
        RECORDED_AT - timedelta(minutes=6),
        RECORDED_AT + timedelta(seconds=1),
    ),
)
def test_full_review_timestamp_must_follow_publication_and_precede_recording(
    valid_packet, valid_brief_draft, reviewed_at: datetime
) -> None:
    bundle = _published_bundle(valid_packet, valid_brief_draft)
    service, _, store = _service(
        bundle=bundle, published_at=RECORDED_AT - timedelta(minutes=5)
    )
    request = _request(
        run_id=bundle.run.run_id,
        schema_version="0.2",
        citation_reviews=(
            _full_review("evidence-00", "claim-headline", reviewed_at),
        ),
    )

    with pytest.raises(ValueError, match="reviewed_at"):
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
