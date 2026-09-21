from datetime import UTC, datetime, timedelta

from finance_research_agent.domain.enums import GateStatus, PlanStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.events import assess_event_risk
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
    )

    assert assessment.plan_status is PlanStatus.BLOCKED
    assert "EVIDENCE_CUTOFF_VIOLATION" in assessment.quality_flags
