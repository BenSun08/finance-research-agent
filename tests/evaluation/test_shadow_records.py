from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from finance_research_agent.domain.enums import Capability
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.regime import Regime
from finance_research_agent.evaluation.shadow_records import (
    OperatorReleaseAttestation,
    OperatorReleaseCheckId,
    OperatorReleaseResult,
    ReleaseCheckConclusion,
    ReleaseCheckId,
    ReleaseCheckReceipt,
    ShadowDayAvailability,
    ShadowDayExclusionReason,
    ShadowDayObservation,
    ShadowExecutiveEventState,
    ShadowExecutiveIdentification,
    ShadowFailureOperation,
    ShadowFailureOperationsCoverage,
    ShadowFailureOperationsObservation,
    ShadowFailureOperationStatus,
    ShadowFailureRecoveryRecord,
    ShadowSafetyIncident,
    ShadowSafetyIncidentCategory,
    ShadowSafetyIncidentObservation,
)


def _observation(**changes: object) -> ShadowDayObservation:
    values: dict[str, object] = {
        "id": "shadow-day-1",
        "market_date": date(2026, 10, 9),
        "recorded_at": datetime(2026, 10, 10, 0, 0, tzinfo=UTC),
        "session_id": "session-2026-10-09",
        "availability": ShadowDayAvailability.ONLINE,
    }
    values.update(changes)
    return ShadowDayObservation(**values)


def test_shadow_day_exclusion_requires_one_of_the_closed_attested_reasons() -> None:
    excluded = _observation(
        availability=ShadowDayAvailability.EXCLUDED,
        exclusion_reason=ShadowDayExclusionReason.TOTAL_LOCAL_NETWORK_OUTAGE,
    )

    assert excluded.exclusion_reason is ShadowDayExclusionReason.TOTAL_LOCAL_NETWORK_OUTAGE
    with pytest.raises(ValidationError, match="EXCLUDED requires an allowed exclusion reason"):
        _observation(availability=ShadowDayAvailability.EXCLUDED)
    with pytest.raises(ValidationError, match="only valid for EXCLUDED"):
        _observation(exclusion_reason=ShadowDayExclusionReason.COMPUTER_OR_CODEX_UNAVAILABLE)


def test_shadow_day_record_rejects_duplicate_references_and_unlinked_corrections() -> None:
    with pytest.raises(ValidationError, match="run ids must be unique"):
        _observation(observed_run_ids=("run-1", "run-1"))
    with pytest.raises(ValidationError, match="correction requires both prior id and digest"):
        _observation(corrects_observation_id="shadow-day-0")

    corrected = _observation(
        corrects_observation_id="shadow-day-0",
        corrects_observation_sha256="a" * 64,
    )
    assert corrected.corrects_observation_id == "shadow-day-0"

    with pytest.raises(ValidationError, match="observation cannot correct itself"):
        _observation(
            corrects_observation_id="shadow-day-1",
            corrects_observation_sha256="a" * 64,
        )
    with pytest.raises(ValidationError, match="feedback ids must be unique"):
        _observation(observed_feedback_ids=("feedback-1", "feedback-1"))


_SAFETY_CATEGORIES = tuple(ShadowSafetyIncidentCategory)


def _safety_observation(**changes: object) -> ShadowSafetyIncidentObservation:
    values: dict[str, object] = {
        "source_commit_sha256": "a" * 64,
        "source_tree_sha256": "b" * 64,
        "reviewed_categories": _SAFETY_CATEGORIES,
        "incidents": (),
    }
    values.update(changes)
    return ShadowSafetyIncidentObservation(**values)


def test_shadow_day_safety_observation_is_source_bound_and_schema_versioned() -> None:
    legacy = _observation(schema_version="0.1")
    current = _observation(safety_incident_observation=_safety_observation())

    assert legacy.schema_version == "0.1"
    assert legacy.safety_incident_observation is None
    assert current.schema_version == "0.3"
    assert current.safety_incident_observation is not None
    assert current.safety_incident_observation.reviewed_categories == _SAFETY_CATEGORIES
    with pytest.raises(
        ValidationError, match="schema 0.1 cannot include a safety incident observation"
    ):
        _observation(
            schema_version="0.1",
            safety_incident_observation=_safety_observation(),
        )


def _failure_operations_observation(**changes: object) -> ShadowFailureOperationsObservation:
    values: dict[str, object] = {
        "coverage": ShadowFailureOperationsCoverage.COMPLETE,
        "failures": (
            ShadowFailureOperation(
                failure_ref="failure-1",
                failure_code=ErrorCode.PROVIDER_UNAVAILABLE,
                status=ShadowFailureOperationStatus.RESOLVED,
                affected_capabilities=(Capability.MARKET_SUMMARY_AVAILABLE,),
                recovery=ShadowFailureRecoveryRecord(
                    recovery_ref="recovery-1",
                    action="Restore the configured market data connection.",
                    validation_plan="Run the provider readiness check and confirm one daily bar.",
                ),
            ),
        ),
    }
    values.update(changes)
    return ShadowFailureOperationsObservation(**values)


def test_shadow_failure_operations_are_explicit_and_schema_versioned() -> None:
    legacy_safety = _observation(
        schema_version="0.2",
        safety_incident_observation=_safety_observation(),
    )
    current = _observation(
        safety_incident_observation=_safety_observation(),
        failure_operations_observation=_failure_operations_observation(),
        production_failure_refs=("failure-1",),
        actionable_recovery_refs=("recovery-1",),
    )

    assert legacy_safety.schema_version == "0.2"
    assert current.schema_version == "0.3"
    assert current.failure_operations_observation is not None
    assert (
        current.failure_operations_observation.coverage
        is ShadowFailureOperationsCoverage.COMPLETE
    )

    with pytest.raises(
        ValidationError,
        match="schema 0.2 cannot include failure operations observation",
    ):
        _observation(
            schema_version="0.2",
            failure_operations_observation=_failure_operations_observation(),
        )


def test_shadow_failure_operation_references_must_be_unique() -> None:
    failure = _failure_operations_observation().failures[0]
    with pytest.raises(ValidationError, match="failure references must be unique"):
        _failure_operations_observation(failures=(failure, failure))


def test_shadow_failure_recovery_text_must_be_printable_and_nonblank() -> None:
    with pytest.raises(ValidationError, match="recovery action must be printable English text"):
        ShadowFailureRecoveryRecord(
            recovery_ref="recovery-1",
            action="   ",
            validation_plan="Check the recovery.",
        )


@pytest.mark.parametrize(
    "categories",
    (
        _SAFETY_CATEGORIES[:-1],
        (*_SAFETY_CATEGORIES[:-1], _SAFETY_CATEGORIES[0]),
        (*_SAFETY_CATEGORIES, _SAFETY_CATEGORIES[0]),
    ),
)
def test_safety_observation_requires_every_closed_category_exactly_once(
    categories: tuple[ShadowSafetyIncidentCategory, ...],
) -> None:
    with pytest.raises(ValidationError):
        _safety_observation(reviewed_categories=categories)


def test_safety_observation_rejects_duplicate_incident_ids() -> None:
    incident = ShadowSafetyIncident(
        id="incident-1",
        category=ShadowSafetyIncidentCategory.CREDENTIAL_LEAK,
        evidence_ref="incident-evidence-1",
        occurred_at=datetime(2026, 10, 9, 12, tzinfo=UTC),
    )

    with pytest.raises(ValidationError, match="incident ids must be unique"):
        _safety_observation(incidents=(incident, incident))


def test_shadow_executive_identification_validates_material_event_evidence() -> None:
    identification = ShadowExecutiveIdentification(
        market_posture=Regime.NEUTRAL,
        event_state=ShadowExecutiveEventState.MATERIAL_EVENTS,
        event_ids=("event-1",),
        priority_symbols=("AAPL",),
        disabled_capabilities=(Capability.EVENT_RISK_CHECK_AVAILABLE,),
    )

    assert identification.event_ids == ("event-1",)
    with pytest.raises(ValidationError, match="material events require identified event ids"):
        ShadowExecutiveIdentification(
            market_posture=Regime.NEUTRAL,
            event_state=ShadowExecutiveEventState.MATERIAL_EVENTS,
            event_ids=(),
            priority_symbols=(),
            disabled_capabilities=(),
        )

    valid_fields = {
        "market_posture": Regime.NEUTRAL,
        "event_state": ShadowExecutiveEventState.NO_MATERIAL_EVENTS,
        "event_ids": (),
        "priority_symbols": ("AAPL",),
        "disabled_capabilities": (Capability.EVENT_RISK_CHECK_AVAILABLE,),
    }
    for field, duplicate_values, message in (
        ("event_ids", ("event-1", "event-1"), "executive event ids must be unique"),
        (
            "priority_symbols",
            ("AAPL", "AAPL"),
            "executive priority symbols must be unique",
        ),
        (
            "disabled_capabilities",
            (Capability.EVENT_RISK_CHECK_AVAILABLE,) * 2,
            "executive disabled capabilities must be unique",
        ),
    ):
        with pytest.raises(ValidationError, match=message):
            ShadowExecutiveIdentification(**(valid_fields | {field: duplicate_values}))

    with pytest.raises(ValidationError, match="event ids require MATERIAL_EVENTS"):
        ShadowExecutiveIdentification(
            **(valid_fields | {"event_ids": ("event-1",)})
        )


def test_operator_release_attestation_binds_check_result_and_exact_source_identity() -> None:
    attestation = OperatorReleaseAttestation(
        id="attestation-desktop-1",
        check_id=OperatorReleaseCheckId.DESKTOP_INSTALL,
        result=OperatorReleaseResult.PASS,
        operator_id="operator-1",
        recorded_at=datetime(2026, 10, 10, 1, 0, tzinfo=UTC),
        package_version="0.5.0.dev0",
        skill_version="sha256:" + "b" * 64,
        source_commit_sha256="c" * 64,
        source_tree_sha256="d" * 64,
        evidence_sha256="e" * 64,
    )

    assert attestation.check_id is OperatorReleaseCheckId.DESKTOP_INSTALL
    assert attestation.result is OperatorReleaseResult.PASS
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        OperatorReleaseAttestation.model_validate(
            attestation.model_dump(mode="python") | {"verified": True}, strict=True
        )


def test_release_check_receipt_binds_one_check_to_exact_source_and_ci_step() -> None:
    receipt = ReleaseCheckReceipt(
        id="receipt-lint-py313",
        check_id=ReleaseCheckId.LINT,
        source_commit_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        repository_id="12345",
        workflow_id="67890",
        actions_run_id="run-20261011-1",
        actions_job_id="job-lint-py313",
        actions_step_id="step-ruff-check",
        python_version="3.13",
        completed_at=datetime(2026, 10, 11, 1, 0, tzinfo=UTC),
        conclusion=ReleaseCheckConclusion.SUCCESS,
        response_sha256="c" * 64,
    )

    assert receipt.check_id is ReleaseCheckId.LINT
    assert receipt.conclusion is ReleaseCheckConclusion.SUCCESS
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ReleaseCheckReceipt.model_validate(
            receipt.model_dump(mode="python") | {"status": "PASS"}, strict=True
        )


def test_release_check_receipt_rejects_unknown_checks_and_conclusions() -> None:
    fields: dict[str, object] = {
        "id": "receipt-lint-py313",
        "check_id": ReleaseCheckId.LINT,
        "source_commit_sha256": "a" * 64,
        "source_tree_sha256": "b" * 64,
        "repository_id": "12345",
        "workflow_id": "67890",
        "actions_run_id": "run-20261011-1",
        "actions_job_id": "job-lint-py313",
        "actions_step_id": "step-ruff-check",
        "python_version": "3.13",
        "completed_at": datetime(2026, 10, 11, 1, 0, tzinfo=UTC),
        "conclusion": ReleaseCheckConclusion.SUCCESS,
        "response_sha256": "c" * 64,
    }

    with pytest.raises(ValidationError):
        ReleaseCheckReceipt(**(fields | {"check_id": "UNSCOPED_CHECK"}))
    with pytest.raises(ValidationError):
        ReleaseCheckReceipt(**(fields | {"conclusion": "pass"}))
