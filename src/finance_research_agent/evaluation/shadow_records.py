"""Strict immutable records for operator-attributed shadow evidence."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from finance_research_agent.domain.enums import Capability
from finance_research_agent.domain.errors import ErrorCode
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


class ShadowSafetyIncidentCategory(StrEnum):
    UNAUTHORIZED_TOOL_PATH = "UNAUTHORIZED_TOOL_PATH"
    CREDENTIAL_LEAK = "CREDENTIAL_LEAK"
    PROHIBITED_STATE = "PROHIBITED_STATE"
    SERIOUS_INJECTION_ESCAPE = "SERIOUS_INJECTION_ESCAPE"
    MATERIAL_OUT_OF_ROOT_WRITE = "MATERIAL_OUT_OF_ROOT_WRITE"


class ShadowSafetyIncident(StrictModel):
    """One operator-observed incident with a stable evidence reference."""

    id: Identifier
    category: ShadowSafetyIncidentCategory
    evidence_ref: Identifier
    occurred_at: UtcDatetime


class ShadowSafetyIncidentObservation(StrictModel):
    """Complete source-bound category review for one observed shadow day."""

    source_commit_sha256: Sha256
    source_tree_sha256: Sha256
    reviewed_categories: tuple[ShadowSafetyIncidentCategory, ...] = Field(
        min_length=len(ShadowSafetyIncidentCategory),
        max_length=len(ShadowSafetyIncidentCategory),
        json_schema_extra={"uniqueItems": True},
    )
    incidents: tuple[ShadowSafetyIncident, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def observation_is_complete(self) -> ShadowSafetyIncidentObservation:
        if len(set(self.reviewed_categories)) != len(self.reviewed_categories) or set(
            self.reviewed_categories
        ) != set(ShadowSafetyIncidentCategory):
            raise ValueError("reviewed categories must contain every category exactly once")
        if len({item.id for item in self.incidents}) != len(self.incidents):
            raise ValueError("incident ids must be unique")
        return self


class ShadowFailureOperationStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"


class ShadowFailureOperationsCoverage(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class ShadowFailureRecoveryRecord(StrictModel):
    """Bounded recovery action and validation plan for one known failure."""

    recovery_ref: Identifier
    action: Annotated[str, Field(max_length=500, pattern=r"^[\x20-\x7E]*$")] | None = None
    validation_plan: Annotated[str, Field(max_length=500, pattern=r"^[\x20-\x7E]*$")] | None = None

    @model_validator(mode="after")
    def text_is_nonblank_when_present(self) -> ShadowFailureRecoveryRecord:
        for name, value in (
            ("recovery action", self.action),
            ("validation plan", self.validation_plan),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be printable English text")
        return self


class ShadowFailureOperation(StrictModel):
    """Typed operator record for one failure and its recovery state."""

    failure_ref: Identifier
    failure_code: ErrorCode
    status: ShadowFailureOperationStatus
    affected_capabilities: tuple[Capability, ...] = Field(default=(), max_length=8)
    recovery: ShadowFailureRecoveryRecord | None = None

    @model_validator(mode="after")
    def capabilities_are_unique(self) -> ShadowFailureOperation:
        if len(set(self.affected_capabilities)) != len(self.affected_capabilities):
            raise ValueError("affected capabilities must be unique")
        return self


class ShadowFailureOperationsObservation(StrictModel):
    """Operator declaration of whether all failures for a day were reviewed."""

    coverage: ShadowFailureOperationsCoverage
    failures: tuple[ShadowFailureOperation, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def failure_references_are_unique(self) -> ShadowFailureOperationsObservation:
        if len({item.failure_ref for item in self.failures}) != len(self.failures):
            raise ValueError("failure references must be unique")
        return self


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

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"schema_version": {"const": "0.1"}},
                        "required": ["schema_version"],
                    },
                    "then": {
                        "properties": {
                            "safety_incident_observation": {"type": "null"},
                            "failure_operations_observation": {"type": "null"},
                        }
                    },
                },
                {
                    "if": {
                        "properties": {"schema_version": {"const": "0.2"}},
                        "required": ["schema_version"],
                    },
                    "then": {
                        "properties": {
                            "failure_operations_observation": {"type": "null"}
                        }
                    },
                },
            ]
        }
    )

    schema_version: Literal["0.1", "0.2", "0.3"] = "0.3"  # type: ignore[assignment]
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
    safety_incident_observation: ShadowSafetyIncidentObservation | None = None
    failure_operations_observation: ShadowFailureOperationsObservation | None = None
    corrects_observation_id: Identifier | None = None
    corrects_observation_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def observation_is_consistent(self) -> ShadowDayObservation:
        if self.schema_version == "0.1" and self.safety_incident_observation is not None:
            raise ValueError("schema 0.1 cannot include a safety incident observation")
        if (
            self.schema_version in {"0.1", "0.2"}
            and self.failure_operations_observation is not None
        ):
            raise ValueError(
                f"schema {self.schema_version} cannot include failure operations observation"
            )
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


class ReleaseCheckId(StrEnum):
    """Closed set of machine checks used by the R11 release contract."""

    FORMAT = "FORMAT"
    LINT = "LINT"
    TYPES = "TYPES"
    UNIT_AND_INTEGRATION_TESTS = "UNIT_AND_INTEGRATION_TESTS"
    SCENARIO_AND_FAILURE_MATRIX = "SCENARIO_AND_FAILURE_MATRIX"
    BRANCH_COVERAGE = "BRANCH_COVERAGE"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    DOCS_AND_EXAMPLES = "DOCS_AND_EXAMPLES"
    SECRET_SCAN = "SECRET_SCAN"
    NETWORK_BOUNDARY = "NETWORK_BOUNDARY"
    PLUGIN_WHEEL_SDIST = "PLUGIN_WHEEL_SDIST"
    FRESH_WHEEL_INSTALL = "FRESH_WHEEL_INSTALL"
    DOCTOR = "DOCTOR"
    COMPATIBILITY = "COMPATIBILITY"


class ReleaseCheckConclusion(StrEnum):
    """GitHub check conclusions retained without converting them into gates."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    NEUTRAL = "neutral"
    TIMED_OUT = "timed_out"
    ACTION_REQUIRED = "action_required"
    STARTUP_FAILURE = "startup_failure"
    STALE = "stale"


class ReleaseCheckReceipt(StrictModel):
    """One source-bound CI step result; it carries evidence, not a pass flag."""

    id: Identifier
    check_id: ReleaseCheckId
    source_commit_sha256: Sha256
    source_tree_sha256: Sha256
    repository_id: Identifier
    workflow_id: Identifier
    actions_run_id: Identifier
    actions_job_id: Identifier
    actions_step_id: Identifier
    python_version: Identifier
    completed_at: UtcDatetime
    conclusion: ReleaseCheckConclusion
    response_sha256: Sha256


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
    "ReleaseCheckConclusion",
    "ReleaseCheckId",
    "ReleaseCheckReceipt",
    "ShadowDayAvailability",
    "ShadowDayExclusionReason",
    "ShadowDayObservation",
    "ShadowExecutiveEventState",
    "ShadowExecutiveIdentification",
    "ShadowFailureOperation",
    "ShadowFailureOperationStatus",
    "ShadowFailureOperationsCoverage",
    "ShadowFailureOperationsObservation",
    "ShadowFailureRecoveryRecord",
    "ShadowSafetyIncident",
    "ShadowSafetyIncidentCategory",
    "ShadowSafetyIncidentObservation",
]
