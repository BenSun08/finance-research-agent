"""Strict immutable records for operator-attributed shadow evidence."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from finance_research_agent.domain.enums import Capability
from finance_research_agent.domain.models import (
    Identifier,
    Sha256,
    SkillVersion,
    StrictModel,
    Symbol,
)
from finance_research_agent.domain.regime import Regime
from finance_research_agent.domain.types import UtcDatetime


class ShadowDayAvailability(StrEnum):
    ONLINE = "ONLINE"
    EXCLUDED = "EXCLUDED"
    UNKNOWN = "UNKNOWN"


class ShadowDayExclusionReason(StrEnum):
    COMPUTER_OR_CODEX_UNAVAILABLE = "COMPUTER_OR_CODEX_UNAVAILABLE"
    TOTAL_LOCAL_NETWORK_OUTAGE = "TOTAL_LOCAL_NETWORK_OUTAGE"


class ShadowExecutiveEventState(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NO_MATERIAL_EVENTS = "NO_MATERIAL_EVENTS"
    MATERIAL_EVENTS = "MATERIAL_EVENTS"


class ShadowExecutiveIdentification(StrictModel):
    """Bounded human account of the brief's posture, events, and priorities."""

    market_posture: Regime
    event_state: ShadowExecutiveEventState
    event_ids: tuple[Identifier, ...] = Field(max_length=64)
    priority_symbols: tuple[Symbol, ...] = Field(max_length=5)
    disabled_capabilities: tuple[Capability, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def identification_is_consistent(self) -> ShadowExecutiveIdentification:
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("executive event ids must be unique")
        if len(set(self.priority_symbols)) != len(self.priority_symbols):
            raise ValueError("executive priority symbols must be unique")
        if len(set(self.disabled_capabilities)) != len(self.disabled_capabilities):
            raise ValueError("executive disabled capabilities must be unique")
        if self.event_state is ShadowExecutiveEventState.MATERIAL_EVENTS and not self.event_ids:
            raise ValueError("material events require identified event ids")
        if self.event_state is not ShadowExecutiveEventState.MATERIAL_EVENTS and self.event_ids:
            raise ValueError("event ids require MATERIAL_EVENTS identification")
        return self


NonNegativeReviewSeconds = Annotated[int, Field(ge=0, le=86_400)]


class ShadowDayObservation(StrictModel):
    """One append-only operator record for an actual or unknown shadow day."""

    id: Identifier
    market_date: date
    recorded_at: UtcDatetime
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
    corrects_observation_id: Identifier | None = None
    corrects_observation_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def observation_is_consistent(self) -> ShadowDayObservation:
        if (
            self.availability is ShadowDayAvailability.EXCLUDED
            and self.exclusion_reason is None
        ):
            raise ValueError("EXCLUDED requires an allowed exclusion reason")
        if (
            self.availability is not ShadowDayAvailability.EXCLUDED
            and self.exclusion_reason is not None
        ):
            raise ValueError("exclusion reason is only valid for EXCLUDED")
        if (self.corrects_observation_id is None) != (
            self.corrects_observation_sha256 is None
        ):
            raise ValueError("correction requires both prior id and digest")
        if self.corrects_observation_id == self.id:
            raise ValueError("observation cannot correct itself")
        for name, values in (
            ("run ids", self.observed_run_ids),
            ("feedback ids", self.observed_feedback_ids),
            ("production failure refs", self.production_failure_refs),
            ("actionable recovery refs", self.actionable_recovery_refs),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must be unique")
        return self


class OperatorReleaseCheckId(StrEnum):
    DESKTOP_INSTALL = "DESKTOP_INSTALL"
    HOST_RESTART = "HOST_RESTART"
    SKILL_DISCOVERY_TRACE = "SKILL_DISCOVERY_TRACE"
    SUPERVISED_SYNTHETIC_WORKFLOW = "SUPERVISED_SYNTHETIC_WORKFLOW"
    ALPACA_LIVE_SMOKE = "ALPACA_LIVE_SMOKE"


class OperatorReleaseResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class OperatorReleaseAttestation(StrictModel):
    """Self-attributed result bound to exact package, skill, and source bytes."""

    id: Identifier
    check_id: OperatorReleaseCheckId
    result: OperatorReleaseResult
    operator_id: Identifier
    recorded_at: UtcDatetime
    package_version: Identifier
    skill_version: SkillVersion
    source_commit_sha256: Sha256
    source_tree_sha256: Sha256
    evidence_sha256: Sha256


__all__ = [
    "OperatorReleaseAttestation",
    "OperatorReleaseCheckId",
    "OperatorReleaseResult",
    "ShadowDayAvailability",
    "ShadowDayExclusionReason",
    "ShadowDayObservation",
    "ShadowExecutiveEventState",
    "ShadowExecutiveIdentification",
]
