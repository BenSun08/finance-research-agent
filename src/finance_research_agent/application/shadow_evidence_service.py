"""Service-stamped append operations for shadow observations and attestations."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from finance_research_agent.application.ports import (
    Clock,
    OperatorReleaseAttestationRepository,
    ShadowObservationRepository,
    TradingCalendar,
)
from finance_research_agent.domain.models import (
    Identifier,
    Sha256,
    SkillVersion,
    StrictModel,
)
from finance_research_agent.domain.types import UtcDatetime, canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    NonNegativeReviewSeconds,
    OperatorReleaseAttestation,
    OperatorReleaseCheckId,
    OperatorReleaseResult,
    ShadowDayAvailability,
    ShadowDayExclusionReason,
    ShadowDayObservation,
    ShadowExecutiveIdentification,
    ShadowFailureOperationsObservation,
    ShadowSafetyIncidentObservation,
)

_NEW_YORK = ZoneInfo("America/New_York")


class RecordShadowDayObservation(StrictModel):
    """Operator input; `recorded_at` is intentionally service-owned."""

    schema_version: Literal["0.1", "0.2", "0.3"] = "0.3"  # type: ignore[assignment]
    id: Identifier
    market_date: date
    session_id: Identifier
    availability: ShadowDayAvailability
    exclusion_reason: ShadowDayExclusionReason | None = None
    observed_run_ids: tuple[Identifier, ...] = Field(default=(), max_length=128)
    observed_feedback_ids: tuple[Identifier, ...] = Field(default=(), max_length=256)
    production_failure_refs: tuple[Identifier, ...] = Field(default=(), max_length=128)
    actionable_recovery_refs: tuple[Identifier, ...] = Field(default=(), max_length=128)
    executive_review_duration_seconds: NonNegativeReviewSeconds | None = None
    detailed_review_duration_seconds: NonNegativeReviewSeconds | None = None
    executive_identification: ShadowExecutiveIdentification | None = None
    safety_incident_observation: ShadowSafetyIncidentObservation | None = None
    failure_operations_observation: ShadowFailureOperationsObservation | None = None
    corrects_observation_id: Identifier | None = None
    corrects_observation_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def request_is_compatible_with_schema_version(self) -> RecordShadowDayObservation:
        if self.schema_version == "0.1" and self.safety_incident_observation is not None:
            raise ValueError("schema 0.1 cannot include a safety incident observation")
        if (
            self.schema_version in {"0.1", "0.2"}
            and self.failure_operations_observation is not None
        ):
            raise ValueError(
                f"schema {self.schema_version} cannot include failure operations observation"
            )
        return self


class ShadowObservationReceipt(StrictModel):
    observation_id: Identifier
    recorded_at: UtcDatetime
    record_sha256: Sha256


class RecordOperatorReleaseAttestation(StrictModel):
    """Operator-supplied host result; the service supplies its timestamp."""

    id: Identifier
    check_id: OperatorReleaseCheckId
    result: OperatorReleaseResult
    operator_id: Identifier
    package_version: Identifier
    skill_version: SkillVersion
    source_commit_sha256: Sha256
    source_tree_sha256: Sha256
    evidence_sha256: Sha256


class OperatorReleaseAttestationReceipt(StrictModel):
    attestation_id: Identifier
    check_id: OperatorReleaseCheckId
    recorded_at: UtcDatetime
    record_sha256: Sha256


def record_shadow_day_observation(
    request: RecordShadowDayObservation,
    repository: ShadowObservationRepository,
    calendar: TradingCalendar,
    clock: Clock,
) -> ShadowObservationReceipt:
    """Append an operator record after validating its date and evidence links."""
    request = RecordShadowDayObservation.model_validate(request, strict=True)
    recorded_at = _now_utc(clock)
    market_date = request.market_date
    if market_date > recorded_at.astimezone(_NEW_YORK).date():
        raise ValueError("cannot record a future shadow market date")
    if not calendar.is_trading_day(market_date):
        raise ValueError("shadow observation market_date must be a trading day")
    for run_id in request.observed_run_ids:
        if not repository.has_run_id(run_id):
            raise ValueError("shadow observation has an unknown run reference")
    for feedback_id in request.observed_feedback_ids:
        if not repository.has_feedback_id(feedback_id):
            raise ValueError("shadow observation has an unknown feedback reference")

    if request.corrects_observation_id is not None:
        previous = repository.get_observation(request.corrects_observation_id)
        if previous is None:
            raise ValueError("shadow observation correction target does not exist")
        previous_digest = sha256(canonical_bytes(previous)).hexdigest()
        if previous_digest != request.corrects_observation_sha256:
            raise ValueError("shadow observation correction target digest does not match")
        if previous.market_date != market_date:
            raise ValueError("shadow observation correction must keep the same market date")

    record = ShadowDayObservation.model_validate(
        request.model_dump(mode="python") | {"recorded_at": recorded_at}, strict=True
    )
    digest = sha256(canonical_bytes(record)).hexdigest()
    repository.append_observation(record)
    return ShadowObservationReceipt(
        observation_id=record.id,
        recorded_at=record.recorded_at,
        record_sha256=digest,
    )


def record_operator_release_attestation(
    request: RecordOperatorReleaseAttestation,
    repository: OperatorReleaseAttestationRepository,
    clock: Clock,
) -> OperatorReleaseAttestationReceipt:
    """Append a self-attributed result; never execute the check being attested."""
    request = RecordOperatorReleaseAttestation.model_validate(request, strict=True)
    record = OperatorReleaseAttestation.model_validate(
        request.model_dump(mode="python") | {"recorded_at": _now_utc(clock)}, strict=True
    )
    digest = sha256(canonical_bytes(record)).hexdigest()
    repository.append_attestation(record)
    return OperatorReleaseAttestationReceipt(
        attestation_id=record.id,
        check_id=record.check_id,
        recorded_at=record.recorded_at,
        record_sha256=digest,
    )


def _now_utc(clock: Clock) -> UtcDatetime:
    value = clock.now_utc()
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("shadow evidence clock must return a UTC timestamp")
    return value


__all__ = [
    "OperatorReleaseAttestationReceipt",
    "RecordOperatorReleaseAttestation",
    "RecordShadowDayObservation",
    "ShadowObservationReceipt",
    "record_operator_release_attestation",
    "record_shadow_day_observation",
]
