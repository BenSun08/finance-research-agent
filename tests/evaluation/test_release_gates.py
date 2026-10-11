from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from finance_research_agent import __version__
from finance_research_agent.application.replay_service import ArtifactReplayResult
from finance_research_agent.application.skill_bundle import compute_skill_version
from finance_research_agent.domain.enums import BriefOrigin, DeliveryStatus
from finance_research_agent.domain.models import (
    PublishedArtifact,
    PublishedRunBundle,
    RunContext,
    StoredRun,
)
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.evaluation import release_gates as release_gates_module
from finance_research_agent.evaluation.models import (
    CurrentScopeServiceObservation,
    DomainAssertionKind,
    DomainAssertionOutcome,
    EvaluationOutcome,
    FailureInjectionId,
    ScenarioAssertionId,
)
from finance_research_agent.evaluation.release_gates import (
    MigrationReadiness,
    MigrationReadinessGate,
    MigrationReadinessSection,
    MigrationReadinessStatus,
    _claimed_assertion_status,
    _observation_booleans,
    _recheck_domain_assertions,
    _recompute_assertion,
    _release_check_gate,
    build_migration_readiness,
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
    ShadowDayObservation,
)
from finance_research_agent.evaluation.shadow_scorecard import (
    EvaluationGate,
    EvaluationGateStatus,
    ShadowEvaluationEvidence,
    ShadowEvaluationOrigin,
    ShadowGateId,
    ShadowPeriod,
    ShadowScorecard,
    VerifiedShadowRunEvidence,
    _create_shadow_evaluation_evidence,
    build_shadow_scorecard,
)

_COMMIT = "a" * 64
_TREE = "b" * 64
_STAMP = datetime(2026, 10, 11, 16, tzinfo=UTC)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CURRENT_SKILL_VERSION = compute_skill_version(
    {
        "SKILL.md": (_REPOSITORY_ROOT / "skills/premarket-research/SKILL.md").read_bytes(),
        "references/workflow-contract.yaml": (
            _REPOSITORY_ROOT / "skills/premarket-research/references/workflow-contract.yaml"
        ).read_bytes(),
    }
)


def _scorecard(
    evidence: ShadowEvaluationEvidence,
    status: EvaluationGateStatus = EvaluationGateStatus.PENDING,
    *,
    valid_trading_days: int = 0,
) -> ShadowScorecard:
    return ShadowScorecard(
        period_start=evidence.period.start_date,
        period_end=evidence.period.end_date,
        origin=evidence.origin,
        source_commit_sha256=evidence.source_commit_sha256,
        source_tree_sha256=evidence.source_tree_sha256,
        valid_trading_days=valid_trading_days,
        eligible_online_days=valid_trading_days,
        gate_results=tuple(
            EvaluationGate(
                id=gate_id,
                status=status,
                evidence_ids=("scorecard",) if status is EvaluationGateStatus.PASS else (),
                reason_codes=("EVIDENCE_PENDING",)
                if status is EvaluationGateStatus.PENDING
                else (),
            )
            for gate_id in ShadowGateId
        ),
        evidence_hashes=evidence.evidence_hashes,
    )


def _evidence(
    *,
    receipts: tuple[ReleaseCheckReceipt, ...] = (),
    attestations: tuple[OperatorReleaseAttestation, ...] = (),
    evidence_hashes: tuple[str, ...] = (),
    origin: ShadowEvaluationOrigin | None = None,
):
    return _create_shadow_evaluation_evidence(
        period=ShadowPeriod(
            start_date=datetime(2026, 10, 5).date(),
            end_date=datetime(2026, 10, 11).date(),
        ),
        origin=origin,
        verified_runs=(),
        feedback_records=(),
        observations=(),
        operator_attestations=attestations,
        release_check_receipts=receipts,
        source_commit_sha256=_COMMIT if receipts else None,
        source_tree_sha256=_TREE if receipts else None,
        evidence_hashes=evidence_hashes,
    )


def _personal_shadow_evidence_with_valid_days(
    day_count: int,
    base_run: RunContext,
    *,
    real_provider_day_count: int | None = None,
) -> ShadowEvaluationEvidence:
    market_dates: list[date] = []
    current_date = date(2026, 9, 1)
    while len(market_dates) < day_count:
        if current_date.weekday() < 5:
            market_dates.append(current_date)
        current_date = date.fromordinal(current_date.toordinal() + 1)

    observations: list[ShadowDayObservation] = []
    verified_runs: list[VerifiedShadowRunEvidence] = []
    for day_index, market_date in enumerate(market_dates):
        run_id = f"premarket-{market_date.isoformat()}-r1"
        run = base_run.model_copy(
            update={"run_id": run_id, "market_date": market_date, "revision": 1}
        )
        bundle = PublishedRunBundle(
            run=run,
            bundle=FrozenMap({"brief_origin": BriefOrigin.DETERMINISTIC_REDUCED.value}),
            report_markdown="verified reduced research publication",
        )
        artifact = PublishedArtifact(
            run_id=run_id,
            bundle_sha256="c" * 64,
            markdown_sha256="d" * 64,
            published_at=datetime(
                market_date.year,
                market_date.month,
                market_date.day,
                16,
                tzinfo=UTC,
            ),
        )
        replay = ArtifactReplayResult(
            run_id=run_id,
            bundle=bundle,
            report_markdown=bundle.report_markdown,
            json_matches=True,
            markdown_matches=True,
            stored_json_sha256="c" * 64,
            replayed_json_sha256="c" * 64,
            stored_markdown_sha256="d" * 64,
            replayed_markdown_sha256="d" * 64,
            component_version_mismatches=(),
        )
        verified_runs.append(
            VerifiedShadowRunEvidence(
                stored_run=StoredRun(run=run, published=True),
                published_bundle=bundle,
                published_artifact=artifact,
                replay_result=replay,
                normal_provider=(
                    real_provider_day_count is None or day_index < real_provider_day_count
                ),
            )
        )
        observations.append(
            ShadowDayObservation(
                id=f"observation-{market_date.isoformat()}",
                market_date=market_date,
                recorded_at=datetime(
                    market_date.year,
                    market_date.month,
                    market_date.day,
                    16,
                    tzinfo=UTC,
                ),
                session_id=f"session-{market_date.isoformat()}",
                availability=ShadowDayAvailability.ONLINE,
                observed_run_ids=(run_id,),
            )
        )

    return _create_shadow_evaluation_evidence(
        period=ShadowPeriod(start_date=market_dates[0], end_date=market_dates[-1]),
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        verified_runs=tuple(verified_runs),
        feedback_records=(),
        observations=tuple(observations),
        operator_attestations=(),
        release_check_receipts=(),
        source_commit_sha256=None,
        source_tree_sha256=None,
        evidence_hashes=("e" * 64,),
    )


def _release_receipts(
    *,
    omit: tuple[ReleaseCheckId, str] | None = None,
    conclusion: ReleaseCheckConclusion = ReleaseCheckConclusion.SUCCESS,
) -> tuple[ReleaseCheckReceipt, ...]:
    records = []
    for check_id in ReleaseCheckId:
        for python_version in ("3.12", "3.13", "3.14"):
            if omit == (check_id, python_version):
                continue
            records.append(
                ReleaseCheckReceipt(
                    id=f"{check_id.value.lower()}-{python_version}",
                    check_id=check_id,
                    source_commit_sha256=_COMMIT,
                    source_tree_sha256=_TREE,
                    repository_id="finance-research-agent",
                    workflow_id="ci",
                    actions_run_id="12345",
                    actions_job_id=f"verify-{python_version}",
                    actions_step_id=f"{check_id.value.lower()}-step",
                    python_version=python_version,
                    completed_at=_STAMP,
                    conclusion=conclusion,
                    response_sha256="c" * 64,
                )
            )
    return tuple(records)


def _attestations(
    result: OperatorReleaseResult = OperatorReleaseResult.PASS,
    *,
    source_commit_sha256: str = _COMMIT,
    source_tree_sha256: str = _TREE,
    package_version: str = __version__,
    skill_version: str = _CURRENT_SKILL_VERSION,
    id_prefix: str = "",
) -> tuple[OperatorReleaseAttestation, ...]:
    return tuple(
        OperatorReleaseAttestation(
            id=f"{id_prefix}attestation-{check_id.value.lower()}",
            check_id=check_id,
            result=result,
            operator_id="operator-1",
            recorded_at=_STAMP,
            package_version=package_version,
            skill_version=skill_version,
            source_commit_sha256=source_commit_sha256,
            source_tree_sha256=source_tree_sha256,
            evidence_sha256="d" * 64,
        )
        for check_id in OperatorReleaseCheckId
    )


def _passing_outcomes() -> tuple[EvaluationOutcome, ...]:
    outcomes: list[EvaluationOutcome] = []
    for scenario in load_evaluation_scenarios():
        required_assertions = scenario.assertions
        passed_assertions = tuple(
            assertion
            for assertion in required_assertions
            if not (
                assertion is ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS
                and not scenario.domain_assertions
            )
        )
        pending_assertions = tuple(
            assertion
            for assertion in required_assertions
            if assertion is ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS
            and not scenario.domain_assertions
        )
        case_results = []
        for expected in (
            scenario.current_scope_expectation.primary,
            *scenario.current_scope_expectation.subcases,
        ):
            assertions = set(required_assertions)
            hashes: dict[str, str] = {}
            packet_hashes: tuple[str, ...] = ()
            if assertions & {
                ScenarioAssertionId.PACKET_HASH_STABLE,
                ScenarioAssertionId.UNTRUSTED_EXCERPT_INERT,
                ScenarioAssertionId.REPAIR_LIMIT_ENFORCED,
            }:
                hashes["research_packet"] = "e" * 64
                packet_hashes = ("e" * 64,) * 3
            if expected.execution_status.value == "PUBLISHED":
                hashes["published_bundle"] = "f" * 64
                hashes["report_markdown"] = "1" * 64
                hashes["replayed_bundle"] = "2" * 64
            observation = CurrentScopeServiceObservation(
                scenario_id=scenario.id,
                run_id=(
                    f"run-{scenario.id.value}-{expected.case_id}"
                    if expected.execution_status.value == "PUBLISHED"
                    else None
                ),
                run_revision=(expected.requested_revision or 1)
                if expected.execution_status.value == "PUBLISHED"
                else None,
                current_scope=expected,
                artifact_hashes=FrozenMap(hashes),
                replay_json_matches=True,
                replay_markdown_matches=True,
                source_limitations_adjacent=True,
                watchlist_exclusions_visible=True,
                provider_call_count=0,
                missed_run_record_durable=expected.execution_status.value == "SKIPPED",
                synthesis_packet_hashes=packet_hashes,
                validation_attempt_count=3
                if ScenarioAssertionId.REPAIR_LIMIT_ENFORCED in assertions
                else 0,
                repair_count=2 if ScenarioAssertionId.REPAIR_LIMIT_ENFORCED in assertions else 0,
                invalid_draft_never_published=True
                if assertions
                & {
                    ScenarioAssertionId.INVALID_DRAFT_NEVER_PUBLISHED,
                    ScenarioAssertionId.UNTRUSTED_EXCERPT_INERT,
                }
                else None,
                provider_calls_before_synthesis=0
                if ScenarioAssertionId.UNTRUSTED_EXCERPT_INERT in assertions
                else None,
                prior_revision_bytes_immutable=True
                if expected.requested_revision == 2
                and ScenarioAssertionId.REVISION_BYTES_IMMUTABLE in assertions
                else None,
            )
            case_results.append(observation)
        assertion_results = tuple(
            DomainAssertionOutcome(
                kind=assertion.kind,
                fixture_id=assertion.fixture_id,
                status="PASS",
                matched_fields=tuple(
                    field_name
                    for field_name in type(assertion.expected_fields).model_fields
                    if field_name != "schema_version"
                    and field_name in assertion.expected_fields.model_fields_set
                ),
                mismatched_fields=(),
            )
            for assertion in scenario.domain_assertions
        )
        artifact_hashes = {
            f"{item.current_scope.case_id}.{name}": digest
            for item in case_results
            for name, digest in item.artifact_hashes.items()
        }
        outcomes.append(
            EvaluationOutcome(
                scenario_id=scenario.id,
                current_scope_outcomes=tuple(case_results),
                domain_assertion_outcomes=assertion_results,
                artifact_hashes=FrozenMap(artifact_hashes),
                assertions_passed=passed_assertions,
                assertions_failed=(),
                assertions_pending=pending_assertions,
            )
        )
    return tuple(outcomes)


def test_migration_readiness_keeps_missing_receipts_pending_and_source_scope_deferred() -> None:
    evidence = _evidence()
    scorecard = _scorecard(evidence)

    result = build_migration_readiness((), evidence, scorecard)

    assert result.current_scope_conformance.status is MigrationReadinessStatus.FAIL
    assert result.supervised_shadow_readiness.status is MigrationReadinessStatus.FAIL
    supervised_gates = {gate.id: gate for gate in result.supervised_shadow_readiness.gate_results}
    assert supervised_gates["OPERATOR_ATTESTATIONS"].status is MigrationReadinessStatus.PENDING
    assert result.full_source_conformance.status is MigrationReadinessStatus.DEFERRED
    assert result.shadow_graduation.status is MigrationReadinessStatus.PENDING
    assert result.schedule_activation == "PAUSED"
    gates = {gate.id: gate for gate in result.current_scope_conformance.gate_results}
    assert gates["SCENARIO_OUTCOMES"].status is MigrationReadinessStatus.PENDING
    assert gates["EXACT_HEAD_RELEASE_CHECKS"].status is MigrationReadinessStatus.PENDING
    assert gates["INJECTION_AND_FAILURE_MATRIX"].status is MigrationReadinessStatus.FAIL


def test_migration_readiness_rechecks_scenario_outputs_and_release_matrix(monkeypatch) -> None:
    _patch_complete_failure_manifest(monkeypatch)
    receipts = _release_receipts()
    evidence = _evidence(receipts=receipts)
    outcomes = _passing_outcomes()
    scorecard = _scorecard(evidence)

    result = build_migration_readiness(outcomes, evidence, scorecard)

    assert result.current_scope_conformance.status is MigrationReadinessStatus.PASS
    assert result.supervised_shadow_readiness.status is MigrationReadinessStatus.PENDING
    scenario_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "SCENARIO_OUTCOMES"
    )
    assert scenario_gate.status is MigrationReadinessStatus.PASS


def test_migration_readiness_fails_a_mismatched_observation_even_if_claimed_passed(
    monkeypatch,
) -> None:
    _patch_complete_failure_manifest(monkeypatch)
    outcomes = list(_passing_outcomes())
    first = outcomes[0]
    first_case = first.current_scope_outcomes[0]
    outcomes[0] = first.model_copy(
        update={
            "current_scope_outcomes": (
                first_case.model_copy(
                    update={
                        "current_scope": first_case.current_scope.model_copy(
                            update={"delivery_status": DeliveryStatus.MISSED_WINDOW}
                        )
                    }
                ),
                *first.current_scope_outcomes[1:],
            )
        }
    )
    evidence = _evidence(receipts=_release_receipts())
    scorecard = _scorecard(evidence)

    result = build_migration_readiness(tuple(outcomes), evidence, scorecard)

    assert result.current_scope_conformance.status is MigrationReadinessStatus.FAIL
    current_scope_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "CURRENT_SCOPE_EXPECTATIONS"
    )
    assert current_scope_gate.status is MigrationReadinessStatus.FAIL


def test_migration_readiness_keeps_missing_scenarios_pending(monkeypatch) -> None:
    _patch_complete_failure_manifest(monkeypatch)
    evidence = _evidence(receipts=_release_receipts())
    scorecard = _scorecard(evidence)

    result = build_migration_readiness(_passing_outcomes()[:-1], evidence, scorecard)

    assert result.current_scope_conformance.status is MigrationReadinessStatus.PENDING
    outcome_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "SCENARIO_OUTCOMES"
    )
    assert outcome_gate.status is MigrationReadinessStatus.PENDING


def test_migration_readiness_fails_exact_head_ci_failure() -> None:
    receipts = _release_receipts(conclusion=ReleaseCheckConclusion.FAILURE)
    evidence = _evidence(receipts=receipts)
    scorecard = _scorecard(evidence)

    result = build_migration_readiness(_passing_outcomes(), evidence, scorecard)

    assert result.current_scope_conformance.status is MigrationReadinessStatus.FAIL
    release_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "EXACT_HEAD_RELEASE_CHECKS"
    )
    assert release_gate.status is MigrationReadinessStatus.FAIL


def test_migration_readiness_requires_every_release_matrix_instance() -> None:
    omitted = (ReleaseCheckId.BRANCH_COVERAGE, "3.14")
    evidence = _evidence(receipts=_release_receipts(omit=omitted))
    scorecard = _scorecard(evidence)

    result = build_migration_readiness(_passing_outcomes(), evidence, scorecard)

    release_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "EXACT_HEAD_RELEASE_CHECKS"
    )
    assert release_gate.status is MigrationReadinessStatus.PENDING
    assert "RELEASE_CHECK_MATRIX_INCOMPLETE" in release_gate.reason_codes


@pytest.mark.parametrize(
    ("omit", "conclusion", "expected_status"),
    (
        (
            (ReleaseCheckId.SCENARIO_AND_FAILURE_MATRIX, "3.12"),
            ReleaseCheckConclusion.SUCCESS,
            MigrationReadinessStatus.PENDING,
        ),
        (
            None,
            ReleaseCheckConclusion.FAILURE,
            MigrationReadinessStatus.FAIL,
        ),
    ),
)
def test_failure_matrix_gate_uses_exact_ci_receipts(
    monkeypatch,
    omit,
    conclusion,
    expected_status,
) -> None:
    _patch_complete_failure_manifest(monkeypatch)
    evidence = _evidence(receipts=_release_receipts(omit=omit, conclusion=conclusion))

    result = build_migration_readiness(_passing_outcomes(), evidence, _scorecard(evidence))

    matrix_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "INJECTION_AND_FAILURE_MATRIX"
    )
    assert matrix_gate.status is expected_status


def test_current_scope_conformance_detects_missing_or_misordered_subcase() -> None:
    outcomes = list(_passing_outcomes())
    s08_index = next(
        index for index, outcome in enumerate(outcomes) if outcome.scenario_id.value == "S08"
    )
    s08 = outcomes[s08_index]
    case_results = s08.current_scope_outcomes
    scenarios = load_evaluation_scenarios()
    expected_scenario = next(item for item in scenarios if item.id.value == "S08")
    assert len(expected_scenario.current_scope_expectation.subcases) == 1
    evidence = _evidence()

    missing_cases = (case_results[0],)
    missing_hashes = {
        f"{item.current_scope.case_id}.{name}": digest
        for item in missing_cases
        for name, digest in item.artifact_hashes.items()
    }
    missing_outcome = s08.model_copy(
        update={
            "current_scope_outcomes": missing_cases,
            "artifact_hashes": FrozenMap(missing_hashes),
        }
    )
    outcomes[s08_index] = missing_outcome
    missing_result = build_migration_readiness(tuple(outcomes), evidence, _scorecard(evidence))
    missing_gate = next(
        gate
        for gate in missing_result.current_scope_conformance.gate_results
        if gate.id == "CURRENT_SCOPE_EXPECTATIONS"
    )
    assert missing_gate.status is MigrationReadinessStatus.PENDING
    assert "CURRENT_SCOPE_CASE_EVIDENCE_MISSING" in missing_gate.reason_codes

    outcomes[s08_index] = s08.model_copy(
        update={"current_scope_outcomes": tuple(reversed(case_results))}
    )
    reordered_result = build_migration_readiness(tuple(outcomes), evidence, _scorecard(evidence))
    reordered_gate = next(
        gate
        for gate in reordered_result.current_scope_conformance.gate_results
        if gate.id == "CURRENT_SCOPE_EXPECTATIONS"
    )
    assert reordered_gate.status is MigrationReadinessStatus.FAIL
    assert "CURRENT_SCOPE_CASE_ORDER_MISMATCH" in reordered_gate.reason_codes


def test_current_scope_conformance_rejects_an_unexpected_extra_case() -> None:
    outcomes = list(_passing_outcomes())
    first = outcomes[0]
    observation = first.current_scope_outcomes[0]
    extra = observation.model_copy(
        update={
            "current_scope": observation.current_scope.model_copy(
                update={"case_id": "unexpected-case"}
            ),
            "run_id": None,
            "run_revision": None,
            "artifact_hashes": FrozenMap({}),
        }
    )
    extra_cases = (*first.current_scope_outcomes, extra)
    outcomes[0] = first.model_copy(update={"current_scope_outcomes": extra_cases})
    evidence = _evidence()

    result = build_migration_readiness(tuple(outcomes), evidence, _scorecard(evidence))

    scope_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "CURRENT_SCOPE_EXPECTATIONS"
    )
    assert scope_gate.status is MigrationReadinessStatus.FAIL
    assert "UNEXPECTED_CURRENT_SCOPE_CASE" in scope_gate.reason_codes


def test_published_scenario_without_replay_artifact_hash_stays_pending(monkeypatch) -> None:
    _patch_complete_failure_manifest(monkeypatch)
    outcomes = list(_passing_outcomes())
    first = outcomes[0]
    observation = first.current_scope_outcomes[0]
    reduced_hashes = dict(observation.artifact_hashes)
    reduced_hashes.pop("replayed_bundle")
    changed_observation = observation.model_copy(
        update={"artifact_hashes": FrozenMap(reduced_hashes)}
    )
    changed_cases = (changed_observation, *first.current_scope_outcomes[1:])
    aggregate_hashes = {
        f"{item.current_scope.case_id}.{name}": digest
        for item in changed_cases
        for name, digest in item.artifact_hashes.items()
    }
    outcomes[0] = first.model_copy(
        update={
            "current_scope_outcomes": changed_cases,
            "artifact_hashes": FrozenMap(aggregate_hashes),
        }
    )
    evidence = _evidence(receipts=_release_receipts())

    result = build_migration_readiness(tuple(outcomes), evidence, _scorecard(evidence))

    scope_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "CURRENT_SCOPE_EXPECTATIONS"
    )
    assert scope_gate.status is MigrationReadinessStatus.PENDING
    assert "PUBLISHED_RUN_EVIDENCE_INCOMPLETE" in scope_gate.reason_codes


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("source_tree_sha256", "9" * 64, "source identity"),
        ("period_start", datetime(2026, 10, 4).date(), "period"),
        ("origin", ShadowEvaluationOrigin.SYNTHETIC_EVALUATION, "origin"),
        ("evidence_hashes", ("e" * 64,), "hashes"),
    ),
)
def test_readiness_rejects_scorecard_for_different_verified_evidence(
    field: str,
    value: object,
    message: str,
) -> None:
    evidence = _evidence(receipts=_release_receipts())
    scorecard = _scorecard(evidence).model_copy(update={field: value})

    with pytest.raises(ValueError, match=message):
        build_migration_readiness((), evidence, scorecard)


def test_readiness_fails_undeclared_scenario_assertion_result(monkeypatch) -> None:
    _patch_complete_failure_manifest(monkeypatch)
    outcomes = list(_passing_outcomes())
    first = outcomes[0]
    outcomes[0] = first.model_copy(
        update={
            "assertions_passed": (
                *first.assertions_passed,
                ScenarioAssertionId.MISSED_RUN_RECORD_DURABLE,
            )
        }
    )
    evidence = _evidence(receipts=_release_receipts())

    result = build_migration_readiness(tuple(outcomes), evidence, _scorecard(evidence))

    assertion_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "SCENARIO_ASSERTIONS"
    )
    assert assertion_gate.status is MigrationReadinessStatus.FAIL
    assert "UNDECLARED_SCENARIO_ASSERTION_RESULT" in assertion_gate.reason_codes


@pytest.mark.parametrize(
    ("field", "value", "expected_status", "reason_code"),
    (
        ("run_id", None, MigrationReadinessStatus.PENDING, "PUBLISHED_RUN_ID_MISSING"),
        ("run_revision", 9, MigrationReadinessStatus.FAIL, "PUBLISHED_RUN_REVISION_MISMATCH"),
        (
            "replay_json_matches",
            None,
            MigrationReadinessStatus.PENDING,
            "PUBLISHED_RUN_REPLAY_EVIDENCE_MISSING",
        ),
        (
            "replay_markdown_matches",
            False,
            MigrationReadinessStatus.FAIL,
            "PUBLISHED_RUN_REPLAY_MISMATCH",
        ),
    ),
)
def test_published_scenario_identity_and_replay_evidence_are_rechecked(
    field: str,
    value: object,
    expected_status: MigrationReadinessStatus,
    reason_code: str,
) -> None:
    outcomes = list(_passing_outcomes())
    first = outcomes[0]
    observation = first.current_scope_outcomes[0]
    changes = {field: value}
    if field == "run_id":
        changes["run_revision"] = None
    changed_observation = observation.model_copy(update=changes)
    outcomes[0] = first.model_copy(
        update={"current_scope_outcomes": (changed_observation, *first.current_scope_outcomes[1:])}
    )
    evidence = _evidence(receipts=_release_receipts())

    result = build_migration_readiness(tuple(outcomes), evidence, _scorecard(evidence))

    scope_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "CURRENT_SCOPE_EXPECTATIONS"
    )
    assert scope_gate.status is expected_status
    assert reason_code in scope_gate.reason_codes


def test_skipped_scenario_cannot_claim_a_publication_identity() -> None:
    outcomes = list(_passing_outcomes())
    skipped_index, case_index = next(
        (scenario_index, outcome_index)
        for scenario_index, outcome in enumerate(outcomes)
        for outcome_index, observation in enumerate(outcome.current_scope_outcomes)
        if observation.current_scope.execution_status.value == "SKIPPED"
    )
    skipped = outcomes[skipped_index]
    observation = skipped.current_scope_outcomes[case_index].model_copy(
        update={"run_id": "unexpected-run", "run_revision": 1}
    )
    changed_cases = list(skipped.current_scope_outcomes)
    changed_cases[case_index] = observation
    outcomes[skipped_index] = skipped.model_copy(
        update={"current_scope_outcomes": tuple(changed_cases)}
    )
    evidence = _evidence()

    result = build_migration_readiness(tuple(outcomes), evidence, _scorecard(evidence))

    scope_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "CURRENT_SCOPE_EXPECTATIONS"
    )
    assert scope_gate.status is MigrationReadinessStatus.FAIL
    assert "UNEXPECTED_PUBLICATION_IDENTITY" in scope_gate.reason_codes


def test_migration_gate_contract_requires_evidence_and_reasons() -> None:
    with pytest.raises(ValidationError, match="passing migration gates require evidence"):
        MigrationReadinessGate(id="PASS_GATE", status=MigrationReadinessStatus.PASS)
    with pytest.raises(ValidationError, match="non-passing migration gates require"):
        MigrationReadinessGate(id="PENDING_GATE", status=MigrationReadinessStatus.PENDING)
    with pytest.raises(ValidationError, match="evidence ids must be unique"):
        MigrationReadinessGate(
            id="PASS_GATE",
            status=MigrationReadinessStatus.PASS,
            evidence_ids=("record-1", "record-1"),
        )
    with pytest.raises(ValidationError, match="reason codes must be unique"):
        MigrationReadinessGate(
            id="PENDING_GATE",
            status=MigrationReadinessStatus.PENDING,
            reason_codes=("MISSING", "MISSING"),
        )


def test_migration_sections_compute_status_and_only_defer_full_source() -> None:
    pending = MigrationReadinessGate(
        id="PENDING_GATE",
        status=MigrationReadinessStatus.PENDING,
        reason_codes=("MISSING_EVIDENCE",),
    )
    with pytest.raises(ValidationError, match="gate ids must be unique"):
        MigrationReadinessSection(
            id="CURRENT_SCOPE_CONFORMANCE",
            gate_results=(pending, pending),
        )
    with pytest.raises(ValidationError, match="only full-source conformance"):
        MigrationReadinessSection(
            id="CURRENT_SCOPE_CONFORMANCE",
            gate_results=(
                MigrationReadinessGate(
                    id="DEFERRED_GATE",
                    status=MigrationReadinessStatus.DEFERRED,
                    reason_codes=("NOT_APPROVED",),
                ),
            ),
        )
    with pytest.raises(ValidationError, match="every full-source conformance gate"):
        MigrationReadinessSection(
            id="FULL_SOURCE_CONFORMANCE",
            gate_results=(
                MigrationReadinessGate(
                    id="PASS_GATE",
                    status=MigrationReadinessStatus.PASS,
                    evidence_ids=("evidence-1",),
                ),
            ),
        )
    status_section = MigrationReadinessSection(
        id="CURRENT_SCOPE_CONFORMANCE",
        gate_results=(
            pending,
            MigrationReadinessGate(
                id="PASS_GATE",
                status=MigrationReadinessStatus.PASS,
                evidence_ids=("evidence-1",),
            ),
        ),
    )
    assert status_section.status is MigrationReadinessStatus.PENDING


def test_readiness_rejects_swapped_section_identities() -> None:
    passing = MigrationReadinessGate(
        id="PASS_GATE",
        status=MigrationReadinessStatus.PASS,
        evidence_ids=("evidence-1",),
    )
    pending = MigrationReadinessGate(
        id="PENDING_GATE",
        status=MigrationReadinessStatus.PENDING,
        reason_codes=("MISSING_EVIDENCE",),
    )
    deferred = MigrationReadinessGate(
        id="DEFERRED_GATE",
        status=MigrationReadinessStatus.DEFERRED,
        reason_codes=("SOURCE_SCOPE_DESIGN_REQUIRED",),
    )
    with pytest.raises(ValidationError, match="fixed R11 identities"):
        MigrationReadiness(
            current_scope_conformance=MigrationReadinessSection(
                id="SUPERVISED_SHADOW_READINESS", gate_results=(passing,)
            ),
            supervised_shadow_readiness=MigrationReadinessSection(
                id="CURRENT_SCOPE_CONFORMANCE", gate_results=(pending,)
            ),
            full_source_conformance=MigrationReadinessSection(
                id="FULL_SOURCE_CONFORMANCE", gate_results=(deferred,)
            ),
            shadow_graduation=MigrationReadinessSection(
                id="SHADOW_GRADUATION", gate_results=(pending,)
            ),
        )


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    (
        ("missing", MigrationReadinessStatus.PENDING),
        ("extra", MigrationReadinessStatus.FAIL),
        ("wrong_kind", MigrationReadinessStatus.FAIL),
        ("partial", MigrationReadinessStatus.PENDING),
        ("failed", MigrationReadinessStatus.FAIL),
        ("unknown_mismatch", MigrationReadinessStatus.FAIL),
        ("field_order", MigrationReadinessStatus.FAIL),
    ),
)
def test_domain_assertion_recheck_fails_closed(mutation, expected_status) -> None:
    scenario = load_evaluation_scenarios()[0]
    outcome = _passing_outcomes()[0]
    expected_assertion = scenario.domain_assertions[0]
    actual = outcome.domain_assertion_outcomes[0]
    fields = actual.matched_fields
    if mutation == "missing":
        actual_results = ()
    elif mutation == "extra":
        actual_results = (actual, actual)
    elif mutation == "wrong_kind":
        actual_results = (
            actual.model_copy(update={"kind": DomainAssertionKind.REGIME_CALCULATION}),
        )
    elif mutation == "partial":
        actual_results = (actual.model_copy(update={"matched_fields": fields[:-1]}),)
    elif mutation == "failed":
        actual_results = (
            actual.model_copy(
                update={
                    "status": "FAIL",
                    "matched_fields": fields[1:],
                    "mismatched_fields": (fields[0],),
                }
            ),
        )
    elif mutation == "unknown_mismatch":
        actual_results = (
            actual.model_copy(
                update={"mismatched_fields": ("unapproved-field",), "status": "FAIL"}
            ),
        )
    else:
        actual_results = (actual.model_copy(update={"matched_fields": tuple(reversed(fields))}),)

    assert (
        _recheck_domain_assertions(
            (expected_assertion,),
            outcome.model_copy(update={"domain_assertion_outcomes": actual_results}),
        )
        is expected_status
    )


def test_outcome_index_rejects_duplicate_and_reordered_scenarios() -> None:
    outcomes = _passing_outcomes()
    evidence = _evidence()
    scorecard = _scorecard(evidence)
    duplicate_result = build_migration_readiness(
        (*outcomes[:-1], outcomes[-2]), evidence, scorecard
    )
    reordered_result = build_migration_readiness(
        (outcomes[1], outcomes[0], *outcomes[2:]), evidence, scorecard
    )

    for result in (duplicate_result, reordered_result):
        outcome_gate = next(
            gate
            for gate in result.current_scope_conformance.gate_results
            if gate.id == "SCENARIO_OUTCOMES"
        )
        assert outcome_gate.status is MigrationReadinessStatus.FAIL


def test_scenario_recheck_classifies_missing_and_extra_cases() -> None:
    scenarios = load_evaluation_scenarios()
    scenario = next(item for item in scenarios if len(item.current_scope_expectation.subcases) == 1)
    outcome = next(item for item in _passing_outcomes() if item.scenario_id is scenario.id)
    missing_case = outcome.model_copy(
        update={"current_scope_outcomes": outcome.current_scope_outcomes[:1]}
    )
    expected_assertion = ScenarioAssertionId.CURRENT_SCOPE_MATCHES
    assert _recompute_assertion(scenario, missing_case, expected_assertion) is (
        MigrationReadinessStatus.PENDING
    )
    assert (
        _recompute_assertion(scenario, outcome, expected_assertion) is MigrationReadinessStatus.PASS
    )


def test_observation_booleans_distinguish_fail_missing_and_pass() -> None:
    observations = _passing_outcomes()[0].current_scope_outcomes
    false_observation = observations[0].model_copy(update={"watchlist_exclusions_visible": False})
    missing_observation = observations[0].model_copy(update={"watchlist_exclusions_visible": None})

    assert _observation_booleans((false_observation,), "watchlist_exclusions_visible") is (
        MigrationReadinessStatus.FAIL
    )
    assert _observation_booleans((missing_observation,), "watchlist_exclusions_visible") is (
        MigrationReadinessStatus.PENDING
    )
    assert _observation_booleans((), "watchlist_exclusions_visible") is (
        MigrationReadinessStatus.PENDING
    )
    assert _observation_booleans(observations, "watchlist_exclusions_visible") is (
        MigrationReadinessStatus.PASS
    )


def test_claimed_assertion_status_reads_all_three_result_groups() -> None:
    outcome = _passing_outcomes()[0]
    assertion = outcome.assertions_passed[0]
    assert _claimed_assertion_status(outcome, assertion) is MigrationReadinessStatus.PASS
    claimed_passes = tuple(value for value in outcome.assertions_passed if value is not assertion)
    failed = outcome.model_copy(
        update={
            "assertions_passed": claimed_passes,
            "assertions_failed": (assertion,),
        }
    )
    pending = outcome.model_copy(
        update={
            "assertions_passed": claimed_passes,
            "assertions_pending": (assertion,),
        }
    )
    assert _claimed_assertion_status(failed, assertion) is MigrationReadinessStatus.FAIL
    assert _claimed_assertion_status(pending, assertion) is MigrationReadinessStatus.PENDING
    assert _claimed_assertion_status(outcome, ScenarioAssertionId.MISSED_RUN_RECORD_DURABLE) is None


@pytest.mark.parametrize(
    ("missing_domain_results", "expected_status", "reason_code"),
    (
        (False, MigrationReadinessStatus.FAIL, "DOMAIN_ASSERTION_FAILED_OR_MISMATCHED"),
        (True, MigrationReadinessStatus.PENDING, "DOMAIN_ASSERTION_EVIDENCE_MISSING"),
    ),
)
def test_builder_aggregates_domain_assertion_failure_and_missing_results(
    missing_domain_results,
    expected_status,
    reason_code,
) -> None:
    outcomes = list(_passing_outcomes())
    scenario = load_evaluation_scenarios()[0]
    outcome = outcomes[0]
    assertion = ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS
    if missing_domain_results:
        assertions_passed = tuple(
            item for item in outcome.assertions_passed if item is not assertion
        )
        changed_outcome = outcome.model_copy(
            update={
                "domain_assertion_outcomes": (),
                "assertions_passed": assertions_passed,
                "assertions_pending": (*outcome.assertions_pending, assertion),
            }
        )
    else:
        domain_result = outcome.domain_assertion_outcomes[0]
        mismatch_field = domain_result.matched_fields[0]
        changed_outcome = outcome.model_copy(
            update={
                "domain_assertion_outcomes": (
                    domain_result.model_copy(
                        update={
                            "status": "FAIL",
                            "matched_fields": domain_result.matched_fields[1:],
                            "mismatched_fields": (mismatch_field,),
                        }
                    ),
                )
            }
        )
    outcomes[0] = changed_outcome
    evidence = _evidence()

    result = build_migration_readiness(tuple(outcomes), evidence, _scorecard(evidence))

    assertion_gate = next(
        gate
        for gate in result.current_scope_conformance.gate_results
        if gate.id == "SCENARIO_ASSERTIONS"
    )
    assert assertion_gate.status is expected_status
    assert reason_code in assertion_gate.reason_codes
    assert scenario.id is outcome.scenario_id


def test_scorecard_day_gate_stays_pending_at_nineteen_verified_personal_days(
    valid_packet,
) -> None:
    evidence = _personal_shadow_evidence_with_valid_days(19, valid_packet.run)
    scorecard = _scorecard(
        evidence,
        status=EvaluationGateStatus.PASS,
        valid_trading_days=19,
    )
    scorecard = scorecard.model_copy(
        update={
            "gate_results": tuple(
                gate.model_copy(
                    update={
                        "status": EvaluationGateStatus.PENDING,
                        "evidence_ids": (),
                        "reason_codes": ("TWENTY_VALID_PERSONAL_SHADOW_DAYS_REQUIRED",),
                    }
                )
                if gate.id is ShadowGateId.AT_LEAST_TWENTY_TRADING_DAYS
                else gate
                for gate in scorecard.gate_results
            )
        }
    )

    result = build_migration_readiness((), evidence, scorecard)

    verified_day_count_gate = next(
        gate
        for gate in result.shadow_graduation.gate_results
        if gate.id == "VERIFIED_VALID_DAY_COUNT"
    )
    day_gate = next(
        gate for gate in result.shadow_graduation.gate_results if gate.id == "PERSONAL_SHADOW_DAYS"
    )
    assert verified_day_count_gate.status is MigrationReadinessStatus.PASS
    assert verified_day_count_gate.measured_value == 19
    assert day_gate.status is MigrationReadinessStatus.PENDING
    assert "TWENTY_VALID_PERSONAL_SHADOW_DAYS_REQUIRED" in day_gate.reason_codes
    assert result.shadow_graduation.status is MigrationReadinessStatus.PENDING


def test_personal_shadow_graduation_passes_with_twenty_verified_days(valid_packet) -> None:
    evidence = _personal_shadow_evidence_with_valid_days(20, valid_packet.run)
    scorecard = _scorecard(
        evidence,
        status=EvaluationGateStatus.PASS,
        valid_trading_days=20,
    )

    result = build_migration_readiness((), evidence, scorecard)

    assert result.shadow_graduation.status is MigrationReadinessStatus.PASS
    assert (
        next(
            gate
            for gate in result.shadow_graduation.gate_results
            if gate.id == "VERIFIED_VALID_DAY_COUNT"
        ).status
        is MigrationReadinessStatus.PASS
    )
    assert (
        next(
            gate
            for gate in result.shadow_graduation.gate_results
            if gate.id == "PERSONAL_SHADOW_DAYS"
        ).measured_value
        == 20
    )


def test_synthetic_publications_do_not_count_as_personal_shadow_days(valid_packet) -> None:
    evidence = _personal_shadow_evidence_with_valid_days(
        20,
        valid_packet.run,
        real_provider_day_count=1,
    )

    class WeekdayCalendar:
        def is_trading_day(self, market_date: date) -> bool:
            return market_date.weekday() < 5

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        WeekdayCalendar(),  # type: ignore[arg-type]
        evidence=evidence,
    )

    assert scorecard.origin is ShadowEvaluationOrigin.PERSONAL_SHADOW
    assert scorecard.valid_trading_days == 1


def test_migration_readiness_reverifies_only_real_personal_shadow_days(valid_packet) -> None:
    evidence = _personal_shadow_evidence_with_valid_days(
        20,
        valid_packet.run,
        real_provider_day_count=1,
    )
    scorecard = _scorecard(
        evidence,
        status=EvaluationGateStatus.PASS,
        valid_trading_days=1,
    )

    result = build_migration_readiness((), evidence, scorecard)

    day_count_gate = next(
        gate
        for gate in result.shadow_graduation.gate_results
        if gate.id == "VERIFIED_VALID_DAY_COUNT"
    )
    assert day_count_gate.status is MigrationReadinessStatus.PASS
    assert day_count_gate.measured_value == 1


def test_personal_shadow_graduation_rejects_unverified_twenty_day_claim() -> None:
    evidence = _evidence(
        receipts=_release_receipts(),
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        evidence_hashes=("c" * 64,),
    )
    scorecard = _scorecard(
        evidence,
        status=EvaluationGateStatus.PASS,
        valid_trading_days=20,
    )

    result = build_migration_readiness((), evidence, scorecard)

    day_count_gate = next(
        gate
        for gate in result.shadow_graduation.gate_results
        if gate.id == "VERIFIED_VALID_DAY_COUNT"
    )
    assert day_count_gate.status is MigrationReadinessStatus.FAIL
    assert day_count_gate.reason_codes == ("SCORECARD_VALID_DAY_COUNT_MISMATCH",)
    assert day_count_gate.measured_value == 20
    assert day_count_gate.required_value == 0
    assert result.shadow_graduation.status is MigrationReadinessStatus.FAIL


def test_release_gate_rejects_receipts_bound_to_a_different_commit() -> None:
    gate = _release_check_gate(
        _release_receipts(),
        (ReleaseCheckId.LINT,),
        gate_id="EXACT_HEAD_RELEASE_CHECKS",
        source_commit_sha256="9" * 64,
        source_tree_sha256=_TREE,
    )

    assert gate.status is MigrationReadinessStatus.FAIL
    assert gate.reason_codes == ("EXACT_HEAD_RELEASE_CHECK_FAILED_OR_MISMATCHED",)


def test_domain_assertion_umbrella_stays_pending_without_declared_operations() -> None:
    scenario = load_evaluation_scenarios()[0].model_copy(update={"domain_assertions": ()})
    outcome = _passing_outcomes()[0]

    assert (
        _recompute_assertion(scenario, outcome, ScenarioAssertionId.DOMAIN_ASSERTIONS_PASS)
        is MigrationReadinessStatus.PENDING
    )


def test_supervised_readiness_requires_all_source_bound_operator_attestations(monkeypatch) -> None:
    _patch_complete_failure_manifest(monkeypatch)
    evidence = _evidence(receipts=_release_receipts(), attestations=_attestations())
    scorecard = _scorecard(evidence)

    result = build_migration_readiness(_passing_outcomes(), evidence, scorecard)

    assert result.current_scope_conformance.status is MigrationReadinessStatus.PASS
    assert result.supervised_shadow_readiness.status is MigrationReadinessStatus.PASS


def test_stale_source_operator_attestations_leave_supervised_readiness_pending(
    monkeypatch,
) -> None:
    _patch_complete_failure_manifest(monkeypatch)
    evidence = _evidence(
        receipts=_release_receipts(),
        attestations=_attestations(source_commit_sha256="9" * 64),
    )
    scorecard = _scorecard(evidence)

    result = build_migration_readiness(_passing_outcomes(), evidence, scorecard)

    assert result.supervised_shadow_readiness.status is MigrationReadinessStatus.PENDING
    attestation_gate = next(
        gate
        for gate in result.supervised_shadow_readiness.gate_results
        if gate.id == "OPERATOR_ATTESTATIONS"
    )
    assert attestation_gate.status is MigrationReadinessStatus.PENDING


def test_current_operator_attestations_are_not_failed_by_retained_stale_history(
    monkeypatch,
) -> None:
    _patch_complete_failure_manifest(monkeypatch)
    stale = _attestations(source_commit_sha256="9" * 64, id_prefix="stale-")
    current = _attestations(id_prefix="current-")
    evidence = _evidence(
        receipts=_release_receipts(),
        attestations=(*stale, *current),
    )
    scorecard = _scorecard(evidence)

    result = build_migration_readiness(_passing_outcomes(), evidence, scorecard)

    assert result.supervised_shadow_readiness.status is MigrationReadinessStatus.PASS
    attestation_gate = next(
        gate
        for gate in result.supervised_shadow_readiness.gate_results
        if gate.id == "OPERATOR_ATTESTATIONS"
    )
    assert attestation_gate.evidence_ids == tuple(item.id for item in current)


@pytest.mark.parametrize(
    ("version_field", "stale_value"),
    (("package_version", "0.4.0"), ("skill_version", "sha256:" + "0" * 64)),
)
def test_operator_attestations_with_stale_package_or_skill_version_are_pending(
    monkeypatch,
    version_field: str,
    stale_value: str,
) -> None:
    _patch_complete_failure_manifest(monkeypatch)
    evidence = _evidence(
        receipts=_release_receipts(),
        attestations=_attestations(**{version_field: stale_value}),
    )
    scorecard = _scorecard(evidence)

    result = build_migration_readiness(_passing_outcomes(), evidence, scorecard)

    attestation_gate = next(
        gate
        for gate in result.supervised_shadow_readiness.gate_results
        if gate.id == "OPERATOR_ATTESTATIONS"
    )
    assert attestation_gate.status is MigrationReadinessStatus.PENDING


def test_current_premarket_skill_version_is_pending_when_a_source_file_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        release_gates_module, "_read_confined_skill_source", lambda _root, _path: None
    )

    assert release_gates_module._current_premarket_skill_version() is None


def test_current_premarket_skill_version_is_pending_when_source_is_unsafe(
    monkeypatch,
) -> None:
    def reject_source(_root: Path, _path: Path) -> bytes:
        raise ValueError("unsafe skill source")

    monkeypatch.setattr(release_gates_module, "_read_confined_skill_source", reject_source)

    assert release_gates_module._current_premarket_skill_version() is None


def test_current_premarket_skill_version_is_pending_when_packaged_skill_cannot_load(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_module = tmp_path / "src/finance_research_agent/evaluation/release_gates.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.touch()
    monkeypatch.setattr(release_gates_module, "__file__", str(fake_module))

    def reject_installed_skill() -> str:
        raise RuntimeError("installed skill is unavailable")

    monkeypatch.setattr(
        release_gates_module,
        "load_installed_premarket_skill_version",
        reject_installed_skill,
    )

    assert release_gates_module._current_premarket_skill_version() is None


def test_confined_skill_source_rejects_paths_outside_the_source_root(tmp_path: Path) -> None:
    assert (
        release_gates_module._read_confined_skill_source(tmp_path, tmp_path.parent / "outside")
        is None
    )


def test_confined_skill_source_rejects_symlinked_or_missing_files(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    outside = tmp_path / "outside-skill.md"
    outside.write_text("untrusted skill", encoding="utf-8")
    linked = source_root / "linked.md"
    linked.symlink_to(outside)

    assert release_gates_module._read_confined_skill_source(source_root, linked) is None
    assert (
        release_gates_module._read_confined_skill_source(source_root, source_root / "missing.md")
        is None
    )


def test_shadow_graduation_uses_scorecard_failure_and_real_day_evidence() -> None:
    gates = tuple(
        EvaluationGate(
            id=gate_id,
            status=(
                EvaluationGateStatus.FAIL
                if gate_id is ShadowGateId.SAFETY
                else EvaluationGateStatus.PASS
            ),
            evidence_ids=("scorecard",) if gate_id is not ShadowGateId.SAFETY else (),
            reason_codes=("SAFETY_INCIDENT",) if gate_id is ShadowGateId.SAFETY else (),
        )
        for gate_id in ShadowGateId
    )
    scorecard = ShadowScorecard(
        period_start=datetime(2026, 10, 5).date(),
        period_end=datetime(2026, 10, 11).date(),
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        valid_trading_days=20,
        eligible_online_days=20,
        evidence_hashes=(),
        gate_results=gates,
    )
    evidence = _evidence(origin=ShadowEvaluationOrigin.PERSONAL_SHADOW)

    result = build_migration_readiness((), evidence, scorecard)

    assert result.shadow_graduation.status is MigrationReadinessStatus.FAIL


def _patch_complete_failure_manifest(monkeypatch) -> None:
    scenarios = load_evaluation_scenarios()
    first = scenarios[0].model_copy(update={"injected_failures": tuple(FailureInjectionId)})
    monkeypatch.setattr(
        release_gates_module,
        "load_evaluation_scenarios",
        lambda: (first, *scenarios[1:]),
    )
