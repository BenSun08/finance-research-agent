"""Versioned, additive feedback contracts for R11 citation reviews."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from finance_research_agent.application.operations import (
    CitationEntailmentReview,
    RecordRunFeedbackRequest,
)
from finance_research_agent.domain.enums import Capability
from finance_research_agent.domain.regime import Regime

REVIEWED_AT = datetime(2026, 10, 10, 13, 0, tzinfo=UTC)


def test_legacy_feedback_remains_valid_and_full_feedback_advertises_v02() -> None:
    legacy = RecordRunFeedbackRequest(
        run_id="premarket-2026-10-10-r1",
        clarity_score=3,
        evidence_score=4,
        usefulness_score=5,
    )
    full = RecordRunFeedbackRequest.model_validate_json(
        json.dumps(
            {
            "schema_version": "0.2",
            "run_id": "premarket-2026-10-10-r1",
            "clarity_score": 3,
            "evidence_score": 4,
            "usefulness_score": 5,
            "citation_reviews": [
                {
                    "schema_version": "0.2",
                    "citation_id": "evidence-00",
                    "claim_id": "claim-1",
                    "entails_claim": True,
                    "verdict": "SUPPORTED",
                    "rationale": "The cited source directly supports the stated field and date.",
                    "reviewed_at": REVIEWED_AT.isoformat(),
                }
            ],
            "executive_review_duration_seconds": 160,
            "detailed_review_duration_seconds": 720,
            "executive_identification": {
                "market_posture": "unknown",
                "event_state": "UNAVAILABLE",
                "event_ids": [],
                "priority_symbols": ["AAPL"],
                "disabled_capabilities": ["EVENT_RISK_CHECK_AVAILABLE"],
            },
            }
        ),
        strict=True,
    )

    assert legacy.schema_version == "0.1"
    assert full.schema_version == "0.2"
    assert full.citation_reviews[0].schema_version == "0.2"
    assert full.citation_reviews[0].verdict.value == "SUPPORTED"
    assert full.executive_review_duration_seconds == 160
    assert full.executive_identification is not None
    assert full.executive_identification.market_posture is Regime.UNKNOWN


@pytest.mark.parametrize(
    "review",
    (
        {
            "schema_version": "0.2",
            "citation_id": "evidence-00",
            "claim_id": "claim-1",
            "entails_claim": True,
            "verdict": "SUPPORTED",
        },
        {
            "schema_version": "0.2",
            "citation_id": "evidence-00",
            "claim_id": "claim-1",
            "entails_claim": False,
            "verdict": "SUPPORTED",
            "rationale": "The cited source supports the claim.",
            "reviewed_at": REVIEWED_AT.isoformat(),
        },
        {
            "schema_version": "0.1",
            "citation_id": "evidence-00",
            "claim_id": "claim-1",
            "entails_claim": True,
            "verdict": "SUPPORTED",
            "rationale": "The cited source supports the claim.",
            "reviewed_at": REVIEWED_AT.isoformat(),
        },
        {
            "schema_version": "0.2",
            "citation_id": "evidence-00",
            "claim_id": "claim-1",
            "entails_claim": False,
            "verdict": "UNSUPPORTED",
            "rationale": "支持 is not an English rationale.",
            "reviewed_at": REVIEWED_AT.isoformat(),
        },
    ),
)
def test_citation_review_requires_a_versioned_consistent_full_review_group(
    review: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        CitationEntailmentReview.model_validate(review)


def test_feedback_bounds_durations_and_structured_identification() -> None:
    with pytest.raises(ValidationError):
        RecordRunFeedbackRequest(
            schema_version="0.2",
            run_id="premarket-2026-10-10-r1",
            clarity_score=3,
            evidence_score=4,
            usefulness_score=5,
            executive_review_duration_seconds=True,
        )
    with pytest.raises(ValidationError):
        RecordRunFeedbackRequest(
            schema_version="0.2",
            run_id="premarket-2026-10-10-r1",
            clarity_score=3,
            evidence_score=4,
            usefulness_score=5,
            detailed_review_duration_seconds=86401,
        )
    with pytest.raises(ValidationError):
        RecordRunFeedbackRequest(
            schema_version="0.2",
            run_id="premarket-2026-10-10-r1",
            clarity_score=3,
            evidence_score=4,
            usefulness_score=5,
            executive_identification={
                "market_posture": Regime.UNKNOWN,
                "event_state": "UNAVAILABLE",
                "event_ids": [],
                "priority_symbols": [],
                "disabled_capabilities": [
                    Capability.EVENT_RISK_CHECK_AVAILABLE,
                    Capability.EVENT_RISK_CHECK_AVAILABLE,
                ],
            },
        )


def test_legacy_feedback_serialization_omits_additive_v02_fields() -> None:
    request = RecordRunFeedbackRequest(
        run_id="premarket-2026-10-10-r1",
        clarity_score=3,
        evidence_score=4,
        usefulness_score=5,
        citation_reviews=(
            CitationEntailmentReview(
                citation_id="evidence-00", claim_id="claim-1", entails_claim=True
            ),
        ),
    )

    assert request.model_dump(mode="json") == {
        "schema_version": "0.1",
        "run_id": "premarket-2026-10-10-r1",
        "clarity_score": 3,
        "evidence_score": 4,
        "usefulness_score": 5,
        "notes": None,
        "citation_reviews": [
            {
                "schema_version": "0.1",
                "citation_id": "evidence-00",
                "claim_id": "claim-1",
                "entails_claim": True,
            }
        ],
    }


def test_event_identification_state_requires_matching_event_ids() -> None:
    base = {
        "schema_version": "0.2",
        "run_id": "premarket-2026-10-10-r1",
        "clarity_score": 3,
        "evidence_score": 4,
        "usefulness_score": 5,
    }
    with pytest.raises(ValidationError):
        RecordRunFeedbackRequest(
            **base,
            executive_identification={
                "market_posture": Regime.UNKNOWN,
                "event_state": "MATERIAL_EVENTS",
                "event_ids": (),
                "priority_symbols": (),
                "disabled_capabilities": (),
            },
        )
    with pytest.raises(ValidationError):
        RecordRunFeedbackRequest(
            **base,
            executive_identification={
                "market_posture": Regime.UNKNOWN,
                "event_state": "NO_MATERIAL_EVENTS",
                "event_ids": ("event-1",),
                "priority_symbols": (),
                "disabled_capabilities": (),
            },
        )
