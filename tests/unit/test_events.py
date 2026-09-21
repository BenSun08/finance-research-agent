from datetime import UTC, datetime, timedelta

import pytest

from finance_research_agent.domain.enums import GateStatus, PlanStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.events import EventEvidenceProjection, assess_event_risk
from finance_research_agent.domain.models import EventRecord, InstrumentIdentity, SourceHealth

NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
INSTRUMENT = InstrumentIdentity(
    instrument_id="MSFT",
    symbol="MSFT",
    name="Microsoft",
    instrument_type="COMMON_STOCK",
    primary_exchange="NASDAQ",
    listing_country="US",
    currency="USD",
    is_active=True,
    is_leveraged=False,
    is_inverse=False,
    is_otc=False,
)


def _projection(event_id: str, *, retrieved_at: datetime = NOW) -> EventEvidenceProjection:
    return EventEvidenceProjection(
        event_id=event_id,
        retrieved_at=retrieved_at,
        supporting_authority_tier=1,
        conflicting_authority_tier=None,
    )


def test_verified_earnings_inside_plan_window_blocks_plan() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id="earnings",
                event_type="EARNINGS",
                subject_symbol="MSFT",
                event_time=NOW + timedelta(days=1),
                verified=True,
                materiality="HIGH",
                supporting_evidence_ids=("ev-earnings",),
                conflict_evidence_ids=(),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        event_evidence=(_projection("earnings"),),
    )

    assert assessment.plan_status is PlanStatus.BLOCKED
    assert any(gate.status is GateStatus.BLOCK for gate in assessment.gates)


def test_unverified_material_event_requires_review() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id="guidance",
                event_type="GUIDANCE",
                subject_symbol="MSFT",
                event_time=NOW,
                verified=False,
                materiality="HIGH",
                supporting_evidence_ids=("ev-guidance",),
                conflict_evidence_ids=(),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        event_evidence=(_projection("guidance"),),
    )

    assert assessment.plan_status is PlanStatus.REVIEW_REQUIRED


def test_source_conflict_remains_visible_and_blocks_affected_plan() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id="conflict",
                event_type="CORPORATE_ACTION",
                subject_symbol="MSFT",
                event_time=NOW,
                verified=True,
                materiality="HIGH",
                supporting_evidence_ids=("ev-official",),
                conflict_evidence_ids=("ev-media",),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        event_evidence=(
            EventEvidenceProjection(
                event_id="conflict",
                retrieved_at=NOW,
                supporting_authority_tier=1,
                conflicting_authority_tier=None,
            ),
        ),
    )

    assert "SOURCE_CONFLICT" in assessment.quality_flags
    assert assessment.plan_status is PlanStatus.BLOCKED


def test_missing_required_macro_calendar_blocks_new_plans() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        source_health=(
            SourceHealth(
                provider="macro-calendar",
                available=False,
                required=True,
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="unavailable",
            ),
        ),
    )

    assert assessment.plan_status is PlanStatus.BLOCKED


def test_material_evidence_after_cutoff_requires_new_revision() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id="late",
                event_type="FILING",
                subject_symbol="MSFT",
                event_time=NOW + timedelta(seconds=1),
                verified=True,
                materiality="HIGH",
                supporting_evidence_ids=("ev-late",),
                conflict_evidence_ids=(),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        event_evidence=(
            EventEvidenceProjection(
                event_id="late",
                retrieved_at=NOW + timedelta(seconds=1),
                supporting_authority_tier=1,
                conflicting_authority_tier=None,
            ),
        ),
    )

    assert assessment.plan_status is PlanStatus.BLOCKED
    assert "EVIDENCE_CUTOFF_VIOLATION" in assessment.quality_flags


def test_relevant_material_event_with_evidence_requires_a_projection() -> None:
    with pytest.raises(ValueError, match="missing event evidence projection"):
        assess_event_risk(
            instrument=INSTRUMENT,
            events=(
                EventRecord(
                    event_id="missing-projection",
                    event_type="FILING",
                    subject_symbol="MSFT",
                    event_time=NOW - timedelta(days=1),
                    verified=True,
                    materiality="HIGH",
                    supporting_evidence_ids=("ev-missing",),
                    conflict_evidence_ids=(),
                ),
            ),
            plan_expires_at=NOW + timedelta(days=2),
            evidence_cutoff_at=NOW,
        )


def test_unknown_event_evidence_projection_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown event evidence projection"):
        assess_event_risk(
            instrument=INSTRUMENT,
            events=(),
            plan_expires_at=NOW + timedelta(days=2),
            evidence_cutoff_at=NOW,
            event_evidence=(_projection("unknown"),),
        )


def test_duplicate_event_evidence_projection_is_rejected() -> None:
    event = EventRecord(
        event_id="duplicate-projection",
        event_type="FILING",
        subject_symbol="MSFT",
        event_time=NOW - timedelta(days=1),
        verified=True,
        materiality="HIGH",
        supporting_evidence_ids=("ev-duplicate",),
        conflict_evidence_ids=(),
    )
    with pytest.raises(ValueError, match="duplicate event evidence projection"):
        assess_event_risk(
            instrument=INSTRUMENT,
            events=(event,),
            plan_expires_at=NOW + timedelta(days=2),
            evidence_cutoff_at=NOW,
            event_evidence=(_projection(event.event_id), _projection(event.event_id)),
        )


def test_future_scheduled_event_known_before_cutoff_is_not_a_cutoff_violation() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id="scheduled",
                event_type="FILING",
                subject_symbol="MSFT",
                event_time=NOW + timedelta(days=1),
                verified=True,
                materiality="HIGH",
                supporting_evidence_ids=("ev-scheduled",),
                conflict_evidence_ids=(),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        event_evidence=(
            EventEvidenceProjection(
                event_id="scheduled",
                retrieved_at=NOW - timedelta(seconds=1),
                supporting_authority_tier=1,
                conflicting_authority_tier=None,
            ),
        ),
    )

    assert assessment.plan_status is PlanStatus.DRAFT
    assert "EVIDENCE_CUTOFF_VIOLATION" not in assessment.quality_flags


def test_past_material_event_retrieved_after_cutoff_requires_new_revision() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id="late-past",
                event_type="FILING",
                subject_symbol="MSFT",
                event_time=NOW - timedelta(days=1),
                verified=True,
                materiality="HIGH",
                supporting_evidence_ids=("ev-late-past",),
                conflict_evidence_ids=(),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        event_evidence=(
            EventEvidenceProjection(
                event_id="late-past",
                retrieved_at=NOW + timedelta(seconds=1),
                supporting_authority_tier=1,
                conflicting_authority_tier=None,
            ),
        ),
    )

    assert assessment.plan_status is PlanStatus.BLOCKED
    assert "EVIDENCE_CUTOFF_VIOLATION" in assessment.quality_flags


@pytest.mark.parametrize("provider", ("federal_reserve", "bls", "bea"))
def test_required_official_macro_outage_blocks_plans(provider: str) -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        source_health=(
            SourceHealth(
                provider=provider,
                available=False,
                required=True,
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="unavailable",
            ),
        ),
    )

    assert assessment.plan_status is PlanStatus.BLOCKED


def test_low_authority_conflict_remains_visible_without_blocking_plan() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id="low-conflict",
                event_type="GUIDANCE",
                subject_symbol="MSFT",
                event_time=NOW,
                verified=True,
                materiality="LOW",
                supporting_evidence_ids=("ev-official",),
                conflict_evidence_ids=("ev-discovery",),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        event_evidence=(
            EventEvidenceProjection(
                event_id="low-conflict",
                retrieved_at=NOW,
                supporting_authority_tier=1,
                conflicting_authority_tier=3,
            ),
        ),
    )

    assert assessment.plan_status is PlanStatus.DRAFT
    assert assessment.quality_flags == ("SOURCE_CONFLICT",)


def test_material_conflict_with_lower_authority_counterevidence_does_not_block() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id="resolved-conflict",
                event_type="GUIDANCE",
                subject_symbol="MSFT",
                event_time=NOW,
                verified=True,
                materiality="HIGH",
                supporting_evidence_ids=("ev-official",),
                conflict_evidence_ids=("ev-discovery",),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        event_evidence=(
            EventEvidenceProjection(
                event_id="resolved-conflict",
                retrieved_at=NOW,
                supporting_authority_tier=1,
                conflicting_authority_tier=2,
            ),
        ),
    )

    assert assessment.plan_status is PlanStatus.DRAFT
    assert assessment.quality_flags == ("SOURCE_CONFLICT",)


@pytest.mark.parametrize("event_type", ("HALT", "IDENTITY_UNCERTAIN"))
def test_halt_or_identity_event_blocks_plan(event_type: str) -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id=event_type.lower(),
                event_type=event_type,
                subject_symbol="MSFT",
                event_time=NOW,
                verified=True,
                materiality="HIGH",
                supporting_evidence_ids=(),
                conflict_evidence_ids=(),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
    )

    assert assessment.plan_status is PlanStatus.BLOCKED


def test_high_impact_macro_event_is_explicit_no_trade_condition() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id="cpi",
                event_type="CPI",
                subject_symbol=None,
                event_time=NOW + timedelta(hours=1),
                verified=True,
                materiality="HIGH",
                supporting_evidence_ids=(),
                conflict_evidence_ids=(),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
    )

    assert assessment.event_risks == ("cpi",)
    assert assessment.no_trade_conditions == ("cpi",)


def test_source_policy_can_escalate_unverified_material_event_to_block() -> None:
    assessment = assess_event_risk(
        instrument=INSTRUMENT,
        events=(
            EventRecord(
                event_id="unverified-block",
                event_type="GUIDANCE",
                subject_symbol="MSFT",
                event_time=NOW,
                verified=False,
                materiality="HIGH",
                supporting_evidence_ids=("ev-unverified",),
                conflict_evidence_ids=(),
            ),
        ),
        plan_expires_at=NOW + timedelta(days=2),
        evidence_cutoff_at=NOW,
        event_evidence=(_projection("unverified-block"),),
        unverified_material_status=PlanStatus.BLOCKED,
    )

    assert assessment.plan_status is PlanStatus.BLOCKED
