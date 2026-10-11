from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from finance_research_agent.application.feedback_service import RecordedFeedback
from finance_research_agent.domain.enums import Capability
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    EventRecord,
    PublishedArtifact,
    PublishedRunBundle,
    RunContext,
    StoredRun,
)
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    ReleaseCheckConclusion,
    ReleaseCheckId,
    ReleaseCheckReceipt,
    ShadowDayAvailability,
    ShadowDayExclusionReason,
    ShadowDayObservation,
    ShadowFailureOperation,
    ShadowFailureOperationsCoverage,
    ShadowFailureOperationsObservation,
    ShadowFailureOperationStatus,
    ShadowFailureRecoveryRecord,
    ShadowSafetyIncident,
    ShadowSafetyIncidentCategory,
    ShadowSafetyIncidentObservation,
)
from finance_research_agent.evaluation.shadow_scorecard import (
    DailyReviewTimeObservation,
    DailyUsefulnessObservation,
    EvaluationGate,
    EvaluationGateStatus,
    ShadowEvaluationOrigin,
    ShadowExecutiveIdentification,
    ShadowGateId,
    ShadowPeriod,
    ShadowScorecard,
    _daily_review_time_gate,
    _daily_usefulness_gate,
    _expected_executive_identification,
    _median,
    build_shadow_scorecard,
)
from tests.support.shadow_evidence import (
    personal_reduced_publication,
    recorded_component_versions,
)


class _Calendar:
    def is_trading_day(self, market_date: date) -> bool:
        return market_date.weekday() < 5

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        raise NotImplementedError


class _EmptyRuns:
    def __init__(
        self,
        records: tuple[StoredRun, ...] = (),
        staged_artifacts: dict[tuple[str, str], bytes] | None = None,
    ) -> None:
        self.records = records
        self.staged_artifacts = staged_artifacts or {}

    def list_runs(self, market_date_start: date, market_date_end: date) -> tuple[StoredRun, ...]:
        return tuple(
            item
            for item in self.records
            if market_date_start <= item.run.market_date <= market_date_end
        )

    def load(self, run_id: str):
        return next((item for item in self.records if item.run.run_id == run_id), None)

    def read_staged_artifact(self, run_id: str, artifact_name: str) -> bytes | None:
        return self.staged_artifacts.get((run_id, artifact_name))


class _EmptyArtifacts:
    def __init__(self) -> None:
        self.bundle: PublishedRunBundle | None = None
        self.report: str | None = None
        self.artifact: PublishedArtifact | None = None

    def load_published_bundle(self, run_id: str):
        return self.bundle

    def get_report(self, run_id: str) -> None:
        return self.report

    def get_published_artifact(self, run_id: str) -> None:
        return self.artifact


class _MultipleArtifacts:
    def __init__(
        self,
        bundles: tuple[PublishedRunBundle, ...],
        artifacts: tuple[PublishedArtifact, ...],
    ) -> None:
        self.bundles = {item.run.run_id: item for item in bundles}
        self.reports = {item.run.run_id: item.report_markdown for item in bundles}
        self.artifacts = {item.run_id: item for item in artifacts}

    def load_published_bundle(self, run_id: str) -> PublishedRunBundle | None:
        return self.bundles.get(run_id)

    def get_report(self, run_id: str) -> str | None:
        return self.reports.get(run_id)

    def get_published_artifact(self, run_id: str) -> PublishedArtifact | None:
        return self.artifacts.get(run_id)


class _EmptyFeedback:
    def __init__(self, records: tuple[RecordedFeedback, ...] = ()) -> None:
        self.records = records

    def list_feedback(self) -> tuple[RecordedFeedback, ...]:
        return self.records


class _EmptyObservations:
    def __init__(self, records: tuple[ShadowDayObservation, ...]) -> None:
        self.records = records

    def list_observations(self) -> tuple[ShadowDayObservation, ...]:
        return self.records


class _EmptyAttestations:
    def list_attestations(self) -> tuple[()]:
        return ()


class _EmptyReleaseEvidence:
    def list_receipts(self) -> tuple[()]:
        return ()


def _gates(status: EvaluationGateStatus = EvaluationGateStatus.PENDING):
    return tuple(
        EvaluationGate(
            id=gate_id,
            status=status,
            reason_codes=("evidence-pending",) if status is EvaluationGateStatus.PENDING else (),
        )
        for gate_id in ShadowGateId
    )


def _scorecard(**changes: object) -> ShadowScorecard:
    values: dict[str, object] = {"gate_results": _gates()}
    values.update(changes)
    return ShadowScorecard(**values)


def test_shadow_scorecard_requires_source_commit_and_tree_together() -> None:
    with pytest.raises(ValidationError, match="source identity requires both"):
        _scorecard(source_commit_sha256="a" * 64)


def test_shadow_scorecard_carries_verified_source_identity() -> None:
    from finance_research_agent.evaluation.shadow_scorecard import (
        _create_shadow_evaluation_evidence,
    )

    evidence = _create_shadow_evaluation_evidence(
        period=ShadowPeriod(start_date=date(2026, 8, 26), end_date=date(2026, 8, 26)),
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        verified_runs=(),
        feedback_records=(),
        observations=(),
        operator_attestations=(),
        release_check_receipts=(),
        source_commit_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        evidence_hashes=("c" * 64,),
    )

    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    assert scorecard.source_commit_sha256 == evidence.source_commit_sha256
    assert scorecard.source_tree_sha256 == evidence.source_tree_sha256


def _metric_feedback(
    feedback_id: str,
    *,
    usefulness_score: int,
    executive_seconds: int,
    detailed_seconds: int,
    notes: str | None = None,
) -> RecordedFeedback:
    return RecordedFeedback(
        schema_version="0.2",
        feedback_id=feedback_id,
        run_id="premarket-2026-08-26-r1",
        bundle_sha256="a" * 64,
        recorded_at=datetime(2026, 8, 26, 16, tzinfo=UTC),
        clarity_score=4,
        evidence_score=4,
        usefulness_score=usefulness_score,
        notes=notes,
        citation_reviews=(),
        executive_review_duration_seconds=executive_seconds,
        detailed_review_duration_seconds=detailed_seconds,
    )


def _citation_evidence_snapshot(
    valid_packet,
    valid_brief_draft,
    *,
    reviews=(),
    feedback_schema_version: str = "0.2",
    material_claims: bool = True,
    revision_count: int = 1,
    reviewed_revisions: tuple[int, ...] | None = None,
    additional_feedback_batches: tuple[tuple[tuple[object, ...], str], ...] = (),
    invalid_receipt: bool = False,
    missing_validation: bool = False,
    post_cutoff_evidence: bool = False,
):
    from finance_research_agent.application.packet_service import build_research_packet
    from finance_research_agent.application.replay_service import ArtifactReplayResult
    from finance_research_agent.domain.enums import BriefOrigin
    from finance_research_agent.domain.validation import validate_research_brief
    from finance_research_agent.evaluation.citation_sampling import (
        select_citation_entailment_sample,
    )
    from finance_research_agent.evaluation.shadow_scorecard import (
        VerifiedShadowRunEvidence,
        _create_shadow_evaluation_evidence,
    )

    base_run = valid_packet.run
    verified_runs = []
    feedback_records = []
    first_bundle = None
    for revision in range(1, revision_count + 1):
        run_id = f"premarket-{base_run.market_date.isoformat()}-r{revision}"
        run = base_run.model_copy(update={"run_id": run_id, "revision": revision})
        packet = build_research_packet(
            run=run,
            evidence=valid_packet.evidence,
            snapshots=valid_packet.market,
            events=valid_packet.events,
            metrics=valid_packet.metrics,
            gates=valid_packet.gates,
            candidates=valid_packet.candidates,
            exclusions=valid_packet.candidate_exclusions,
            plans=valid_packet.deterministic_plan_inputs,
            capabilities=valid_packet.capability_states,
            observations=valid_packet.prior_plan_observations,
            max_serialized_bytes=valid_packet.synthesis_constraints.max_serialized_bytes,
            regime_result=valid_packet.regime_result,
        )
        bundle_data = {"research_packet": packet.model_dump(mode="json")}
        if material_claims:
            brief = valid_brief_draft.model_copy(update={"run_id": run_id})
            bundle_data["brief_draft"] = brief.model_dump(mode="json")
            if not missing_validation:
                bundle_data["validation_report"] = validate_research_brief(
                    packet,
                    brief,
                    validation_attempt=1,
                ).model_dump(mode="json")
        else:
            bundle_data["brief_origin"] = BriefOrigin.DETERMINISTIC_REDUCED.value
        if first_bundle is None:
            first_bundle = PublishedRunBundle.model_construct(
                run=run,
                bundle=FrozenMap(bundle_data),
                report_markdown="verified report fixture",
                markdown_sha256=None,
            )
        if post_cutoff_evidence:
            packet_data = dict(bundle_data["research_packet"])
            evidence_data = [dict(item) for item in packet_data["evidence"]]
            source_data = dict(evidence_data[0]["source"])
            source_data["retrieved_at"] = (
                packet.run.require_evidence_cutoff() + timedelta(minutes=1)
            ).isoformat()
            evidence_data[0] = {**evidence_data[0], "source": source_data}
            bundle_data["research_packet"] = {**packet_data, "evidence": evidence_data}
        bundle = PublishedRunBundle.model_construct(
            run=run,
            bundle=FrozenMap(bundle_data),
            report_markdown="verified report fixture",
            markdown_sha256=None,
        )
        bundle_hash = sha256(canonical_bytes(bundle)).hexdigest()
        published_at = run.invoked_at + timedelta(minutes=2)
        artifact = PublishedArtifact(
            run_id=run_id,
            bundle_sha256="f" * 64 if invalid_receipt else bundle_hash,
            markdown_sha256="c" * 64,
            published_at=published_at,
        )
        replay = ArtifactReplayResult(
            run_id=run_id,
            bundle=bundle,
            report_markdown=bundle.report_markdown,
            json_matches=True,
            markdown_matches=True,
            stored_json_sha256=bundle_hash,
            replayed_json_sha256=bundle_hash,
            stored_markdown_sha256=artifact.markdown_sha256,
            replayed_markdown_sha256=artifact.markdown_sha256,
            component_version_mismatches=(),
        )
        verified_runs.append(
            VerifiedShadowRunEvidence(
                stored_run=StoredRun(run=run, published=True),
                published_bundle=bundle,
                published_artifact=artifact,
                replay_result=replay,
            )
        )
        selected_revisions = (
            range(1, revision_count + 1) if reviewed_revisions is None else reviewed_revisions
        )
        review_batches: list[tuple[tuple[object, ...], str]] = []
        if reviews and revision in selected_revisions:
            review_batches.append((tuple(reviews), feedback_schema_version))
        if revision == 1:
            review_batches.extend(additional_feedback_batches)
        for batch_index, (review_batch, batch_schema_version) in enumerate(review_batches, start=1):
            suffix = "" if batch_index == 1 else f"-later-{batch_index}"
            feedback_id = (
                "feedback-citations"
                if revision == 1 and batch_index == 1
                else f"feedback-citations-r{revision}{suffix}"
            )
            feedback_records.append(
                RecordedFeedback(
                    schema_version=batch_schema_version,
                    feedback_id=feedback_id,
                    run_id=run_id,
                    bundle_sha256=bundle_hash,
                    recorded_at=published_at + timedelta(minutes=batch_index),
                    clarity_score=4,
                    evidence_score=4,
                    usefulness_score=4,
                    citation_reviews=review_batch,
                )
            )
    assert first_bundle is not None
    evidence = _create_shadow_evaluation_evidence(
        period=ShadowPeriod(start_date=base_run.market_date, end_date=base_run.market_date),
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        verified_runs=tuple(verified_runs),
        feedback_records=tuple(feedback_records),
        observations=(),
        operator_attestations=(),
        release_check_receipts=(),
        source_commit_sha256=None,
        source_tree_sha256=None,
        evidence_hashes=("d" * 64,),
    )
    selected_claim_ids = select_citation_entailment_sample(first_bundle)
    return evidence, selected_claim_ids


def _citation_review(claim_id: str, *, verdict="SUPPORTED", schema_version="0.2"):
    from finance_research_agent.application.operations import (
        CitationEntailmentReview,
        CitationVerdict,
    )

    parsed_verdict = CitationVerdict(verdict) if schema_version == "0.2" else None
    return CitationEntailmentReview(
        schema_version=schema_version,
        citation_id="evidence-00",
        claim_id=claim_id,
        entails_claim=parsed_verdict is CitationVerdict.SUPPORTED
        if parsed_verdict is not None
        else verdict == "SUPPORTED",
        verdict=parsed_verdict,
        rationale="The cited frozen evidence supports this claim."
        if parsed_verdict is not None
        else None,
        reviewed_at=datetime(2026, 8, 26, 12, 47, tzinfo=UTC)
        if parsed_verdict is not None
        else None,
    )


def _evidence_gate(scorecard):
    return next(gate for gate in scorecard.gate_results if gate.id is ShadowGateId.EVIDENCE)


def test_evidence_gate_passes_after_supported_citation_sample_and_artifact_checks(
    valid_packet, valid_brief_draft
) -> None:
    empty_evidence, selected_claim_ids = _citation_evidence_snapshot(
        valid_packet, valid_brief_draft
    )
    complete_evidence, complete_claim_ids = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        reviews=tuple(_citation_review(claim_id) for claim_id in selected_claim_ids),
    )

    pending = build_shadow_scorecard(
        empty_evidence.published_runs,
        empty_evidence.feedback_records,
        _Calendar(),
        evidence=empty_evidence,
    )
    complete = build_shadow_scorecard(
        complete_evidence.published_runs,
        complete_evidence.feedback_records,
        _Calendar(),
        evidence=complete_evidence,
    )

    assert complete_claim_ids == selected_claim_ids
    assert len(selected_claim_ids) > 0
    assert _evidence_gate(pending).status is EvaluationGateStatus.PENDING
    assert _evidence_gate(pending).reason_codes == ("CITATION_ENTAILMENT_SAMPLE_INCOMPLETE",)
    assert _evidence_gate(complete).status is EvaluationGateStatus.PASS
    assert _evidence_gate(complete).reason_codes == ()
    assert _evidence_gate(complete).measured_value == 0
    assert _evidence_gate(complete).required_value == 0
    assert "premarket-2026-08-26-r1" in _evidence_gate(complete).evidence_ids
    assert "feedback-citations" in _evidence_gate(complete).evidence_ids


def test_evidence_gate_stays_pending_without_published_runs() -> None:
    from finance_research_agent.evaluation.shadow_scorecard import (
        _create_shadow_evaluation_evidence,
    )

    evidence = _create_shadow_evaluation_evidence(
        period=ShadowPeriod(start_date=date(2026, 8, 26), end_date=date(2026, 8, 26)),
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        verified_runs=(),
        feedback_records=(),
        observations=(),
        operator_attestations=(),
        release_check_receipts=(),
        source_commit_sha256=None,
        source_tree_sha256=None,
        evidence_hashes=("d" * 64,),
    )

    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _evidence_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert gate.reason_codes == ("NO_PUBLISHED_RUNS",)


@pytest.mark.parametrize("verdict", ["PARTIAL", "UNSUPPORTED"])
def test_evidence_gate_fails_on_any_material_adverse_full_review(
    valid_packet, valid_brief_draft, verdict: str
) -> None:
    _, selected_claim_ids = _citation_evidence_snapshot(valid_packet, valid_brief_draft)
    reviews = tuple(
        _citation_review(claim_id, verdict="SUPPORTED" if index else verdict)
        for index, claim_id in enumerate(selected_claim_ids)
    )
    evidence, _ = _citation_evidence_snapshot(valid_packet, valid_brief_draft, reviews=reviews)

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert gate.reason_codes == ("MATERIAL_CITATION_REVIEW_NOT_SUPPORTED",)


def test_evidence_gate_keeps_adverse_review_fail_when_its_citation_reference_is_invalid(
    valid_packet, valid_brief_draft
) -> None:
    _, selected_claim_ids = _citation_evidence_snapshot(valid_packet, valid_brief_draft)
    adverse_review = _citation_review(selected_claim_ids[0], verdict="PARTIAL").model_copy(
        update={"citation_id": "unreferenced-evidence"}
    )
    evidence, _ = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        reviews=(adverse_review,),
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert gate.reason_codes == ("MATERIAL_CITATION_REVIEW_NOT_SUPPORTED",)


@pytest.mark.parametrize(
    ("entails", "expected_status", "expected_reasons"),
    (
        (
            True,
            EvaluationGateStatus.PENDING,
            ("CITATION_ENTAILMENT_SAMPLE_INCOMPLETE", "LEGACY_REVIEW_INCOMPLETE"),
        ),
        (
            False,
            EvaluationGateStatus.FAIL,
            ("LEGACY_ADVERSE_CITATION_REVIEW", "LEGACY_REVIEW_INCOMPLETE"),
        ),
    ),
)
def test_evidence_gate_never_treats_legacy_assertion_as_full_sample_review(
    valid_packet,
    valid_brief_draft,
    entails: bool,
    expected_status: EvaluationGateStatus,
    expected_reasons: tuple[str, ...],
) -> None:
    _, selected_claim_ids = _citation_evidence_snapshot(valid_packet, valid_brief_draft)
    evidence, _ = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        feedback_schema_version="0.1",
        reviews=tuple(
            _citation_review(
                claim_id,
                verdict="SUPPORTED" if entails else "UNSUPPORTED",
                schema_version="0.1",
            )
            for claim_id in selected_claim_ids
        ),
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is expected_status
    assert gate.reason_codes == expected_reasons


def test_evidence_gate_passes_a_structurally_empty_citation_sample_after_artifact_checks(
    valid_packet, valid_brief_draft
) -> None:
    evidence, claim_ids = _citation_evidence_snapshot(
        valid_packet, valid_brief_draft, material_claims=False
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert claim_ids == ()
    assert _evidence_gate(scorecard).status is EvaluationGateStatus.PASS
    assert _evidence_gate(scorecard).reason_codes == ("NO_MATERIAL_CLAIMS",)
    assert _evidence_gate(scorecard).measured_value == 0


def test_evidence_gate_stays_pending_when_numeric_validation_evidence_is_missing(
    valid_packet, valid_brief_draft
) -> None:
    _, selected_claim_ids = _citation_evidence_snapshot(valid_packet, valid_brief_draft)
    evidence, _ = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        reviews=tuple(_citation_review(claim_id) for claim_id in selected_claim_ids),
        missing_validation=True,
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert gate.reason_codes == ("NUMERIC_VALIDATION_EVIDENCE_MISSING",)
    assert gate.measured_value is None
    assert gate.required_value == 0


def test_evidence_gate_fails_for_post_cutoff_packet_evidence(
    valid_packet, valid_brief_draft
) -> None:
    _, selected_claim_ids = _citation_evidence_snapshot(valid_packet, valid_brief_draft)
    evidence, _ = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        reviews=tuple(_citation_review(claim_id) for claim_id in selected_claim_ids),
        post_cutoff_evidence=True,
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert "FROZEN_RESEARCH_PACKET_INVALID" in gate.reason_codes


@pytest.mark.parametrize(
    ("entails", "expected_status", "expected_reasons"),
    (
        (
            True,
            EvaluationGateStatus.PASS,
            ("NO_MATERIAL_CLAIMS", "LEGACY_REVIEW_INCOMPLETE"),
        ),
        (
            False,
            EvaluationGateStatus.FAIL,
            (
                "NO_MATERIAL_CLAIMS",
                "LEGACY_ADVERSE_CITATION_REVIEW",
                "LEGACY_REVIEW_INCOMPLETE",
            ),
        ),
    ),
)
def test_evidence_gate_retains_legacy_reviews_without_material_claims(
    valid_packet,
    valid_brief_draft,
    entails: bool,
    expected_status: EvaluationGateStatus,
    expected_reasons: tuple[str, ...],
) -> None:
    review = _citation_review(
        "claim-outside-material-sample",
        verdict="SUPPORTED" if entails else "UNSUPPORTED",
        schema_version="0.1",
    )
    evidence, _ = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        feedback_schema_version="0.1",
        material_claims=False,
        reviews=(review,),
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is expected_status
    assert gate.reason_codes == expected_reasons


def test_evidence_gate_requires_review_of_each_published_revision(
    valid_packet, valid_brief_draft
) -> None:
    _, selected_claim_ids = _citation_evidence_snapshot(valid_packet, valid_brief_draft)
    reviews = tuple(_citation_review(claim_id) for claim_id in selected_claim_ids)
    evidence, _ = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        reviews=reviews,
        revision_count=2,
        reviewed_revisions=(1,),
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert gate.reason_codes == ("CITATION_ENTAILMENT_SAMPLE_INCOMPLETE",)
    assert "premarket-2026-08-26-r1" in gate.evidence_ids
    assert "premarket-2026-08-26-r2" in gate.evidence_ids


def test_evidence_gate_retains_later_adverse_review_after_supported_review(
    valid_packet, valid_brief_draft
) -> None:
    _, selected_claim_ids = _citation_evidence_snapshot(valid_packet, valid_brief_draft)
    supported_reviews = tuple(_citation_review(claim_id) for claim_id in selected_claim_ids)
    adverse_review = _citation_review(selected_claim_ids[0], verdict="PARTIAL")
    evidence, _ = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        reviews=supported_reviews,
        additional_feedback_batches=(((adverse_review,), "0.2"),),
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert gate.reason_codes == ("MATERIAL_CITATION_REVIEW_NOT_SUPPORTED",)
    assert "feedback-citations-r1-later-2" in gate.evidence_ids


def test_evidence_gate_surfaces_legacy_positive_alongside_full_review(
    valid_packet, valid_brief_draft
) -> None:
    _, selected_claim_ids = _citation_evidence_snapshot(valid_packet, valid_brief_draft)
    evidence, _ = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        reviews=tuple(_citation_review(claim_id) for claim_id in selected_claim_ids),
        additional_feedback_batches=(
            ((_citation_review(selected_claim_ids[0], schema_version="0.1"),), "0.1"),
        ),
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PASS
    assert gate.reason_codes == ("LEGACY_REVIEW_INCOMPLETE",)


@pytest.mark.parametrize("invalid_reference", ["claim_id", "citation_id", "reviewed_at"])
def test_evidence_gate_does_not_count_invalid_full_review(
    valid_packet, valid_brief_draft, invalid_reference: str
) -> None:
    _, selected_claim_ids = _citation_evidence_snapshot(valid_packet, valid_brief_draft)
    review = _citation_review(selected_claim_ids[0])
    if invalid_reference == "claim_id":
        review = review.model_copy(update={"claim_id": "unselected-claim"})
    elif invalid_reference == "citation_id":
        review = review.model_copy(update={"citation_id": "unreferenced-evidence"})
    else:
        review = review.model_copy(
            update={"reviewed_at": datetime(2026, 8, 26, 12, 49, tzinfo=UTC)}
        )
    evidence, _ = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        reviews=(review,),
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert gate.reason_codes == ("CITATION_ENTAILMENT_SAMPLE_INCOMPLETE",)


def test_evidence_gate_fails_closed_when_verified_citation_context_is_invalid(
    valid_packet, valid_brief_draft
) -> None:
    evidence, _ = _citation_evidence_snapshot(
        valid_packet,
        valid_brief_draft,
        invalid_receipt=True,
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _evidence_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert gate.reason_codes == ("CITATION_CONTEXT_INVALID",)


def _failed_normal_provider_evidence(valid_packet, tmp_path, durations: tuple[int, ...] | None):
    import json

    from finance_research_agent.application.performance_telemetry import RunTelemetryRecorder
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )
    from finance_research_agent.domain.enums import DataQualityStatus, ExecutionStatus
    from finance_research_agent.domain.models import RunCheckpoint
    from finance_research_agent.domain.types import FrozenMap

    base_bundle, _ = personal_reduced_publication(valid_packet, tmp_path)
    recorder = RunTelemetryRecorder(monotonic_ns=lambda: 0)
    recorder.record_http_exchange("alpaca", 1, 256)
    telemetry = recorder.snapshot()
    payload = json.dumps(
        telemetry.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    digest = sha256(payload).hexdigest()
    artifact_name = f"performance_telemetry_{digest}"
    market_dates: list[date] = []
    market_date = base_bundle.run.market_date
    required_runs = len(durations) if durations is not None else 1
    while len(market_dates) < required_runs:
        if market_date.weekday() < 5:
            market_dates.append(market_date)
        market_date = date.fromordinal(market_date.toordinal() + 1)

    stored_runs: list[StoredRun] = []
    staged_artifacts: dict[tuple[str, str], bytes] = {}
    for index, run_date in enumerate(market_dates):
        invoked_at = datetime.combine(run_date, time(12, 45), tzinfo=UTC)
        run = base_bundle.run.model_copy(
            update={
                "run_id": f"premarket-{run_date.isoformat()}-r1",
                "market_date": run_date,
                "revision": 1,
                "invoked_at": invoked_at,
                "evidence_cutoff_at": invoked_at,
                "execution_status": (
                    ExecutionStatus.FAILED if durations is not None else ExecutionStatus.ANALYZING
                ),
                "data_quality_status": DataQualityStatus.FAIL,
            }
        )
        terminal_duration = durations[index] if durations is not None else None
        checkpoint = RunCheckpoint(
            run_id=run.run_id,
            stage="FAILED" if terminal_duration is not None else "ANALYZING",
            execution_status=(
                ExecutionStatus.FAILED
                if terminal_duration is not None
                else ExecutionStatus.ANALYZING
            ),
            data_quality_status=DataQualityStatus.FAIL,
            delivery_status=run.delivery_status,
            written_at=(
                invoked_at + timedelta(seconds=terminal_duration)
                if terminal_duration is not None
                else invoked_at + timedelta(minutes=2)
            ),
            evidence_cutoff_at=run.evidence_cutoff_at,
            artifact_hashes=FrozenMap({artifact_name: digest}),
            resumable=terminal_duration is None,
        )
        stored_runs.append(StoredRun(run=run, checkpoints=(checkpoint,)))
        staged_artifacts[(run.run_id, artifact_name)] = payload

    period = ShadowPeriod(start_date=market_dates[0], end_date=market_dates[-1])
    return load_shadow_evaluation_evidence(
        period,
        _EmptyRuns(tuple(stored_runs), staged_artifacts),  # type: ignore[arg-type]
        _EmptyArtifacts(),  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations(()),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )


def test_shadow_period_uses_inclusive_ordered_date_bounds() -> None:
    period = ShadowPeriod(
        start_date=date(2026, 8, 26),
        end_date=date(2026, 8, 28),
    )

    assert period.start_date == date(2026, 8, 26)
    assert period.end_date == date(2026, 8, 28)


def test_shadow_period_rejects_reversed_date_bounds() -> None:
    with pytest.raises(ValidationError, match="start_date must not follow end_date"):
        ShadowPeriod(
            start_date=date(2026, 8, 28),
            end_date=date(2026, 8, 26),
        )


def test_legacy_scorecard_call_cannot_claim_unverified_gates() -> None:
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=None)

    assert tuple(gate.id for gate in scorecard.gate_results) == tuple(ShadowGateId)
    assert {gate.status for gate in scorecard.gate_results} == {"PENDING"}
    assert all(
        "VERIFIED_SHADOW_EVIDENCE_REQUIRED" in gate.reason_codes for gate in scorecard.gate_results
    )
    assert scorecard.failed_gate_ids == ()
    assert scorecard.pending_gate_ids == tuple(ShadowGateId)
    assert scorecard.graduation_ready is False


def _safety_evidence(
    receipts: tuple[ReleaseCheckReceipt, ...],
    observations: tuple[ShadowDayObservation, ...] = (),
    *,
    period: ShadowPeriod | None = None,
    source_commit_sha256: str | None = "a" * 64,
    source_tree_sha256: str | None = "b" * 64,
):
    from finance_research_agent.evaluation.shadow_scorecard import (
        _create_shadow_evaluation_evidence,
    )

    return _create_shadow_evaluation_evidence(
        period=period or ShadowPeriod(start_date=date(2026, 8, 26), end_date=date(2026, 8, 26)),
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        verified_runs=(),
        feedback_records=(),
        observations=observations,
        operator_attestations=(),
        release_check_receipts=receipts,
        source_commit_sha256=source_commit_sha256,
        source_tree_sha256=source_tree_sha256,
        evidence_hashes=("c" * 64,),
    )


def _safety_receipts(
    *,
    conclusion: ReleaseCheckConclusion = ReleaseCheckConclusion.SUCCESS,
    mismatched_source: bool = False,
) -> tuple[ReleaseCheckReceipt, ...]:
    required_checks = (
        ReleaseCheckId.SCENARIO_AND_FAILURE_MATRIX,
        ReleaseCheckId.SECRET_SCAN,
        ReleaseCheckId.NETWORK_BOUNDARY,
    )
    return tuple(
        ReleaseCheckReceipt(
            id=f"{check_id.value.lower()}-{python_version}",
            check_id=check_id,
            source_commit_sha256="d" * 64 if mismatched_source else "a" * 64,
            source_tree_sha256="e" * 64 if mismatched_source else "b" * 64,
            repository_id="finance-research-agent",
            workflow_id="ci",
            actions_run_id="run-123",
            actions_job_id=f"verify-{python_version}",
            actions_step_id=f"{check_id.value.lower()}-step",
            python_version=python_version,
            completed_at=datetime(2026, 8, 26, 12, tzinfo=UTC),
            conclusion=conclusion,
            response_sha256="f" * 64,
        )
        for check_id in required_checks
        for python_version in ("3.12", "3.13", "3.14")
    )


def _safety_gate(scorecard):
    return next(gate for gate in scorecard.gate_results if gate.id is ShadowGateId.SAFETY)


def _failure_evidence(
    observations: tuple[ShadowDayObservation, ...],
    *,
    period: ShadowPeriod | None = None,
):
    from finance_research_agent.evaluation.shadow_scorecard import (
        _create_shadow_evaluation_evidence,
    )

    return _create_shadow_evaluation_evidence(
        period=period or ShadowPeriod(start_date=date(2026, 8, 26), end_date=date(2026, 8, 26)),
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        verified_runs=(),
        feedback_records=(),
        observations=observations,
        operator_attestations=(),
        release_check_receipts=(),
        source_commit_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        evidence_hashes=("c" * 64,),
    )


def _failure_operations_gate(scorecard):
    return next(
        gate for gate in scorecard.gate_results if gate.id is ShadowGateId.FAILURE_OPERATIONS
    )


def _failure_operation(
    *,
    recovery: ShadowFailureRecoveryRecord | None = None,
    status: ShadowFailureOperationStatus = ShadowFailureOperationStatus.RESOLVED,
) -> ShadowFailureOperation:
    return ShadowFailureOperation(
        failure_ref="failure-provider-1",
        failure_code=ErrorCode.PROVIDER_UNAVAILABLE,
        status=status,
        affected_capabilities=(Capability.MARKET_SUMMARY_AVAILABLE,),
        recovery=recovery,
    )


def _actionable_recovery() -> ShadowFailureRecoveryRecord:
    return ShadowFailureRecoveryRecord(
        recovery_ref="recovery-provider-1",
        action="Restore the configured market data connection.",
        validation_plan="Run readiness and confirm a valid daily bar is returned.",
    )


def _failure_day_observation(**changes: object) -> ShadowDayObservation:
    values: dict[str, object] = {
        "id": "shadow-day-failures-1",
        "market_date": date(2026, 8, 26),
        "recorded_at": datetime(2026, 8, 26, 16, tzinfo=UTC),
        "session_id": "session-failures-1",
        "availability": ShadowDayAvailability.ONLINE,
    }
    values.update(changes)
    return ShadowDayObservation(**values)


def test_failure_operations_gate_is_pending_without_known_coverage() -> None:
    evidence = _failure_evidence((_failure_day_observation(),))
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert "FAILURE_OPERATIONS_COVERAGE_UNKNOWN" in gate.reason_codes


def test_failure_operations_gate_is_pending_for_incomplete_coverage() -> None:
    observation = _failure_day_observation(
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.INCOMPLETE,
            failures=(),
        )
    )
    evidence = _failure_evidence((observation,))
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert "FAILURE_OPERATIONS_COVERAGE_INCOMPLETE" in gate.reason_codes


def test_failure_operations_gate_passes_only_with_explicit_complete_zero_coverage() -> None:
    observation = _failure_day_observation(
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(),
        )
    )
    evidence = _failure_evidence((observation,))
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PASS
    assert gate.measured_value == 0
    assert gate.required_value == "all observed failures have an actionable recovery record"


def test_failure_operations_gate_accepts_a_closed_actionable_failure_record() -> None:
    recovery = _actionable_recovery()
    observation = _failure_day_observation(
        production_failure_refs=("failure-provider-1",),
        actionable_recovery_refs=(recovery.recovery_ref,),
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(_failure_operation(recovery=recovery),),
        ),
    )
    evidence = _failure_evidence((observation,))
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PASS
    assert gate.measured_value == 1
    assert "failure-provider-1" in gate.evidence_ids
    assert "recovery-provider-1" in gate.evidence_ids


def test_failure_operations_gate_fails_for_known_failure_without_details_or_recovery() -> None:
    known_but_undocumented = _failure_day_observation(
        production_failure_refs=("failure-provider-1",),
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(),
        ),
    )
    no_recovery = _failure_day_observation(
        id="shadow-day-failures-no-recovery",
        production_failure_refs=("failure-provider-1",),
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(_failure_operation(),),
        ),
    )

    undocumented_scorecard = build_shadow_scorecard(
        (), (), _Calendar(), evidence=_failure_evidence((known_but_undocumented,))
    )
    no_recovery_scorecard = build_shadow_scorecard(
        (), (), _Calendar(), evidence=_failure_evidence((no_recovery,))
    )

    assert _failure_operations_gate(undocumented_scorecard).status is EvaluationGateStatus.FAIL
    assert "FAILURE_OPERATION_DETAILS_MISSING" in _failure_operations_gate(
        undocumented_scorecard
    ).reason_codes
    assert _failure_operations_gate(no_recovery_scorecard).status is EvaluationGateStatus.FAIL
    assert "FAILURE_RECOVERY_NOT_ACTIONABLE" in _failure_operations_gate(
        no_recovery_scorecard
    ).reason_codes


def test_failure_operations_gate_requires_recovery_ref_to_match_actionable_index() -> None:
    recovery = _actionable_recovery()
    observation = _failure_day_observation(
        production_failure_refs=("failure-provider-1",),
        actionable_recovery_refs=(),
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(_failure_operation(recovery=recovery),),
        ),
    )
    evidence = _failure_evidence((observation,))
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert "FAILURE_RECOVERY_REFERENCE_MISSING" in gate.reason_codes


def test_failure_operations_gate_fails_for_recovery_without_action_or_validation() -> None:
    partial_recovery = ShadowFailureRecoveryRecord(
        recovery_ref="recovery-provider-1",
        action=None,
        validation_plan="Run readiness and confirm a valid daily bar is returned.",
    )
    observation = _failure_day_observation(
        production_failure_refs=("failure-provider-1",),
        actionable_recovery_refs=(partial_recovery.recovery_ref,),
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(_failure_operation(recovery=partial_recovery),),
        ),
    )
    evidence = _failure_evidence((observation,))
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert "FAILURE_RECOVERY_NOT_ACTIONABLE" in gate.reason_codes


def test_failure_operations_gate_fails_for_conflicting_details_of_one_failure() -> None:
    recovery = _actionable_recovery()
    first = _failure_day_observation(
        production_failure_refs=("failure-provider-1",),
        actionable_recovery_refs=(recovery.recovery_ref,),
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(_failure_operation(recovery=recovery),),
        ),
    )
    changed_operation = _failure_operation(recovery=recovery).model_copy(
        update={"failure_code": ErrorCode.INVALID_RESPONSE}
    )
    second = _failure_day_observation(
        id="shadow-day-failures-conflict",
        production_failure_refs=("failure-provider-1",),
        actionable_recovery_refs=(recovery.recovery_ref,),
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(changed_operation,),
        ),
    )
    evidence = _failure_evidence((first, second))
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert "CONFLICTING_FAILURE_OPERATION_RECORDS" in gate.reason_codes


def test_failure_operations_gate_allows_status_progression_for_one_failure() -> None:
    recovery = _actionable_recovery()
    first = _failure_day_observation(
        production_failure_refs=("failure-provider-1",),
        actionable_recovery_refs=(recovery.recovery_ref,),
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(
                _failure_operation(
                    recovery=recovery,
                    status=ShadowFailureOperationStatus.OPEN,
                ),
            ),
        ),
    )
    second_date = date(2026, 8, 27)
    second = _failure_day_observation(
        id="shadow-day-failures-resolved",
        market_date=second_date,
        recorded_at=datetime(2026, 8, 27, 16, tzinfo=UTC),
        production_failure_refs=("failure-provider-1",),
        actionable_recovery_refs=(recovery.recovery_ref,),
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(
                _failure_operation(
                    recovery=recovery,
                    status=ShadowFailureOperationStatus.RESOLVED,
                ),
            ),
        ),
    )
    evidence = _failure_evidence(
        (first, second),
        period=ShadowPeriod(start_date=date(2026, 8, 26), end_date=second_date),
    )

    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PASS
    assert gate.reason_codes == ()


def test_failure_operations_gate_fails_when_no_capability_is_identified() -> None:
    recovery = _actionable_recovery()
    operation = _failure_operation(recovery=recovery).model_copy(
        update={"affected_capabilities": ()}
    )
    observation = _failure_day_observation(
        production_failure_refs=("failure-provider-1",),
        actionable_recovery_refs=(recovery.recovery_ref,),
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(operation,),
        ),
    )
    evidence = _failure_evidence((observation,))
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert "FAILURE_OPERATION_CAPABILITY_MISSING" in gate.reason_codes


def test_failure_operations_gate_requires_a_complete_observation_for_each_eligible_date() -> None:
    first_day = _failure_day_observation(
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(),
        )
    )
    evidence = _failure_evidence(
        (first_day,),
        period=ShadowPeriod(start_date=date(2026, 8, 26), end_date=date(2026, 8, 27)),
    )
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert "FAILURE_OPERATIONS_COVERAGE_UNKNOWN" in gate.reason_codes


def test_failure_operations_gate_stays_pending_without_eligible_days() -> None:
    evidence = _failure_evidence(
        (), period=ShadowPeriod(start_date=date(2026, 8, 29), end_date=date(2026, 8, 30))
    )
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert "NO_ELIGIBLE_TRADING_DAYS" in gate.reason_codes


def test_failure_operations_coverage_on_an_excluded_day_is_not_eligible_coverage() -> None:
    excluded = _failure_day_observation(
        availability=ShadowDayAvailability.EXCLUDED,
        exclusion_reason=ShadowDayExclusionReason.COMPUTER_OR_CODEX_UNAVAILABLE,
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.INCOMPLETE,
            failures=(),
        ),
    )
    excluded_without_failure_review = _failure_day_observation(
        id="shadow-day-excluded-without-failure-review",
        availability=ShadowDayAvailability.EXCLUDED,
        exclusion_reason=ShadowDayExclusionReason.COMPUTER_OR_CODEX_UNAVAILABLE,
    )
    evidence = _failure_evidence((excluded, excluded_without_failure_review))
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    gate = _failure_operations_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert "NO_ELIGIBLE_TRADING_DAYS" in gate.reason_codes
    assert "FAILURE_OPERATIONS_COVERAGE_UNKNOWN" not in gate.reason_codes


def test_failure_operations_correction_supersedes_the_prior_day_record() -> None:
    prior = _failure_day_observation(production_failure_refs=("failure-provider-1",))
    corrected = _failure_day_observation(
        id="shadow-day-failures-correction",
        corrects_observation_id=prior.id,
        corrects_observation_sha256="d" * 64,
        failure_operations_observation=ShadowFailureOperationsObservation(
            coverage=ShadowFailureOperationsCoverage.COMPLETE,
            failures=(),
        ),
    )
    evidence = _failure_evidence((prior, corrected))
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=evidence)

    assert _failure_operations_gate(scorecard).status is EvaluationGateStatus.PASS


def _safety_day_observation(
    *,
    source_commit_sha256: str = "a" * 64,
    source_tree_sha256: str = "b" * 64,
    incidents: tuple[ShadowSafetyIncident, ...] = (),
) -> ShadowDayObservation:
    return ShadowDayObservation(
        id="shadow-day-safety-1",
        market_date=date(2026, 8, 26),
        recorded_at=datetime(2026, 8, 26, 16, tzinfo=UTC),
        session_id="session-safety-1",
        availability=ShadowDayAvailability.ONLINE,
        safety_incident_observation=ShadowSafetyIncidentObservation(
            source_commit_sha256=source_commit_sha256,
            source_tree_sha256=source_tree_sha256,
            reviewed_categories=tuple(ShadowSafetyIncidentCategory),
            incidents=incidents,
        ),
    )


def test_safety_gate_requires_version_bound_security_checks_and_incident_observation() -> None:
    missing_checks = build_shadow_scorecard((), (), _Calendar(), evidence=_safety_evidence(()))
    checks_only = build_shadow_scorecard(
        (), (), _Calendar(), evidence=_safety_evidence(_safety_receipts())
    )

    assert _safety_gate(missing_checks).status is EvaluationGateStatus.PENDING
    assert "SAFETY_RELEASE_CHECK_MATRIX_INCOMPLETE" in _safety_gate(missing_checks).reason_codes
    assert _safety_gate(checks_only).status is EvaluationGateStatus.PENDING
    assert "SAFETY_INCIDENT_OBSERVATION_REQUIRED" in _safety_gate(checks_only).reason_codes


@pytest.mark.parametrize(
    ("receipts", "expected_reason"),
    ((_safety_receipts(conclusion=ReleaseCheckConclusion.FAILURE), "SAFETY_RELEASE_CHECK_FAILED"),),
)
def test_safety_gate_fails_when_exact_source_security_check_fails(
    receipts: tuple[ReleaseCheckReceipt, ...], expected_reason: str
) -> None:
    scorecard = build_shadow_scorecard((), (), _Calendar(), evidence=_safety_evidence(receipts))

    gate = _safety_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert expected_reason in gate.reason_codes


def test_safety_gate_waits_for_security_checks_bound_to_evidence_source() -> None:
    evidence = _safety_evidence(_safety_receipts(mismatched_source=True))
    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _safety_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert "SAFETY_RELEASE_CHECK_SOURCE_MISMATCH" in gate.reason_codes


def test_safety_gate_passes_with_exact_checks_and_complete_zero_incident_observation() -> None:
    observation = _safety_day_observation()
    scorecard = build_shadow_scorecard(
        (),
        (),
        _Calendar(),
        evidence=_safety_evidence(_safety_receipts(), observations=(observation,)),
    )

    gate = _safety_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PASS
    assert gate.measured_value == 0
    assert gate.required_value == 0
    assert observation.id in gate.evidence_ids


def test_safety_gate_fails_for_typed_incident_even_when_other_evidence_is_missing() -> None:
    incident = ShadowSafetyIncident(
        id="incident-credential-1",
        category=ShadowSafetyIncidentCategory.CREDENTIAL_LEAK,
        evidence_ref="secret-scan-evidence-1",
        occurred_at=datetime(2026, 8, 26, 14, tzinfo=UTC),
    )
    observation = _safety_day_observation(incidents=(incident,))
    scorecard = build_shadow_scorecard(
        (), (), _Calendar(), evidence=_safety_evidence((), observations=(observation,))
    )

    gate = _safety_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert "SAFETY_INCIDENT_OBSERVED" in gate.reason_codes
    assert incident.id in gate.evidence_ids
    assert incident.evidence_ref in gate.evidence_ids
    assert gate.measured_value == 1


def test_safety_gate_fails_for_incident_on_an_attested_excluded_day() -> None:
    incident = ShadowSafetyIncident(
        id="incident-prohibited-state-1",
        category=ShadowSafetyIncidentCategory.PROHIBITED_STATE,
        evidence_ref="state-audit-evidence-1",
        occurred_at=datetime(2026, 8, 26, 14, tzinfo=UTC),
    )
    online = _safety_day_observation(incidents=(incident,))
    excluded = ShadowDayObservation(
        **(
            online.model_dump()
            | {
                "id": "shadow-day-safety-excluded",
                "availability": ShadowDayAvailability.EXCLUDED,
                "exclusion_reason": ShadowDayExclusionReason.TOTAL_LOCAL_NETWORK_OUTAGE,
            }
        )
    )
    scorecard = build_shadow_scorecard(
        (), (), _Calendar(), evidence=_safety_evidence((), observations=(excluded,))
    )

    gate = _safety_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert "SAFETY_INCIDENT_PROHIBITED_STATE" in gate.reason_codes
    assert gate.measured_value == 1


def test_safety_gate_uses_the_current_corrected_day_record() -> None:
    incident = ShadowSafetyIncident(
        id="incident-corrected-away-1",
        category=ShadowSafetyIncidentCategory.SERIOUS_INJECTION_ESCAPE,
        evidence_ref="injection-review-evidence-1",
        occurred_at=datetime(2026, 8, 26, 14, tzinfo=UTC),
    )
    prior = _safety_day_observation(incidents=(incident,))
    corrected = ShadowDayObservation(
        **(
            prior.model_dump()
            | {
                "id": "shadow-day-safety-corrected",
                "safety_incident_observation": ShadowSafetyIncidentObservation(
                    source_commit_sha256="a" * 64,
                    source_tree_sha256="b" * 64,
                    reviewed_categories=tuple(ShadowSafetyIncidentCategory),
                    incidents=(),
                ),
                "corrects_observation_id": prior.id,
                "corrects_observation_sha256": "c" * 64,
            }
        )
    )
    scorecard = build_shadow_scorecard(
        (),
        (),
        _Calendar(),
        evidence=_safety_evidence(_safety_receipts(), observations=(prior, corrected)),
    )

    gate = _safety_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PASS
    assert corrected.id in gate.evidence_ids
    assert prior.id not in gate.evidence_ids
    assert incident.id not in gate.evidence_ids


@pytest.mark.parametrize(
    ("observations", "expected_reason"),
    (
        ((), "SAFETY_INCIDENT_OBSERVATION_REQUIRED"),
        (
            (_safety_day_observation(source_commit_sha256="d" * 64),),
            "SAFETY_INCIDENT_OBSERVATION_SOURCE_MISMATCH",
        ),
        (
            (_safety_day_observation(source_tree_sha256="e" * 64),),
            "SAFETY_INCIDENT_OBSERVATION_SOURCE_MISMATCH",
        ),
    ),
)
def test_safety_gate_stays_pending_without_exact_source_day_observation(
    observations: tuple[ShadowDayObservation, ...], expected_reason: str
) -> None:
    scorecard = build_shadow_scorecard(
        (), (), _Calendar(), evidence=_safety_evidence(_safety_receipts(), observations)
    )

    gate = _safety_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert expected_reason in gate.reason_codes


def test_safety_gate_requires_source_identity_and_at_least_one_eligible_day() -> None:
    no_source = build_shadow_scorecard(
        (),
        (),
        _Calendar(),
        evidence=_safety_evidence(
            _safety_receipts(),
            (_safety_day_observation(),),
            source_commit_sha256=None,
            source_tree_sha256=None,
        ),
    )
    no_trading_dates = build_shadow_scorecard(
        (),
        (),
        _Calendar(),
        evidence=_safety_evidence(
            _safety_receipts(),
            period=ShadowPeriod(start_date=date(2026, 8, 29), end_date=date(2026, 8, 29)),
        ),
    )

    assert _safety_gate(no_source).status is EvaluationGateStatus.PENDING
    assert "SAFETY_SOURCE_IDENTITY_UNAVAILABLE" in _safety_gate(no_source).reason_codes
    assert _safety_gate(no_trading_dates).status is EvaluationGateStatus.PENDING
    assert "NO_ELIGIBLE_TRADING_DAYS" in _safety_gate(no_trading_dates).reason_codes


def test_safety_gate_checks_each_current_record_for_an_eligible_day() -> None:
    complete = _safety_day_observation()
    incomplete = ShadowDayObservation(
        id="shadow-day-safety-2",
        market_date=complete.market_date,
        recorded_at=complete.recorded_at,
        session_id="session-safety-2",
        availability=ShadowDayAvailability.ONLINE,
    )
    scorecard = build_shadow_scorecard(
        (),
        (),
        _Calendar(),
        evidence=_safety_evidence(_safety_receipts(), observations=(complete, incomplete)),
    )

    gate = _safety_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert "SAFETY_INCIDENT_OBSERVATION_REQUIRED" in gate.reason_codes
    assert {complete.id, incomplete.id} <= set(gate.evidence_ids)


def _deterministic_evidence(verified_runs=(), receipts: tuple[ReleaseCheckReceipt, ...] = ()):
    from finance_research_agent.evaluation.shadow_scorecard import (
        _create_shadow_evaluation_evidence,
    )

    return _create_shadow_evaluation_evidence(
        period=ShadowPeriod(start_date=date(2026, 8, 26), end_date=date(2026, 8, 26)),
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        verified_runs=tuple(verified_runs),
        feedback_records=(),
        observations=(),
        operator_attestations=(),
        release_check_receipts=receipts,
        source_commit_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        evidence_hashes=("c" * 64,),
    )


def _deterministic_receipts(
    *,
    conclusion: ReleaseCheckConclusion = ReleaseCheckConclusion.SUCCESS,
    mismatched_source: bool = False,
) -> tuple[ReleaseCheckReceipt, ...]:
    required_checks = (
        ReleaseCheckId.UNIT_AND_INTEGRATION_TESTS,
        ReleaseCheckId.SCENARIO_AND_FAILURE_MATRIX,
    )
    return tuple(
        ReleaseCheckReceipt(
            id=f"{check_id.value.lower()}-{python_version}",
            check_id=check_id,
            source_commit_sha256="d" * 64 if mismatched_source else "a" * 64,
            source_tree_sha256="e" * 64 if mismatched_source else "b" * 64,
            repository_id="finance-research-agent",
            workflow_id="ci",
            actions_run_id="run-456",
            actions_job_id=f"verify-{python_version}",
            actions_step_id=f"{check_id.value.lower()}-step",
            python_version=python_version,
            completed_at=datetime(2026, 8, 26, 12, tzinfo=UTC),
            conclusion=conclusion,
            response_sha256="f" * 64,
        )
        for check_id in required_checks
        for python_version in ("3.12", "3.13", "3.14")
    )


def _deterministic_gate(scorecard):
    return next(
        gate for gate in scorecard.gate_results if gate.id is ShadowGateId.DETERMINISTIC_CORRECTNESS
    )


def test_deterministic_gate_requires_exact_release_checks_and_same_version_replay(
    valid_packet, valid_brief_draft
) -> None:
    verified = _citation_evidence_snapshot(valid_packet, valid_brief_draft)[0].verified_runs
    missing_checks_evidence = _deterministic_evidence(verified)
    missing_checks = build_shadow_scorecard(
        missing_checks_evidence.published_runs,
        missing_checks_evidence.feedback_records,
        _Calendar(),
        evidence=missing_checks_evidence,
    )
    missing_replay_evidence = _deterministic_evidence((), _deterministic_receipts())
    missing_replay = build_shadow_scorecard(
        missing_replay_evidence.published_runs,
        missing_replay_evidence.feedback_records,
        _Calendar(),
        evidence=missing_replay_evidence,
    )
    complete_evidence = _deterministic_evidence(verified, _deterministic_receipts())
    complete = build_shadow_scorecard(
        complete_evidence.published_runs,
        complete_evidence.feedback_records,
        _Calendar(),
        evidence=complete_evidence,
    )

    assert _deterministic_gate(missing_checks).status is EvaluationGateStatus.PENDING
    assert (
        "DETERMINISTIC_RELEASE_CHECK_MATRIX_INCOMPLETE"
        in _deterministic_gate(missing_checks).reason_codes
    )
    assert _deterministic_gate(missing_replay).status is EvaluationGateStatus.PENDING
    assert (
        "DETERMINISTIC_SAME_VERSION_REPLAY_REQUIRED"
        in _deterministic_gate(missing_replay).reason_codes
    )
    assert _deterministic_gate(complete).status is EvaluationGateStatus.PASS
    assert _deterministic_gate(complete).measured_value == 0
    assert _deterministic_gate(complete).required_value == 0


@pytest.mark.parametrize(
    ("receipts", "expected_reason"),
    (
        (
            _deterministic_receipts(conclusion=ReleaseCheckConclusion.FAILURE),
            "DETERMINISTIC_RELEASE_CHECK_FAILED",
        ),
    ),
)
def test_deterministic_gate_fails_on_failed_or_wrong_source_receipt(
    receipts: tuple[ReleaseCheckReceipt, ...], expected_reason: str
) -> None:
    evidence = _deterministic_evidence((), receipts)
    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _deterministic_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert expected_reason in gate.reason_codes


def test_deterministic_gate_waits_for_receipts_bound_to_evidence_source() -> None:
    evidence = _deterministic_evidence((), _deterministic_receipts(mismatched_source=True))
    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _deterministic_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert "DETERMINISTIC_RELEASE_CHECK_SOURCE_MISMATCH" in gate.reason_codes


def test_deterministic_gate_waits_when_only_version_drifted_artifacts_exist(
    valid_packet, valid_brief_draft
) -> None:
    from dataclasses import replace

    verified = _citation_evidence_snapshot(valid_packet, valid_brief_draft)[0].verified_runs
    original = verified[0]
    assert original.replay_result is not None
    drifted_replay = replace(
        original.replay_result,
        markdown_matches=False,
        replayed_markdown_sha256=None,
        component_version_mismatches=("core_version",),
    )
    drifted_run = replace(original, replay_result=drifted_replay)
    evidence = _deterministic_evidence((drifted_run,), _deterministic_receipts())
    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _deterministic_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert "DETERMINISTIC_SAME_VERSION_REPLAY_REQUIRED" in gate.reason_codes


def test_deterministic_gate_waits_for_missing_numeric_validation_evidence(
    valid_packet, valid_brief_draft
) -> None:
    from dataclasses import replace

    verified = _citation_evidence_snapshot(valid_packet, valid_brief_draft)[0].verified_runs
    original = verified[0]
    assert original.published_bundle is not None and original.replay_result is not None
    contents = original.published_bundle.model_dump(mode="json")["bundle"]
    del contents["validation_report"]
    incomplete_bundle = original.published_bundle.model_copy(update={"bundle": FrozenMap(contents)})
    replay = replace(original.replay_result, bundle=incomplete_bundle)
    incomplete_run = replace(original, published_bundle=incomplete_bundle, replay_result=replay)
    evidence = _deterministic_evidence((incomplete_run,), _deterministic_receipts())
    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _deterministic_gate(scorecard)
    assert gate.status is EvaluationGateStatus.PENDING
    assert "NUMERIC_VALIDATION_EVIDENCE_MISSING" in gate.reason_codes


def test_deterministic_gate_fails_on_same_version_artifact_replay_mismatch(
    valid_packet, valid_brief_draft
) -> None:
    from dataclasses import replace

    verified = _citation_evidence_snapshot(valid_packet, valid_brief_draft)[0].verified_runs
    original = verified[0]
    assert original.replay_result is not None
    bad_replay = replace(
        original.replay_result,
        markdown_matches=False,
        replayed_markdown_sha256="d" * 64,
    )
    bad_run = replace(original, replay_result=bad_replay)
    evidence = _deterministic_evidence((bad_run,), _deterministic_receipts())

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _deterministic_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert "SAME_VERSION_ARTIFACT_REPLAY_MISMATCH" in gate.reason_codes


def test_deterministic_gate_revalidates_published_numeric_claims(
    valid_packet, valid_brief_draft
) -> None:
    from dataclasses import replace

    verified = _citation_evidence_snapshot(valid_packet, valid_brief_draft)[0].verified_runs
    original = verified[0]
    assert original.published_bundle is not None and original.replay_result is not None
    contents = original.published_bundle.model_dump(mode="json")["bundle"]
    draft = dict(contents["brief_draft"])
    claims = [dict(claim) for claim in draft["claims"]]
    numeric_claim = next(claim for claim in claims if claim["claim_id"] == "claim-sma")
    numeric_claim["numeric_value"] = "104.00"
    draft["claims"] = claims
    contents["brief_draft"] = draft
    bad_bundle = original.published_bundle.model_copy(update={"bundle": FrozenMap(contents)})
    replay = replace(original.replay_result, bundle=bad_bundle)
    bad_run = replace(original, published_bundle=bad_bundle, replay_result=replay)
    evidence = _deterministic_evidence((bad_run,), _deterministic_receipts())

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    gate = _deterministic_gate(scorecard)
    assert gate.status is EvaluationGateStatus.FAIL
    assert "PUBLISHED_NUMERIC_OR_CLAIM_VALIDATION_FAILED" in gate.reason_codes


def test_scorecard_contract_excludes_performance_and_execution_fields() -> None:
    forbidden = {
        "realized_profit_loss",
        "return_pct",
        "win_rate",
        "fills",
        "fees",
        "slippage",
        "executed_units",
    }

    assert set(ShadowScorecard.model_fields).isdisjoint(forbidden)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _scorecard(realized_profit_loss="not a scorecard field")


def test_pending_gate_requires_a_reason_code() -> None:
    with pytest.raises(ValidationError, match="requires a reason code"):
        EvaluationGate(id=ShadowGateId.SAFETY, status=EvaluationGateStatus.PENDING)


def test_usefulness_observation_requires_score_or_corrective_work() -> None:
    with pytest.raises(ValidationError, match="requires a score or corrective work"):
        DailyUsefulnessObservation(market_date=date(2026, 8, 26), feedback_id="feedback-1")

    scored = DailyUsefulnessObservation(
        market_date=date(2026, 8, 26), feedback_id="feedback-2", usefulness_score=4
    )
    corrective = DailyUsefulnessObservation(
        market_date=date(2026, 8, 26),
        feedback_id="feedback-3",
        corrective_work="Review the opening-range explanation.",
    )
    assert scored.usefulness_score == 4
    assert corrective.corrective_work == "Review the opening-range explanation."


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"period_start": date(2026, 8, 26)}, "requires both start and end"),
        (
            {"period_start": date(2026, 8, 27), "period_end": date(2026, 8, 26)},
            "must not follow",
        ),
        ({"gate_results": _gates()[:-1]}, "every shadow gate exactly once in order"),
        ({"valid_trading_days": 1}, "cannot exceed eligible_online_days"),
        ({"published_in_target_window": 1}, "cannot exceed eligible_online_days"),
    ),
)
def test_scorecard_rejects_inconsistent_period_gate_order_and_day_counts(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _scorecard(**changes)


def test_scorecard_derives_failure_and_pending_ids_without_marking_ready() -> None:
    gates = list(_gates(EvaluationGateStatus.PASS))
    gates[0] = EvaluationGate(
        id=ShadowGateId.AT_LEAST_TWENTY_TRADING_DAYS,
        status=EvaluationGateStatus.FAIL,
    )
    gates[1] = EvaluationGate(
        id=ShadowGateId.SAFETY,
        status=EvaluationGateStatus.PENDING,
        reason_codes=("missing-incident-evidence",),
    )
    scorecard = _scorecard(
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        valid_trading_days=20,
        eligible_online_days=20,
        gate_results=tuple(gates),
    )

    assert scorecard.failed_gate_ids == (ShadowGateId.AT_LEAST_TWENTY_TRADING_DAYS,)
    assert scorecard.pending_gate_ids == (ShadowGateId.SAFETY,)
    assert scorecard.graduation_ready is False


def test_only_twenty_complete_personal_shadow_days_can_be_ready() -> None:
    complete = _scorecard(
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        valid_trading_days=20,
        eligible_online_days=20,
        gate_results=_gates(EvaluationGateStatus.PASS),
    )
    short = _scorecard(
        origin=ShadowEvaluationOrigin.PERSONAL_SHADOW,
        valid_trading_days=19,
        eligible_online_days=20,
        gate_results=_gates(EvaluationGateStatus.PASS),
    )
    synthetic = _scorecard(
        origin=ShadowEvaluationOrigin.SYNTHETIC_EVALUATION,
        valid_trading_days=20,
        eligible_online_days=20,
        gate_results=_gates(EvaluationGateStatus.PASS),
    )

    assert complete.graduation_ready is True
    assert short.graduation_ready is False
    assert synthetic.graduation_ready is False


def test_legacy_scorecard_period_uses_sorted_unique_published_dates(valid_packet) -> None:
    base_run = valid_packet.run

    def bundle_for(market_date: date) -> PublishedRunBundle:
        values = base_run.model_dump(mode="python")
        values["market_date"] = market_date
        values["run_id"] = f"premarket-{market_date.isoformat()}-r1"
        return PublishedRunBundle(run=RunContext.model_validate(values))

    scorecard = build_shadow_scorecard(
        (
            bundle_for(date(2026, 8, 27)),
            bundle_for(date(2026, 8, 26)),
            bundle_for(date(2026, 8, 27)),
        ),
        (),
        _Calendar(),
    )

    assert scorecard.period_start == date(2026, 8, 26)
    assert scorecard.period_end == date(2026, 8, 27)
    assert scorecard.valid_trading_days == 0


def test_verified_scorecard_counts_eligible_days_and_keeps_unknown_coverage_pending() -> None:
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    period = ShadowPeriod(start_date=date(2026, 10, 5), end_date=date(2026, 10, 7))
    observations = (
        ShadowDayObservation(
            id="online-monday",
            market_date=date(2026, 10, 5),
            recorded_at=datetime(2026, 10, 5, 16, tzinfo=UTC),
            session_id="session-monday",
            availability=ShadowDayAvailability.ONLINE,
        ),
        ShadowDayObservation(
            id="excluded-tuesday",
            market_date=date(2026, 10, 6),
            recorded_at=datetime(2026, 10, 6, 16, tzinfo=UTC),
            session_id="session-tuesday",
            availability=ShadowDayAvailability.EXCLUDED,
            exclusion_reason=ShadowDayExclusionReason.COMPUTER_OR_CODEX_UNAVAILABLE,
        ),
        ShadowDayObservation(
            id="unknown-wednesday",
            market_date=date(2026, 10, 7),
            recorded_at=datetime(2026, 10, 7, 16, tzinfo=UTC),
            session_id="session-wednesday",
            availability=ShadowDayAvailability.UNKNOWN,
        ),
    )
    evidence = load_shadow_evaluation_evidence(
        period,
        _EmptyRuns(),  # type: ignore[arg-type]
        _EmptyArtifacts(),  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations(observations),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert scorecard.period_start == period.start_date
    assert scorecard.period_end == period.end_date
    assert scorecard.eligible_online_days == 2
    assert scorecard.valid_trading_days == 0
    assert scorecard.published_in_target_window == 0
    gates = {gate.id: gate for gate in scorecard.gate_results}
    assert gates[ShadowGateId.AT_LEAST_TWENTY_TRADING_DAYS].status is EvaluationGateStatus.PENDING
    assert gates[ShadowGateId.TIMELY_PUBLICATION].status is EvaluationGateStatus.PENDING
    timely_reasons = gates[ShadowGateId.TIMELY_PUBLICATION].reason_codes
    assert "UNKNOWN_OR_MISSING_DAY_OBSERVATION" in timely_reasons


@pytest.mark.parametrize(
    ("minutes_after_helper_receipt", "expected_local_time", "expected_count", "gate_status"),
    (
        (13, time(9, 0), 1, EvaluationGateStatus.PASS),
        (14, time(9, 1), 0, EvaluationGateStatus.FAIL),
    ),
)
def test_personal_research_publication_obeys_new_york_target_window(
    valid_packet,
    tmp_path,
    monkeypatch,
    minutes_after_helper_receipt: int,
    expected_local_time: time,
    expected_count: int,
    gate_status: EvaluationGateStatus,
) -> None:
    from finance_research_agent.application import shadow_evidence_loader
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    bundle, artifact = personal_reduced_publication(valid_packet, tmp_path)
    artifact = artifact.model_copy(
        update={
            "published_at": artifact.published_at + timedelta(minutes=minutes_after_helper_receipt)
        }
    )
    monkeypatch.setattr(
        shadow_evidence_loader,
        "current_component_versions",
        lambda snapshot: recorded_component_versions(bundle.run),
    )
    period = ShadowPeriod(start_date=bundle.run.market_date, end_date=bundle.run.market_date)
    observation = ShadowDayObservation(
        id="online-publication-day",
        market_date=bundle.run.market_date,
        recorded_at=datetime(2026, 8, 26, 16, tzinfo=UTC),
        session_id="session-publication-day",
        availability=ShadowDayAvailability.ONLINE,
    )
    feedback_record = RecordedFeedback(
        feedback_id="feedback-publication-day",
        run_id=bundle.run.run_id,
        bundle_sha256=artifact.bundle_sha256,
        recorded_at=datetime(2026, 8, 26, 16, tzinfo=UTC),
        clarity_score=4,
        evidence_score=4,
        usefulness_score=3,
        citation_reviews=(),
    )
    runs = _EmptyRuns((StoredRun(run=bundle.run, published=True),))
    artifacts = _EmptyArtifacts()
    artifacts.bundle = bundle
    artifacts.report = bundle.report_markdown
    artifacts.artifact = artifact
    evidence = load_shadow_evaluation_evidence(
        period,
        runs,  # type: ignore[arg-type]
        artifacts,  # type: ignore[arg-type]
        _EmptyFeedback((feedback_record,)),  # type: ignore[arg-type]
        _EmptyObservations((observation,)),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert (
        artifact.published_at.astimezone(ZoneInfo("America/New_York")).time() == expected_local_time
    )
    assert scorecard.origin is ShadowEvaluationOrigin.PERSONAL_SHADOW
    assert scorecard.eligible_online_days == 1
    assert scorecard.valid_trading_days == 1
    assert scorecard.published_in_target_window == expected_count
    timely_gate = next(
        gate for gate in scorecard.gate_results if gate.id is ShadowGateId.TIMELY_PUBLICATION
    )
    assert timely_gate.status is gate_status
    assert timely_gate.measured_value == expected_count
    changed_bundle = bundle.model_copy(
        update={"report_markdown": bundle.report_markdown + " changed"}
    )
    with pytest.raises(ValueError, match="published runs must exactly match verified evidence"):
        build_shadow_scorecard(
            (changed_bundle,), evidence.feedback_records, _Calendar(), evidence=evidence
        )
    with pytest.raises(ValueError, match="published runs must exactly match verified evidence"):
        build_shadow_scorecard((), evidence.feedback_records, _Calendar(), evidence=evidence)
    with pytest.raises(ValueError, match="published run identifiers must be unique"):
        build_shadow_scorecard(
            (bundle, bundle), evidence.feedback_records, _Calendar(), evidence=evidence
        )
    changed_feedback = evidence.feedback_records[0].model_copy(update={"usefulness_score": 4})
    with pytest.raises(ValueError, match="feedback must exactly match verified evidence"):
        build_shadow_scorecard(
            evidence.published_runs,
            (changed_feedback,),
            _Calendar(),
            evidence=evidence,
        )
    with pytest.raises(ValueError, match="feedback must exactly match verified evidence"):
        build_shadow_scorecard(evidence.published_runs, (), _Calendar(), evidence=evidence)
    with pytest.raises(ValueError, match="feedback identifiers must be unique"):
        build_shadow_scorecard(
            evidence.published_runs,
            (feedback_record, feedback_record),
            _Calendar(),
            evidence=evidence,
        )


def test_multiple_revisions_count_only_earliest_daily_research_publication(
    valid_packet, tmp_path, monkeypatch
) -> None:
    from finance_research_agent.application import shadow_evidence_loader
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    first_bundle, first_artifact = personal_reduced_publication(valid_packet, tmp_path)
    second_run = valid_packet.run.model_copy(
        update={
            "run_id": "premarket-2026-08-26-r2",
            "revision": 2,
        }
    )
    second_packet = valid_packet.model_copy(update={"run": second_run})
    second_bundle, second_artifact = personal_reduced_publication(second_packet, tmp_path)
    second_artifact = second_artifact.model_copy(
        update={"published_at": second_artifact.published_at + timedelta(minutes=14)}
    )
    monkeypatch.setattr(
        shadow_evidence_loader,
        "current_component_versions",
        lambda snapshot: recorded_component_versions(first_bundle.run),
    )
    stored_runs = (
        StoredRun(run=first_bundle.run, published=True),
        StoredRun(run=second_bundle.run, published=True),
    )
    observation = ShadowDayObservation(
        id="online-revision-day",
        market_date=first_bundle.run.market_date,
        recorded_at=datetime(2026, 8, 26, 16, tzinfo=UTC),
        session_id="session-revision-day",
        availability=ShadowDayAvailability.ONLINE,
        observed_run_ids=(first_bundle.run.run_id, second_bundle.run.run_id),
    )
    evidence = load_shadow_evaluation_evidence(
        ShadowPeriod(
            start_date=first_bundle.run.market_date,
            end_date=first_bundle.run.market_date,
        ),
        _EmptyRuns(stored_runs),  # type: ignore[arg-type]
        _MultipleArtifacts(
            (first_bundle, second_bundle),
            (first_artifact, second_artifact),
        ),  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations((observation,)),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert scorecard.valid_trading_days == 1
    assert scorecard.published_in_target_window == 1
    timely_gate = next(
        gate for gate in scorecard.gate_results if gate.id is ShadowGateId.TIMELY_PUBLICATION
    )
    assert timely_gate.status is EvaluationGateStatus.PASS


def test_verified_scorecard_can_end_on_last_representable_calendar_date() -> None:
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    period = ShadowPeriod(start_date=date.max, end_date=date.max)
    evidence = load_shadow_evaluation_evidence(
        period,
        _EmptyRuns(),  # type: ignore[arg-type]
        _EmptyArtifacts(),  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations(()),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert scorecard.period_end == date.max
    assert scorecard.eligible_online_days == 1


def test_verified_scorecard_uses_latest_hash_linked_exclusion_correction() -> None:
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    period = ShadowPeriod(start_date=date(2026, 10, 5), end_date=date(2026, 10, 5))
    original = ShadowDayObservation(
        id="online-before-correction",
        market_date=date(2026, 10, 5),
        recorded_at=datetime(2026, 10, 5, 15, tzinfo=UTC),
        session_id="session-corrected",
        availability=ShadowDayAvailability.ONLINE,
    )
    correction = ShadowDayObservation(
        id="exclusion-correction",
        market_date=date(2026, 10, 5),
        recorded_at=datetime(2026, 10, 5, 16, tzinfo=UTC),
        session_id="session-corrected",
        availability=ShadowDayAvailability.EXCLUDED,
        exclusion_reason=ShadowDayExclusionReason.TOTAL_LOCAL_NETWORK_OUTAGE,
        corrects_observation_id=original.id,
        corrects_observation_sha256=sha256(canonical_bytes(original)).hexdigest(),
    )
    evidence = load_shadow_evaluation_evidence(
        period,
        _EmptyRuns(),  # type: ignore[arg-type]
        _EmptyArtifacts(),  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations((original, correction)),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert scorecard.eligible_online_days == 0
    timely_gate = next(
        gate for gate in scorecard.gate_results if gate.id is ShadowGateId.TIMELY_PUBLICATION
    )
    assert timely_gate.status is EvaluationGateStatus.PENDING
    assert timely_gate.reason_codes == ("NO_ELIGIBLE_TRADING_DAYS",)


def test_verified_scorecard_rejects_publication_not_in_loader_snapshot(valid_packet) -> None:
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    period = ShadowPeriod(start_date=date(2026, 8, 26), end_date=date(2026, 8, 26))
    evidence = load_shadow_evaluation_evidence(
        period,
        _EmptyRuns(),  # type: ignore[arg-type]
        _EmptyArtifacts(),  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations(()),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )
    unverified_bundle = PublishedRunBundle(run=valid_packet.run)

    with pytest.raises(ValueError, match="published runs must exactly match verified evidence"):
        build_shadow_scorecard(
            (unverified_bundle,),
            (),
            _Calendar(),
            evidence=evidence,
        )


def test_verified_scorecard_rejects_feedback_not_in_loader_snapshot(valid_packet) -> None:
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )

    period = ShadowPeriod(start_date=date(2026, 8, 26), end_date=date(2026, 8, 26))
    evidence = load_shadow_evaluation_evidence(
        period,
        _EmptyRuns(),  # type: ignore[arg-type]
        _EmptyArtifacts(),  # type: ignore[arg-type]
        _EmptyFeedback(),  # type: ignore[arg-type]
        _EmptyObservations(()),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )
    unverified_feedback = RecordedFeedback(
        feedback_id="feedback-unverified",
        run_id=valid_packet.run.run_id,
        bundle_sha256="a" * 64,
        recorded_at=datetime(2026, 8, 26, 16, tzinfo=UTC),
        clarity_score=3,
        evidence_score=3,
        usefulness_score=3,
        citation_reviews=(),
    )

    with pytest.raises(ValueError, match="feedback must exactly match verified evidence"):
        build_shadow_scorecard(
            (),
            (unverified_feedback,),
            _Calendar(),
            evidence=evidence,
        )


@pytest.mark.parametrize(
    ("durations", "expected_status", "expected_p95"),
    (
        ((900,) * 19 + (901,), EvaluationGateStatus.PASS, 900),
        ((900,) * 18 + (901, 901), EvaluationGateStatus.FAIL, 901),
    ),
)
def test_verified_scorecard_uses_nearest_rank_normal_provider_duration_p95(
    valid_packet,
    tmp_path,
    durations: tuple[int, ...],
    expected_status: EvaluationGateStatus,
    expected_p95: int,
) -> None:
    evidence = _failed_normal_provider_evidence(valid_packet, tmp_path, durations)

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert len(scorecard.duration_observations) == 20
    gate = next(
        result
        for result in scorecard.gate_results
        if result.id is ShadowGateId.NORMAL_PROVIDER_DURATION
    )
    assert gate.status is expected_status
    assert gate.measured_value == expected_p95
    assert gate.required_value == 900


def test_normal_provider_duration_gate_is_pending_without_a_terminal_checkpoint(
    valid_packet, tmp_path
) -> None:
    evidence = _failed_normal_provider_evidence(valid_packet, tmp_path, None)

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    assert scorecard.duration_observations == ()
    gate = next(
        result
        for result in scorecard.gate_results
        if result.id is ShadowGateId.NORMAL_PROVIDER_DURATION
    )
    assert gate.status is EvaluationGateStatus.PENDING
    assert gate.reason_codes == ("NORMAL_PROVIDER_TERMINAL_EVIDENCE_MISSING",)


@pytest.mark.parametrize(
    (
        "later_identified_posture",
        "day_identified_posture",
        "feedback_identification_present",
        "expected_identification_status",
    ),
    (
        ("unknown", "unknown", True, EvaluationGateStatus.PASS),
        ("neutral", "unknown", True, EvaluationGateStatus.FAIL),
        ("unknown", None, True, EvaluationGateStatus.PASS),
        ("unknown", "neutral", True, EvaluationGateStatus.FAIL),
        ("unknown", "absent", True, EvaluationGateStatus.PASS),
        ("unknown", "absent", False, EvaluationGateStatus.PENDING),
        ("unknown", "unknown", False, EvaluationGateStatus.PASS),
    ),
)
def test_daily_metrics_and_identification_use_selected_complete_feedback(
    valid_packet,
    tmp_path,
    monkeypatch,
    later_identified_posture,
    day_identified_posture,
    feedback_identification_present,
    expected_identification_status,
) -> None:
    from finance_research_agent.application import shadow_evidence_loader
    from finance_research_agent.application.operations import (
        ExecutiveEventState,
        ExecutiveIdentification,
    )
    from finance_research_agent.application.shadow_evidence_loader import (
        load_shadow_evaluation_evidence,
    )
    from finance_research_agent.domain.enums import Capability
    from finance_research_agent.domain.errors import ErrorCode
    from finance_research_agent.domain.models import CapabilityState
    from finance_research_agent.domain.regime import Regime
    from finance_research_agent.evaluation.shadow_records import ShadowExecutiveEventState

    packet = valid_packet.model_copy(
        update={
            "capability_states": (
                CapabilityState(
                    capability=Capability.EVENT_RISK_CHECK_AVAILABLE,
                    available=False,
                    reason_codes=(ErrorCode.PROVIDER_UNAVAILABLE,),
                    evidence_ids=(),
                ),
            )
        }
    )
    bundle, artifact = personal_reduced_publication(packet, tmp_path)
    monkeypatch.setattr(
        shadow_evidence_loader,
        "current_component_versions",
        lambda snapshot: recorded_component_versions(bundle.run),
    )
    identification = ExecutiveIdentification(
        market_posture=Regime.UNKNOWN,
        event_state=ExecutiveEventState.UNAVAILABLE,
        event_ids=(),
        priority_symbols=(),
        disabled_capabilities=(Capability.EVENT_RISK_CHECK_AVAILABLE,),
    )
    later_identification = identification.model_copy(
        update={"market_posture": Regime(later_identified_posture)}
    )
    day_identification = None
    if day_identified_posture not in (None, "absent"):
        day_identification = ShadowExecutiveIdentification(
            market_posture=identification.market_posture,
            event_state=ShadowExecutiveEventState(identification.event_state.value),
            event_ids=identification.event_ids,
            priority_symbols=identification.priority_symbols,
            disabled_capabilities=identification.disabled_capabilities,
        )
        day_identification = day_identification.model_copy(
            update={"market_posture": Regime(day_identified_posture)}
        )
    common_feedback = {
        "run_id": bundle.run.run_id,
        "bundle_sha256": artifact.bundle_sha256,
        "recorded_at": datetime(2026, 8, 26, 13, 30, tzinfo=UTC),
        "clarity_score": 4,
        "evidence_score": 4,
        "citation_reviews": (),
        "schema_version": "0.2",
        "executive_identification": (identification if feedback_identification_present else None),
    }
    later_full_feedback = RecordedFeedback(
        **(
            common_feedback
            | {
                "feedback_id": "feedback-z",
                "recorded_at": datetime(2026, 8, 26, 14, tzinfo=UTC),
                "usefulness_score": 1,
                "executive_review_duration_seconds": 300,
                "detailed_review_duration_seconds": 1200,
                "executive_identification": (
                    later_identification if feedback_identification_present else None
                ),
            }
        )
    )
    selected_full_feedback = RecordedFeedback(
        **(
            common_feedback
            | {
                "feedback_id": "feedback-a",
                "usefulness_score": 4,
                "executive_review_duration_seconds": 120,
                "detailed_review_duration_seconds": 900,
            }
        )
    )
    earlier_partial_feedback = RecordedFeedback(
        **(
            common_feedback
            | {
                "feedback_id": "feedback-partial",
                "recorded_at": datetime(2026, 8, 26, 13, tzinfo=UTC),
                "schema_version": "0.1",
                "executive_identification": None,
                "usefulness_score": 5,
            }
        )
    )
    observation = ShadowDayObservation(
        id="online-review-day",
        market_date=bundle.run.market_date,
        recorded_at=datetime(2026, 8, 26, 16, tzinfo=UTC),
        session_id="session-review-day",
        availability=ShadowDayAvailability.ONLINE,
        observed_run_ids=(bundle.run.run_id,),
        observed_feedback_ids=(
            earlier_partial_feedback.feedback_id,
            later_full_feedback.feedback_id,
            selected_full_feedback.feedback_id,
        ),
        executive_identification=day_identification,
    )
    runs = _EmptyRuns((StoredRun(run=bundle.run, published=True),))
    artifacts = _EmptyArtifacts()
    artifacts.bundle = bundle
    artifacts.report = bundle.report_markdown
    artifacts.artifact = artifact
    evidence = load_shadow_evaluation_evidence(
        ShadowPeriod(start_date=bundle.run.market_date, end_date=bundle.run.market_date),
        runs,  # type: ignore[arg-type]
        artifacts,  # type: ignore[arg-type]
        _EmptyFeedback((later_full_feedback, earlier_partial_feedback, selected_full_feedback)),  # type: ignore[arg-type]
        _EmptyObservations(() if day_identified_posture == "absent" else (observation,)),  # type: ignore[arg-type]
        _EmptyAttestations(),  # type: ignore[arg-type]
        _EmptyReleaseEvidence(),  # type: ignore[arg-type]
    )

    scorecard = build_shadow_scorecard(
        evidence.published_runs,
        evidence.feedback_records,
        _Calendar(),
        evidence=evidence,
    )

    if feedback_identification_present:
        assert scorecard.review_time_observations == (
            DailyReviewTimeObservation(
                market_date=bundle.run.market_date,
                feedback_id="feedback-a",
                executive_review_duration_seconds=120,
                detailed_review_duration_seconds=900,
            ),
        )
        assert scorecard.usefulness_observations == (
            DailyUsefulnessObservation(
                market_date=bundle.run.market_date,
                feedback_id="feedback-a",
                usefulness_score=4,
            ),
        )
    else:
        assert scorecard.review_time_observations == ()
        assert scorecard.usefulness_observations == ()
    gates = {gate.id: gate for gate in scorecard.gate_results}
    if feedback_identification_present:
        assert gates[ShadowGateId.REVIEW_TIME].status is EvaluationGateStatus.PASS
        assert gates[ShadowGateId.USEFULNESS].status is EvaluationGateStatus.PASS
    assert gates[ShadowGateId.EXECUTIVE_IDENTIFICATION].status is expected_identification_status
    if expected_identification_status is EvaluationGateStatus.FAIL:
        assert gates[ShadowGateId.EXECUTIVE_IDENTIFICATION].reason_codes == (
            "EXECUTIVE_IDENTIFICATION_MISMATCH",
        )
    expected_daily_feedback_ids = {
        earlier_partial_feedback.feedback_id,
        later_full_feedback.feedback_id,
        selected_full_feedback.feedback_id,
    }
    assert expected_daily_feedback_ids.issubset(gates[ShadowGateId.REVIEW_TIME].evidence_ids)
    assert expected_daily_feedback_ids.issubset(gates[ShadowGateId.USEFULNESS].evidence_ids)


def test_unavailable_event_capability_suppresses_retained_event_identifications(
    valid_packet, tmp_path
) -> None:
    from finance_research_agent.domain.enums import Capability
    from finance_research_agent.domain.errors import ErrorCode
    from finance_research_agent.domain.models import CapabilityState
    from finance_research_agent.evaluation.shadow_records import ShadowExecutiveEventState

    packet = valid_packet.model_copy(
        update={
            "events": (
                EventRecord(
                    event_id="event-retained",
                    event_type="EARNINGS",
                    subject_symbol="AAPL",
                    event_time=valid_packet.run.evidence_cutoff_at,
                    verified=True,
                    materiality="HIGH",
                    supporting_evidence_ids=("evidence-00",),
                    conflict_evidence_ids=(),
                ),
            ),
            "capability_states": (
                CapabilityState(
                    capability=Capability.EVENT_RISK_CHECK_AVAILABLE,
                    available=False,
                    reason_codes=(ErrorCode.PROVIDER_UNAVAILABLE,),
                    evidence_ids=(),
                ),
            ),
        }
    )
    bundle, _ = personal_reduced_publication(packet, tmp_path)

    expected = _expected_executive_identification(bundle)

    assert expected is not None
    assert expected.event_state is ShadowExecutiveEventState.UNAVAILABLE
    assert expected.event_ids == ()


@pytest.mark.parametrize(
    ("materiality", "expected_state", "expected_ids"),
    (
        (
            "HIGH",
            "MATERIAL_EVENTS",
            ("event-material",),
        ),
        (
            "LOW",
            "NO_MATERIAL_EVENTS",
            (),
        ),
        (
            None,
            "NO_MATERIAL_EVENTS",
            (),
        ),
    ),
)
def test_available_event_capability_derives_material_event_identification(
    valid_packet, tmp_path, materiality, expected_state, expected_ids
) -> None:
    from finance_research_agent.domain.enums import Capability
    from finance_research_agent.domain.models import CapabilityState
    from finance_research_agent.evaluation.shadow_records import ShadowExecutiveEventState

    events = (
        (
            EventRecord(
                event_id="event-material",
                event_type="EARNINGS",
                subject_symbol="AAPL",
                event_time=valid_packet.run.evidence_cutoff_at,
                verified=True,
                materiality=materiality,
                supporting_evidence_ids=("evidence-00",),
                conflict_evidence_ids=(),
            ),
        )
        if materiality is not None
        else ()
    )
    packet = valid_packet.model_copy(
        update={
            "events": events,
            "capability_states": (
                CapabilityState(
                    capability=Capability.EVENT_RISK_CHECK_AVAILABLE,
                    available=True,
                    reason_codes=(),
                    evidence_ids=(),
                ),
            ),
        }
    )
    bundle, _ = personal_reduced_publication(packet, tmp_path)

    expected = _expected_executive_identification(bundle)

    assert expected is not None
    assert expected.event_state is ShadowExecutiveEventState(expected_state)
    assert expected.event_ids == expected_ids


@pytest.mark.parametrize("packet_state", ("missing", "invalid", "without_event_capability"))
def test_identification_context_is_unavailable_without_a_valid_event_capability_packet(
    valid_packet, tmp_path, packet_state
) -> None:
    bundle, _ = personal_reduced_publication(valid_packet, tmp_path)
    if packet_state == "missing":
        bundle = bundle.model_copy(update={"bundle": FrozenMap({})})
    elif packet_state == "invalid":
        bundle = bundle.model_copy(
            update={"bundle": FrozenMap({"research_packet": {"not_a_packet": True}})}
        )

    assert _expected_executive_identification(bundle) is None


def test_daily_review_time_uses_exact_even_count_medians_and_fails_over_limit() -> None:
    first_day = date(2026, 8, 26)
    second_day = date(2026, 8, 27)
    first = _metric_feedback(
        "feedback-day-one",
        usefulness_score=4,
        executive_seconds=120,
        detailed_seconds=900,
    )
    second = _metric_feedback(
        "feedback-day-two",
        usefulness_score=4,
        executive_seconds=181,
        detailed_seconds=901,
    )

    observations, gate = _daily_review_time_gate(
        (first_day, second_day),
        {first_day: first, second_day: second},
    )

    assert _median((120, 181)) == Decimal("150.5")
    assert _median((900, 901)) == Decimal("900.5")
    assert len(observations) == 2
    assert gate.status is EvaluationGateStatus.FAIL
    assert gate.measured_value == "executive=150.5;detailed=900.5"
    assert gate.reason_codes == ("REVIEW_TIME_LIMIT_EXCEEDED",)


@pytest.mark.parametrize(
    ("feedback_record", "expected_status", "expected_reason"),
    (
        (
            _metric_feedback(
                "feedback-low-no-note",
                usefulness_score=3,
                executive_seconds=60,
                detailed_seconds=300,
            ),
            EvaluationGateStatus.FAIL,
            "AVERAGE_USEFULNESS_BELOW_4",
        ),
        (
            _metric_feedback(
                "feedback-low-with-notes",
                usefulness_score=3,
                executive_seconds=60,
                detailed_seconds=300,
                notes="The opening rationale is unclear and needs review.",
            ),
            EvaluationGateStatus.PENDING,
            "CORRECTIVE_WORK_REQUIRES_VALIDATION",
        ),
    ),
)
def test_daily_usefulness_does_not_treat_notes_as_validated_correction(
    feedback_record: RecordedFeedback,
    expected_status: EvaluationGateStatus,
    expected_reason: str,
) -> None:
    observations, gate = _daily_usefulness_gate(
        (date(2026, 8, 26),),
        {date(2026, 8, 26): feedback_record},
    )

    assert observations[0].feedback_id == feedback_record.feedback_id
    assert gate.status is expected_status
    assert gate.reason_codes == (expected_reason,)


def test_daily_usefulness_preserves_full_contract_length_feedback_notes() -> None:
    feedback_record = _metric_feedback(
        "feedback-long-note",
        usefulness_score=3,
        executive_seconds=60,
        detailed_seconds=300,
        notes="x" * 501,
    )

    observations, gate = _daily_usefulness_gate(
        (date(2026, 8, 26),),
        {date(2026, 8, 26): feedback_record},
    )

    assert observations[0].corrective_work == feedback_record.notes
    assert gate.status is EvaluationGateStatus.PENDING
    assert gate.reason_codes == ("CORRECTIVE_WORK_REQUIRES_VALIDATION",)
