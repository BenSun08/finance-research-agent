"""Deterministic release and migration-readiness aggregation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, model_validator

from finance_research_agent import __version__
from finance_research_agent.application.skill_bundle import (
    compute_skill_version,
    load_installed_premarket_skill_version,
)
from finance_research_agent.domain.enums import BriefOrigin
from finance_research_agent.domain.models import Identifier, Sha256, StrictModel
from finance_research_agent.evaluation.models import (
    DomainAssertion,
    EvaluationOutcome,
    EvaluationScenario,
    FailureInjectionId,
    ScenarioAssertionId,
    ScenarioId,
)
from finance_research_agent.evaluation.scenarios import load_evaluation_scenarios
from finance_research_agent.evaluation.shadow_records import (
    OperatorReleaseAttestation,
    OperatorReleaseCheckId,
    OperatorReleaseResult,
    ReleaseCheckConclusion,
    ReleaseCheckId,
    ReleaseCheckReceipt,
    ShadowDayAvailability,
)
from finance_research_agent.evaluation.shadow_scorecard import (
    EvaluationGate,
    ShadowEvaluationEvidence,
    ShadowEvaluationOrigin,
    ShadowScorecard,
)


class MigrationReadinessStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"
    DEFERRED = "DEFERRED"


class MigrationReadinessGate(StrictModel):
    """One derived migration gate with references to its supporting records."""

    id: Identifier
    status: MigrationReadinessStatus
    evidence_ids: tuple[Identifier, ...] = ()
    reason_codes: tuple[Identifier, ...] = ()
    measured_value: str | int | Decimal | None = None
    required_value: str | int | Decimal | None = None

    @model_validator(mode="after")
    def explain_non_passing_results(self) -> MigrationReadinessGate:
        if (
            self.status
            in (
                MigrationReadinessStatus.FAIL,
                MigrationReadinessStatus.PENDING,
                MigrationReadinessStatus.DEFERRED,
            )
            and not self.reason_codes
        ):
            raise ValueError("non-passing migration gates require a reason code")
        if self.status is MigrationReadinessStatus.PASS and not self.evidence_ids:
            raise ValueError("passing migration gates require evidence ids")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("migration gate evidence ids must be unique")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("migration gate reason codes must be unique")
        return self


class MigrationReadinessSection(StrictModel):
    """An ordered group of gates whose status is computed from those gates."""

    id: Literal[
        "CURRENT_SCOPE_CONFORMANCE",
        "SUPERVISED_SHADOW_READINESS",
        "FULL_SOURCE_CONFORMANCE",
        "SHADOW_GRADUATION",
    ]
    gate_results: tuple[MigrationReadinessGate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def gate_ids_are_unique(self) -> MigrationReadinessSection:
        gate_ids = tuple(gate.id for gate in self.gate_results)
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("migration readiness gate ids must be unique within a section")
        deferred = tuple(
            gate for gate in self.gate_results if gate.status is MigrationReadinessStatus.DEFERRED
        )
        if self.id == "FULL_SOURCE_CONFORMANCE":
            if len(deferred) != len(self.gate_results):
                raise ValueError("every full-source conformance gate must remain deferred")
        elif deferred:
            raise ValueError("only full-source conformance gates may be deferred")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> MigrationReadinessStatus:
        statuses = tuple(gate.status for gate in self.gate_results)
        if MigrationReadinessStatus.FAIL in statuses:
            return MigrationReadinessStatus.FAIL
        if MigrationReadinessStatus.PENDING in statuses:
            return MigrationReadinessStatus.PENDING
        if all(status is MigrationReadinessStatus.DEFERRED for status in statuses):
            return MigrationReadinessStatus.DEFERRED
        return MigrationReadinessStatus.PASS


class MigrationReadiness(StrictModel):
    """Four separately reported R11 readiness decisions."""

    current_scope_conformance: MigrationReadinessSection
    supervised_shadow_readiness: MigrationReadinessSection
    full_source_conformance: MigrationReadinessSection
    shadow_graduation: MigrationReadinessSection

    @model_validator(mode="after")
    def sections_have_fixed_identity(self) -> MigrationReadiness:
        expected = (
            (self.current_scope_conformance, "CURRENT_SCOPE_CONFORMANCE"),
            (self.supervised_shadow_readiness, "SUPERVISED_SHADOW_READINESS"),
            (self.full_source_conformance, "FULL_SOURCE_CONFORMANCE"),
            (self.shadow_graduation, "SHADOW_GRADUATION"),
        )
        if any(section.id != section_id for section, section_id in expected):
            raise ValueError("migration readiness sections must use the fixed R11 identities")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def schedule_activation(self) -> Literal["PAUSED"]:
        """R11 readiness never activates a schedule."""
        return "PAUSED"


_SUPPORTED_PYTHON_VERSIONS = ("3.12", "3.13", "3.14")
_REQUIRED_RELEASE_CHECKS: tuple[ReleaseCheckId, ...] = tuple(ReleaseCheckId)


def build_migration_readiness(
    scenario_outcomes: Sequence[EvaluationOutcome],
    evidence: ShadowEvaluationEvidence,
    scorecard: ShadowScorecard,
) -> MigrationReadiness:
    """Derive R11 readiness from manifest outcomes and verified release evidence."""
    scenarios = load_evaluation_scenarios()
    outcomes = tuple(
        EvaluationOutcome.model_validate(outcome, strict=True) for outcome in scenario_outcomes
    )
    _require_scorecard_evidence_binding(evidence, scorecard)

    outcome_by_id, outcome_coverage = _index_scenario_outcomes(outcomes, scenarios)
    current_scope_gate = _scenario_conformance_gate(outcome_by_id, scenarios)
    assertion_gate = _scenario_assertions_gate(outcome_by_id, scenarios)
    failure_matrix_gate = _failure_matrix_gate(scenarios, evidence.release_check_receipts)
    release_gate = _release_checks_gate(evidence)
    coverage_gate = _coverage_gate(evidence.release_check_receipts)

    current_scope = MigrationReadinessSection(
        id="CURRENT_SCOPE_CONFORMANCE",
        gate_results=(
            outcome_coverage,
            current_scope_gate,
            assertion_gate,
            failure_matrix_gate,
            release_gate,
            coverage_gate,
        ),
    )
    supervised = _supervised_shadow_section(current_scope, evidence)
    full_source = MigrationReadinessSection(
        id="FULL_SOURCE_CONFORMANCE",
        gate_results=(
            MigrationReadinessGate(
                id="SOURCE_SCOPE_DESIGN",
                status=MigrationReadinessStatus.DEFERRED,
                reason_codes=("SEPARATELY_APPROVED_SOURCE_SCOPE_REQUIRED",),
            ),
        ),
    )
    graduation = _shadow_graduation_section(scorecard, evidence)
    return MigrationReadiness(
        current_scope_conformance=current_scope,
        supervised_shadow_readiness=supervised,
        full_source_conformance=full_source,
        shadow_graduation=graduation,
    )


def _require_scorecard_evidence_binding(
    evidence: ShadowEvaluationEvidence,
    scorecard: ShadowScorecard,
) -> None:
    if scorecard.period_start != evidence.period.start_date or scorecard.period_end != (
        evidence.period.end_date
    ):
        raise ValueError("scorecard period must exactly match verified shadow evidence")
    if scorecard.origin is not evidence.origin:
        raise ValueError("scorecard origin must exactly match verified shadow evidence")
    if scorecard.evidence_hashes != evidence.evidence_hashes:
        raise ValueError("scorecard hashes must exactly match verified shadow evidence")
    if scorecard.source_commit_sha256 != evidence.source_commit_sha256 or (
        scorecard.source_tree_sha256 != evidence.source_tree_sha256
    ):
        raise ValueError("scorecard source identity must exactly match verified shadow evidence")


def _index_scenario_outcomes(
    outcomes: tuple[EvaluationOutcome, ...],
    scenarios: tuple[EvaluationScenario, ...],
) -> tuple[dict[ScenarioId, EvaluationOutcome], MigrationReadinessGate]:
    identifiers = tuple(outcome.scenario_id for outcome in outcomes)
    duplicates = len(identifiers) != len(set(identifiers))
    manifest_ids = tuple(scenario.id for scenario in scenarios)
    supplied_in_manifest_order = tuple(
        scenario_id for scenario_id in manifest_ids if scenario_id in set(identifiers)
    )
    order_is_valid = identifiers == supplied_in_manifest_order
    observed: dict[ScenarioId, EvaluationOutcome] = {}
    for outcome in outcomes:
        observed.setdefault(outcome.scenario_id, outcome)

    if duplicates or not order_is_valid:
        gate = _gate(
            "SCENARIO_OUTCOMES",
            MigrationReadinessStatus.FAIL,
            evidence_ids=tuple(item.scenario_id.value for item in outcomes),
            reason_codes=("SCENARIO_OUTCOME_ORDER_OR_IDENTITY_INVALID",),
        )
    elif identifiers != manifest_ids:
        gate = _gate(
            "SCENARIO_OUTCOMES",
            MigrationReadinessStatus.PENDING,
            evidence_ids=tuple(item.scenario_id.value for item in outcomes),
            reason_codes=("SCENARIO_OUTCOMES_INCOMPLETE",),
            measured_value=len(identifiers),
            required_value=len(manifest_ids),
        )
    else:
        gate = _gate(
            "SCENARIO_OUTCOMES",
            MigrationReadinessStatus.PASS,
            evidence_ids=tuple(item.scenario_id.value for item in outcomes),
            measured_value=len(identifiers),
            required_value=len(manifest_ids),
        )
    return observed, gate


def _scenario_conformance_gate(
    outcomes: dict[ScenarioId, EvaluationOutcome],
    scenarios: tuple[EvaluationScenario, ...],
) -> MigrationReadinessGate:
    evidence_ids: list[str] = []
    reasons: list[str] = []
    missing = False
    failed = False
    for scenario in scenarios:
        outcome = outcomes.get(scenario.id)
        if outcome is None:
            missing = True
            continue
        evidence_ids.append(scenario.id.value)
        case_results = outcome.current_scope_outcomes
        expectations = (
            scenario.current_scope_expectation.primary,
            *scenario.current_scope_expectation.subcases,
        )
        if len(case_results) > len(expectations):
            failed = True
            reasons.append("UNEXPECTED_CURRENT_SCOPE_CASE")
        elif len(case_results) < len(expectations):
            missing = True
            reasons.append("CURRENT_SCOPE_CASE_EVIDENCE_MISSING")
        for observed, expected in zip(case_results, expectations, strict=False):
            if observed.current_scope.case_id != expected.case_id:
                failed = True
                reasons.append("CURRENT_SCOPE_CASE_ORDER_MISMATCH")
            if observed.current_scope != expected:
                failed = True
                reasons.append("CURRENT_SCOPE_EXPECTATION_MISMATCH")
            if expected.execution_status.value == "PUBLISHED":
                expected_revision = expected.requested_revision or 1
                if observed.run_id is None:
                    missing = True
                    reasons.append("PUBLISHED_RUN_ID_MISSING")
                if observed.run_revision is None:
                    missing = True
                    reasons.append("PUBLISHED_RUN_REVISION_MISSING")
                elif observed.run_revision != expected_revision:
                    failed = True
                    reasons.append("PUBLISHED_RUN_REVISION_MISMATCH")
                required_artifacts = {"published_bundle", "report_markdown", "replayed_bundle"}
                if not required_artifacts <= set(observed.artifact_hashes):
                    missing = True
                    reasons.append("PUBLISHED_RUN_EVIDENCE_INCOMPLETE")
                for replay_result in (
                    observed.replay_json_matches,
                    observed.replay_markdown_matches,
                ):
                    if replay_result is False:
                        failed = True
                        reasons.append("PUBLISHED_RUN_REPLAY_MISMATCH")
                    elif replay_result is None:
                        missing = True
                        reasons.append("PUBLISHED_RUN_REPLAY_EVIDENCE_MISSING")
            elif observed.run_id is not None or observed.run_revision is not None:
                failed = True
                reasons.append("UNEXPECTED_PUBLICATION_IDENTITY")
    if failed:
        status = MigrationReadinessStatus.FAIL
    elif missing:
        status = MigrationReadinessStatus.PENDING
    else:
        status = MigrationReadinessStatus.PASS
    if status is MigrationReadinessStatus.PENDING:
        reasons.append("CURRENT_SCOPE_EVIDENCE_INCOMPLETE")
    return _gate(
        "CURRENT_SCOPE_EXPECTATIONS",
        status,
        evidence_ids=tuple(evidence_ids),
        reason_codes=_unique(reasons),
        measured_value="COMPARED_TO_FIXED_S01_S25_MANIFEST",
        required_value="EXACT_CURRENT_SCOPE_EXPECTATIONS",
    )


def _scenario_assertions_gate(
    outcomes: dict[ScenarioId, EvaluationOutcome],
    scenarios: tuple[EvaluationScenario, ...],
) -> MigrationReadinessGate:
    evidence_ids: list[str] = []
    reasons: list[str] = []
    pending = False
    failed = False
    for scenario in scenarios:
        outcome = outcomes.get(scenario.id)
        if outcome is None:
            pending = True
            reasons.append("SCENARIO_OUTCOME_MISSING")
            continue
        evidence_ids.append(scenario.id.value)
        declared_assertions = set(scenario.assertions)
        claimed_assertions = (
            set(outcome.assertions_passed)
            | set(outcome.assertions_failed)
            | set(outcome.assertions_pending)
        )
        if claimed_assertions - declared_assertions:
            failed = True
            reasons.append("UNDECLARED_SCENARIO_ASSERTION_RESULT")
        for assertion in scenario.assertions:
            result = _recompute_assertion(scenario, outcome, assertion)
            claimed = _claimed_assertion_status(outcome, assertion)
            if claimed is not result:
                failed = True
                reasons.append("SCENARIO_ASSERTION_RESULT_MISMATCH")
            if result is MigrationReadinessStatus.FAIL:
                failed = True
                reasons.append(f"ASSERTION_FAILED_{assertion.value}")
            elif result is MigrationReadinessStatus.PENDING:
                pending = True
                reasons.append(f"ASSERTION_EVIDENCE_MISSING_{assertion.value}")
        if scenario.domain_assertions:
            domain_status = _recheck_domain_assertions(scenario.domain_assertions, outcome)
            if domain_status is MigrationReadinessStatus.FAIL:
                failed = True
                reasons.append("DOMAIN_ASSERTION_FAILED_OR_MISMATCHED")
            elif domain_status is MigrationReadinessStatus.PENDING:
                pending = True
                reasons.append("DOMAIN_ASSERTION_EVIDENCE_MISSING")
    status = (
        MigrationReadinessStatus.FAIL
        if failed
        else MigrationReadinessStatus.PENDING
        if pending
        else MigrationReadinessStatus.PASS
    )
    return _gate(
        "SCENARIO_ASSERTIONS",
        status,
        evidence_ids=tuple(evidence_ids),
        reason_codes=_unique(reasons),
        measured_value="RECOMPUTED_FROM_TYPED_OBSERVATIONS",
        required_value="ALL_DECLARED_ASSERTIONS_PASS",
    )


def _recompute_assertion(
    scenario: EvaluationScenario,
    outcome: EvaluationOutcome,
    assertion: ScenarioAssertionId,
) -> MigrationReadinessStatus:
    observations = outcome.current_scope_outcomes
    if assertion is ScenarioAssertionId.CURRENT_SCOPE_MATCHES:
        expected = (
            scenario.current_scope_expectation.primary,
            *scenario.current_scope_expectation.subcases,
        )
        if len(observations) < len(expected):
            return MigrationReadinessStatus.PENDING
        if len(observations) > len(expected):
            return MigrationReadinessStatus.FAIL
        return _pass_or_fail(tuple(item.current_scope for item in observations) == expected)
    if assertion is ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS:
        if not scenario.domain_assertions:
            return MigrationReadinessStatus.PENDING
        return _recheck_domain_assertions(scenario.domain_assertions, outcome)
    if assertion is ScenarioAssertionId.ALL_WATCHLIST_EXCLUSIONS_VISIBLE:
        return _observation_booleans(observations, "watchlist_exclusions_visible")
    if assertion is ScenarioAssertionId.SOURCE_LIMITATIONS_ADJACENT:
        return _observation_booleans(observations, "source_limitations_adjacent")
    if assertion is ScenarioAssertionId.PACKET_HASH_STABLE:
        return _pass_or_fail(
            all(
                bool(item.synthesis_packet_hashes)
                and len(item.synthesis_packet_hashes) <= 3
                and len(set(item.synthesis_packet_hashes)) == 1
                and item.synthesis_packet_hashes[0] == item.artifact_hashes.get("research_packet")
                for item in observations
            )
        )
    if assertion is ScenarioAssertionId.REVISION_BYTES_IMMUTABLE:
        return _pass_or_fail(
            len(observations) == 2
            and observations[0].run_revision == 1
            and observations[1].run_revision == 2
            and all(
                item.replay_json_matches is True and item.replay_markdown_matches is True
                for item in observations
            )
            and observations[1].prior_revision_bytes_immutable is True
        )
    if assertion is ScenarioAssertionId.INVALID_DRAFT_NEVER_PUBLISHED:
        return _observation_booleans(observations, "invalid_draft_never_published")
    if assertion is ScenarioAssertionId.REPAIR_LIMIT_ENFORCED:
        return _pass_or_fail(
            all(
                item.validation_attempt_count <= 3
                and item.repair_count <= 2
                and len(item.synthesis_packet_hashes) <= 3
                and item.validation_attempt_count <= len(item.synthesis_packet_hashes)
                and (
                    item.current_scope.reduced_report_reason is None
                    or item.current_scope.reduced_report_reason.value
                    != "VALIDATION_REPAIR_EXHAUSTED"
                    or item.validation_attempt_count == 3
                )
                for item in observations
            )
        )
    if assertion is ScenarioAssertionId.UNTRUSTED_EXCERPT_INERT:
        return _pass_or_fail(
            all(
                bool(item.synthesis_packet_hashes)
                and len(set(item.synthesis_packet_hashes)) == 1
                and item.synthesis_packet_hashes[0] == item.artifact_hashes.get("research_packet")
                and item.provider_calls_before_synthesis == item.provider_call_count
                and item.invalid_draft_never_published is True
                for item in observations
            )
        )
    if assertion is ScenarioAssertionId.NO_PROVIDER_READ_AFTER_WINDOW:
        return _pass_or_fail(
            all(
                item.current_scope.delivery_status.value == "MISSED_WINDOW"
                and item.provider_call_count == 0
                for item in observations
            )
        )
    if assertion is ScenarioAssertionId.MISSED_RUN_RECORD_DURABLE:
        skipped = any(
            item.current_scope.execution_status.value == "SKIPPED" for item in observations
        )
        matches = skipped and all(
            item.missed_run_record_durable
            == (item.current_scope.execution_status.value == "SKIPPED")
            for item in observations
        )
        return _pass_or_fail(matches)
    raise AssertionError(f"unhandled scenario assertion {assertion}")


def _recheck_domain_assertions(
    assertions: tuple[DomainAssertion, ...],
    outcome: EvaluationOutcome,
) -> MigrationReadinessStatus:
    observed = outcome.domain_assertion_outcomes
    if len(observed) < len(assertions):
        return MigrationReadinessStatus.PENDING
    if len(observed) > len(assertions):
        return MigrationReadinessStatus.FAIL
    pending = False
    for expected, actual in zip(assertions, observed, strict=True):
        expected_fields = tuple(
            name
            for name in type(expected.expected_fields).model_fields
            if name != "schema_version" and name in expected.expected_fields.model_fields_set
        )
        actual_fields = set(actual.matched_fields) | set(actual.mismatched_fields)
        if actual.kind is not expected.kind or actual.fixture_id is not expected.fixture_id:
            return MigrationReadinessStatus.FAIL
        if set(actual.mismatched_fields) - set(expected_fields):
            return MigrationReadinessStatus.FAIL
        if actual.status == "FAIL" or actual.mismatched_fields:
            return MigrationReadinessStatus.FAIL
        if actual_fields != set(expected_fields):
            pending = True
        elif actual.status != "PASS" or tuple(actual.matched_fields) != expected_fields:
            return MigrationReadinessStatus.FAIL
    if pending:
        return MigrationReadinessStatus.PENDING
    return MigrationReadinessStatus.PASS


def _claimed_assertion_status(
    outcome: EvaluationOutcome,
    assertion: ScenarioAssertionId,
) -> MigrationReadinessStatus | None:
    if assertion in outcome.assertions_passed:
        return MigrationReadinessStatus.PASS
    if assertion in outcome.assertions_failed:
        return MigrationReadinessStatus.FAIL
    if assertion in outcome.assertions_pending:
        return MigrationReadinessStatus.PENDING
    return None


def _observation_booleans(
    observations: tuple[object, ...], field_name: str
) -> MigrationReadinessStatus:
    values = tuple(getattr(item, field_name) for item in observations)
    if any(value is False for value in values):
        return MigrationReadinessStatus.FAIL
    if not observations or any(value is None for value in values):
        return MigrationReadinessStatus.PENDING
    return MigrationReadinessStatus.PASS


def _failure_matrix_gate(
    scenarios: tuple[EvaluationScenario, ...],
    receipts: tuple[ReleaseCheckReceipt, ...],
) -> MigrationReadinessGate:
    declared = {failure for scenario in scenarios for failure in scenario.injected_failures}
    expected = set(FailureInjectionId)
    receipt_gate = _release_check_gate(
        receipts,
        (ReleaseCheckId.SCENARIO_AND_FAILURE_MATRIX,),
        gate_id="SCENARIO_AND_FAILURE_MATRIX",
    )
    if declared != expected:
        return _gate(
            "INJECTION_AND_FAILURE_MATRIX",
            MigrationReadinessStatus.FAIL,
            evidence_ids=tuple(sorted(item.value for item in declared)),
            reason_codes=("FAILURE_INJECTION_MANIFEST_INCOMPLETE",),
            measured_value=len(declared),
            required_value=len(expected),
        )
    if receipt_gate.status is MigrationReadinessStatus.FAIL:
        return _gate(
            "INJECTION_AND_FAILURE_MATRIX",
            MigrationReadinessStatus.FAIL,
            evidence_ids=receipt_gate.evidence_ids,
            reason_codes=receipt_gate.reason_codes,
            measured_value=len(declared),
            required_value=len(expected),
        )
    if receipt_gate.status is MigrationReadinessStatus.PENDING:
        return _gate(
            "INJECTION_AND_FAILURE_MATRIX",
            MigrationReadinessStatus.PENDING,
            evidence_ids=tuple(sorted(item.value for item in declared)) + receipt_gate.evidence_ids,
            reason_codes=receipt_gate.reason_codes,
            measured_value=len(declared),
            required_value=len(expected),
        )
    return _gate(
        "INJECTION_AND_FAILURE_MATRIX",
        MigrationReadinessStatus.PASS,
        evidence_ids=tuple(sorted(item.value for item in declared)) + receipt_gate.evidence_ids,
        measured_value=len(declared),
        required_value=len(expected),
    )


def _release_checks_gate(evidence: ShadowEvaluationEvidence) -> MigrationReadinessGate:
    return _release_check_gate(
        evidence.release_check_receipts,
        _REQUIRED_RELEASE_CHECKS,
        gate_id="EXACT_HEAD_RELEASE_CHECKS",
        source_commit_sha256=evidence.source_commit_sha256,
        source_tree_sha256=evidence.source_tree_sha256,
    )


def _release_check_gate(
    receipts: tuple[ReleaseCheckReceipt, ...],
    required_checks: tuple[ReleaseCheckId, ...],
    *,
    gate_id: str,
    source_commit_sha256: Sha256 | None = None,
    source_tree_sha256: Sha256 | None = None,
) -> MigrationReadinessGate:
    selected: list[ReleaseCheckReceipt] = []
    missing: list[str] = []
    failures: list[ReleaseCheckReceipt] = []
    for check_id in required_checks:
        for python_version in _SUPPORTED_PYTHON_VERSIONS:
            receipt = next(
                (
                    item
                    for item in receipts
                    if item.check_id is check_id and item.python_version == python_version
                ),
                None,
            )
            if receipt is None:
                missing.append(f"{check_id.value}_{python_version.replace('.', '_')}")
                continue
            selected.append(receipt)
            if (
                source_commit_sha256 is not None
                and receipt.source_commit_sha256 != source_commit_sha256
            ) or (
                source_tree_sha256 is not None and receipt.source_tree_sha256 != source_tree_sha256
            ):
                failures.append(receipt)
            elif receipt.conclusion is not ReleaseCheckConclusion.SUCCESS:
                failures.append(receipt)
    evidence_ids = tuple(item.id for item in selected)
    if failures:
        return _gate(
            gate_id,
            MigrationReadinessStatus.FAIL,
            evidence_ids=evidence_ids,
            reason_codes=("EXACT_HEAD_RELEASE_CHECK_FAILED_OR_MISMATCHED",),
            measured_value=len(failures),
            required_value=0,
        )
    if missing:
        return _gate(
            gate_id,
            MigrationReadinessStatus.PENDING,
            evidence_ids=evidence_ids,
            reason_codes=("RELEASE_CHECK_MATRIX_INCOMPLETE",),
            measured_value=len(selected),
            required_value=len(required_checks) * len(_SUPPORTED_PYTHON_VERSIONS),
        )
    return _gate(
        gate_id,
        MigrationReadinessStatus.PASS,
        evidence_ids=evidence_ids,
        measured_value=len(selected),
        required_value=len(required_checks) * len(_SUPPORTED_PYTHON_VERSIONS),
    )


def _coverage_gate(receipts: tuple[ReleaseCheckReceipt, ...]) -> MigrationReadinessGate:
    gate = _release_check_gate(
        receipts,
        (ReleaseCheckId.BRANCH_COVERAGE,),
        gate_id="BRANCH_COVERAGE",
    )
    if gate.status is MigrationReadinessStatus.PASS:
        return gate.model_copy(update={"required_value": ">=90% whole-package line coverage"})
    if gate.status is MigrationReadinessStatus.PENDING:
        return gate.model_copy(update={"reason_codes": ("COVERAGE_RECEIPT_MISSING",)})
    return gate


def _supervised_shadow_section(
    current_scope: MigrationReadinessSection,
    evidence: ShadowEvaluationEvidence,
) -> MigrationReadinessSection:
    if current_scope.status is MigrationReadinessStatus.FAIL:
        prerequisite = _gate(
            "CURRENT_SCOPE_PREREQUISITE",
            MigrationReadinessStatus.FAIL,
            evidence_ids=tuple(
                gate.id
                for gate in current_scope.gate_results
                if gate.status is MigrationReadinessStatus.FAIL
            ),
            reason_codes=("CURRENT_SCOPE_CONFORMANCE_FAILED",),
        )
    elif current_scope.status is MigrationReadinessStatus.PENDING:
        prerequisite = _gate(
            "CURRENT_SCOPE_PREREQUISITE",
            MigrationReadinessStatus.PENDING,
            evidence_ids=tuple(
                gate.id
                for gate in current_scope.gate_results
                if gate.status is MigrationReadinessStatus.PENDING
            ),
            reason_codes=("CURRENT_SCOPE_CONFORMANCE_PENDING",),
        )
    else:
        prerequisite = _gate(
            "CURRENT_SCOPE_PREREQUISITE",
            MigrationReadinessStatus.PASS,
            evidence_ids=("CURRENT_SCOPE_CONFORMANCE",),
        )

    expected_ids = tuple(OperatorReleaseCheckId)
    by_check: dict[OperatorReleaseCheckId, list[OperatorReleaseAttestation]] = {
        check_id: [] for check_id in expected_ids
    }
    current_skill_version = _current_premarket_skill_version()
    current_attestations = tuple(
        record
        for record in evidence.operator_attestations
        if evidence.source_commit_sha256 is not None
        and evidence.source_tree_sha256 is not None
        and record.source_commit_sha256 == evidence.source_commit_sha256
        and record.source_tree_sha256 == evidence.source_tree_sha256
        and record.package_version == __version__
        and current_skill_version is not None
        and record.skill_version == current_skill_version
    )
    for record in current_attestations:
        by_check[record.check_id].append(record)
    missing = [check_id for check_id, records in by_check.items() if not records]
    failed = [
        record for record in current_attestations if record.result is OperatorReleaseResult.FAIL
    ]
    if failed:
        attestation_gate = _gate(
            "OPERATOR_ATTESTATIONS",
            MigrationReadinessStatus.FAIL,
            evidence_ids=tuple(record.id for record in failed),
            reason_codes=("OPERATOR_ATTESTATION_FAILED",),
            measured_value=len(failed),
            required_value=0,
        )
    elif missing:
        attestation_gate = _gate(
            "OPERATOR_ATTESTATIONS",
            MigrationReadinessStatus.PENDING,
            evidence_ids=tuple(record.id for record in current_attestations),
            reason_codes=("OPERATOR_ATTESTATIONS_INCOMPLETE",),
            measured_value=len(expected_ids) - len(missing),
            required_value=len(expected_ids),
        )
    else:
        attestation_gate = _gate(
            "OPERATOR_ATTESTATIONS",
            MigrationReadinessStatus.PASS,
            evidence_ids=tuple(record.id for record in current_attestations),
            measured_value=len(expected_ids),
            required_value=len(expected_ids),
        )
    return MigrationReadinessSection(
        id="SUPERVISED_SHADOW_READINESS",
        gate_results=(prerequisite, attestation_gate),
    )


def _current_premarket_skill_version() -> str | None:
    source_root = Path(__file__).resolve().parents[3]
    skill_root = source_root / "skills" / "premarket-research"
    skill_path = skill_root / "SKILL.md"
    workflow_path = skill_root / "references" / "workflow-contract.yaml"
    if skill_root.exists() or skill_root.is_symlink():
        try:
            skill_bytes = _read_confined_skill_source(source_root, skill_path)
            workflow_bytes = _read_confined_skill_source(source_root, workflow_path)
            if skill_bytes is None or workflow_bytes is None:
                return None
            return compute_skill_version(
                {
                    "SKILL.md": skill_bytes,
                    "references/workflow-contract.yaml": workflow_bytes,
                }
            )
        except (OSError, ValueError):
            return None
    try:
        return load_installed_premarket_skill_version()
    except (ImportError, OSError, RuntimeError, ValueError):
        return None


def _read_confined_skill_source(source_root: Path, path: Path) -> bytes | None:
    try:
        relative = path.relative_to(source_root)
    except ValueError:
        return None
    current = source_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return None
    if not current.is_file() or not current.resolve().is_relative_to(source_root):
        return None
    return current.read_bytes()


def _shadow_graduation_section(
    scorecard: ShadowScorecard,
    evidence: ShadowEvaluationEvidence,
) -> MigrationReadinessSection:
    quality_gates = tuple(_from_scorecard_gate(gate) for gate in scorecard.gate_results)
    verified_day_count, verified_day_ids = _verified_personal_shadow_days(evidence)
    day_evidence_ids = _unique(evidence.evidence_hashes + verified_day_ids)
    if scorecard.valid_trading_days != verified_day_count:
        day_count_gate = _gate(
            "VERIFIED_VALID_DAY_COUNT",
            MigrationReadinessStatus.FAIL,
            evidence_ids=day_evidence_ids,
            reason_codes=("SCORECARD_VALID_DAY_COUNT_MISMATCH",),
            measured_value=scorecard.valid_trading_days,
            required_value=verified_day_count,
        )
    elif not day_evidence_ids:
        day_count_gate = _gate(
            "VERIFIED_VALID_DAY_COUNT",
            MigrationReadinessStatus.PENDING,
            reason_codes=("VERIFIED_PERSONAL_DAY_EVIDENCE_MISSING",),
            measured_value=scorecard.valid_trading_days,
            required_value=verified_day_count,
        )
    else:
        day_count_gate = _gate(
            "VERIFIED_VALID_DAY_COUNT",
            MigrationReadinessStatus.PASS,
            evidence_ids=day_evidence_ids,
            measured_value=verified_day_count,
            required_value=verified_day_count,
        )
    if scorecard.origin is not ShadowEvaluationOrigin.PERSONAL_SHADOW:
        day_gate = _gate(
            "PERSONAL_SHADOW_DAYS",
            MigrationReadinessStatus.PENDING,
            evidence_ids=day_evidence_ids,
            reason_codes=("PERSONAL_SHADOW_ORIGIN_REQUIRED",),
            measured_value=verified_day_count,
            required_value=20,
        )
    elif verified_day_count < 20:
        day_gate = _gate(
            "PERSONAL_SHADOW_DAYS",
            MigrationReadinessStatus.PENDING,
            evidence_ids=day_evidence_ids,
            reason_codes=("TWENTY_VALID_PERSONAL_SHADOW_DAYS_REQUIRED",),
            measured_value=verified_day_count,
            required_value=20,
        )
    else:
        day_gate = _gate(
            "PERSONAL_SHADOW_DAYS",
            MigrationReadinessStatus.PASS,
            evidence_ids=day_evidence_ids,
            measured_value=verified_day_count,
            required_value=20,
        )
    return MigrationReadinessSection(
        id="SHADOW_GRADUATION",
        gate_results=(*quality_gates, day_count_gate, day_gate),
    )


def _verified_personal_shadow_days(
    evidence: ShadowEvaluationEvidence,
) -> tuple[int, tuple[str, ...]]:
    if evidence.origin is not ShadowEvaluationOrigin.PERSONAL_SHADOW:
        return 0, ()

    superseded_ids = {
        item.corrects_observation_id
        for item in evidence.observations
        if item.corrects_observation_id is not None
    }
    availability_by_date: dict[date, set[ShadowDayAvailability]] = {}
    observation_ids_by_date: dict[date, list[str]] = {}
    for observation in evidence.observations:
        if observation.id in superseded_ids:
            continue
        availability_by_date.setdefault(observation.market_date, set()).add(
            observation.availability
        )
        observation_ids_by_date.setdefault(observation.market_date, []).append(observation.id)

    research_run_ids_by_date: dict[date, list[str]] = {}
    for verified_run in evidence.verified_runs:
        if verified_run.normal_provider is not True:
            continue
        bundle = verified_run.published_bundle
        artifact = verified_run.published_artifact
        if bundle is None or artifact is None:
            continue
        if (
            "brief_draft" not in bundle.bundle
            and bundle.bundle.get("brief_origin") != BriefOrigin.DETERMINISTIC_REDUCED.value
        ):
            continue
        research_run_ids_by_date.setdefault(bundle.run.market_date, []).append(
            verified_run.stored_run.run.run_id
        )

    valid_dates = tuple(
        market_date
        for market_date, availability in availability_by_date.items()
        if availability == {ShadowDayAvailability.ONLINE}
        and market_date in research_run_ids_by_date
    )
    evidence_ids = tuple(
        sorted(
            identifier
            for market_date in valid_dates
            for identifier in (
                *observation_ids_by_date[market_date],
                *research_run_ids_by_date[market_date],
            )
        )
    )
    return len(valid_dates), evidence_ids


def _from_scorecard_gate(gate: EvaluationGate) -> MigrationReadinessGate:
    return MigrationReadinessGate(
        id=f"SHADOW_{gate.id.value}",
        status=MigrationReadinessStatus(gate.status.value),
        evidence_ids=gate.evidence_ids,
        reason_codes=gate.reason_codes,
        measured_value=gate.measured_value,
        required_value=gate.required_value,
    )


def _gate(
    identifier: str,
    status: MigrationReadinessStatus,
    *,
    evidence_ids: tuple[str, ...] = (),
    reason_codes: tuple[str, ...] = (),
    measured_value: str | int | Decimal | None = None,
    required_value: str | int | Decimal | None = None,
) -> MigrationReadinessGate:
    return MigrationReadinessGate(
        id=identifier,
        status=status,
        evidence_ids=_unique(evidence_ids),
        reason_codes=_unique(reason_codes),
        measured_value=measured_value,
        required_value=required_value,
    )


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _pass_or_fail(value: bool) -> MigrationReadinessStatus:
    return MigrationReadinessStatus.PASS if value else MigrationReadinessStatus.FAIL


__all__ = [
    "MigrationReadiness",
    "MigrationReadinessGate",
    "MigrationReadinessSection",
    "MigrationReadinessStatus",
    "build_migration_readiness",
]
