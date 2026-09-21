"""Independent, non-overridable Product A event-risk gates."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from finance_research_agent.domain.enums import Capability, GateStatus, PlanStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    EventRecord,
    GateResult,
    Identifier,
    InstrumentIdentity,
    SourceHealth,
    StrictModel,
)
from finance_research_agent.domain.types import UtcDatetime

_RULE_VERSION = "r5-events-1"
_MACRO_TYPES = {"FED", "FOMC", "CPI", "PPI", "EMPLOYMENT", "GDP", "MACRO"}
_LATE_MATERIAL_TYPES = {"GUIDANCE", "FILING", "CORPORATE_ACTION", "ANNOUNCEMENT"}


class EventAssessment(StrictModel):
    """Event overlay outcome that a later score cannot weaken or override."""

    plan_status: PlanStatus
    gates: tuple[GateResult, ...]
    event_risks: tuple[str, ...] = Field(default=())
    no_trade_conditions: tuple[str, ...] = Field(default=())
    quality_flags: tuple[str, ...] = Field(default=())


class EventEvidenceProjection(StrictModel):
    """Prevalidated event-evidence metadata retained apart from event time."""

    event_id: Identifier
    retrieved_at: UtcDatetime
    supporting_authority_tier: int = Field(ge=1, le=255)
    conflicting_authority_tier: int | None = Field(default=None, ge=1, le=255)


def _gate(
    status: GateStatus, reason: ErrorCode, message: str, evidence_ids: tuple[str, ...]
) -> GateResult:
    return GateResult(
        gate_id=f"event-{reason.value.lower()}",
        status=status,
        reason_code=reason.value,
        message=message,
        evidence_ids=evidence_ids,
        capability=Capability.PLAN_DRAFT_AVAILABLE,
        rule_version=_RULE_VERSION,
    )


def _macro_unavailable(source_health: Sequence[SourceHealth]) -> bool:
    return any(
        not health.available
        and health.required
        and (
            "macro" in health.provider
            or health.provider in {"federal_reserve", "fed", "bls", "bea"}
        )
        for health in source_health
    )


def assess_event_risk(
    *,
    instrument: InstrumentIdentity,
    events: Sequence[EventRecord],
    plan_expires_at: UtcDatetime,
    evidence_cutoff_at: UtcDatetime,
    source_health: Sequence[SourceHealth] = (),
    event_evidence: Sequence[EventEvidenceProjection] = (),
    unverified_material_status: PlanStatus = PlanStatus.REVIEW_REQUIRED,
) -> EventAssessment:
    """Assess event evidence independently of regime or setup and fail closed."""

    gates: list[GateResult] = []
    risks: list[str] = []
    no_trade: list[str] = []
    flags: list[str] = []
    relevant_events = tuple(
        event for event in events if event.subject_symbol in {None, instrument.symbol}
    )
    relevant_event_ids = {event.event_id for event in relevant_events}
    projections: dict[str, EventEvidenceProjection] = {}
    for projection in event_evidence:
        if projection.event_id not in relevant_event_ids:
            raise ValueError("unknown event evidence projection")
        if projection.event_id in projections:
            raise ValueError("duplicate event evidence projection")
        projections[projection.event_id] = projection
    for event in relevant_events:
        if (
            event.materiality == "HIGH"
            and (event.supporting_evidence_ids or event.conflict_evidence_ids)
            and event.event_id not in projections
        ):
            raise ValueError("missing event evidence projection")
    if unverified_material_status not in {PlanStatus.REVIEW_REQUIRED, PlanStatus.BLOCKED}:
        raise ValueError("unverified material status must require review or block")
    if _macro_unavailable(source_health):
        gates.append(
            _gate(
                GateStatus.BLOCK,
                ErrorCode.PROVIDER_UNAVAILABLE,
                "required macro calendar is unavailable",
                (),
            )
        )
    for event in relevant_events:
        evidence_ids = tuple(
            dict.fromkeys((*event.supporting_evidence_ids, *event.conflict_evidence_ids))
        )
        event_projection = projections.get(event.event_id)
        event_type = event.event_type.upper()
        if event.conflict_evidence_ids:
            flags.append(ErrorCode.SOURCE_CONFLICT.value)
            unresolved = (
                event_projection is None
                or event_projection.conflicting_authority_tier is None
                or event_projection.conflicting_authority_tier
                <= event_projection.supporting_authority_tier
            )
            if event.materiality == "HIGH" and unresolved:
                gates.append(
                    _gate(
                        GateStatus.BLOCK,
                        ErrorCode.SOURCE_CONFLICT,
                        "material unresolved source conflict requires resolution",
                        evidence_ids,
                    )
                )
        if (
            event.materiality == "HIGH"
            and event_projection is not None
            and event_projection.retrieved_at > evidence_cutoff_at
        ):
            flags.append(ErrorCode.EVIDENCE_CUTOFF_VIOLATION.value)
            gates.append(
                _gate(
                    GateStatus.BLOCK,
                    ErrorCode.EVIDENCE_CUTOFF_VIOLATION,
                    "post-cutoff material evidence requires a new revision",
                    evidence_ids,
                )
            )
        if (
            event_type == "EARNINGS"
            and event.verified
            and evidence_cutoff_at <= event.event_time <= plan_expires_at
        ):
            gates.append(
                _gate(
                    GateStatus.BLOCK,
                    ErrorCode.UNSUPPORTED_INSTRUMENT,
                    "verified earnings fall inside plan lifetime",
                    evidence_ids,
                )
            )
        elif event_type in {"HALT", "CORPORATE_ACTION", "IDENTITY_UNCERTAIN"}:
            gates.append(
                _gate(
                    GateStatus.BLOCK,
                    ErrorCode.UNSUPPORTED_INSTRUMENT,
                    "halt, corporate action, or identity uncertainty blocks the instrument",
                    evidence_ids,
                )
            )
        elif not event.verified and event.materiality in {"MEDIUM", "HIGH", "UNKNOWN"}:
            gates.append(
                _gate(
                    (
                        GateStatus.BLOCK
                        if unverified_material_status is PlanStatus.BLOCKED
                        else GateStatus.WARNING
                    ),
                    ErrorCode.PROVIDER_NO_DATA,
                    (
                        "unverified material event is blocked by source policy"
                        if unverified_material_status is PlanStatus.BLOCKED
                        else "unverified material event requires review"
                    ),
                    evidence_ids,
                )
            )
        if event_type in _MACRO_TYPES and event.materiality == "HIGH":
            risks.append(event.event_id)
            no_trade.append(event.event_id)
    if any(gate.status is GateStatus.BLOCK for gate in gates):
        status = PlanStatus.BLOCKED
    elif any(gate.status is GateStatus.WARNING for gate in gates):
        status = PlanStatus.REVIEW_REQUIRED
    else:
        status = PlanStatus.DRAFT
    return EventAssessment(
        plan_status=status,
        gates=tuple(gates),
        event_risks=tuple(dict.fromkeys(risks)),
        no_trade_conditions=tuple(dict.fromkeys(no_trade)),
        quality_flags=tuple(dict.fromkeys(flags)),
    )
