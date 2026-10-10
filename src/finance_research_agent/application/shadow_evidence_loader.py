"""Load one immutable shadow evidence snapshot from fixed application ports."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from pydantic import BaseModel

from finance_research_agent.application.component_versions import current_component_versions
from finance_research_agent.application.feedback_service import RecordedFeedback
from finance_research_agent.application.ports import (
    FeedbackRepository,
    OperatorReleaseAttestationRepository,
    PublishedArtifactReader,
    ReleaseEvidenceReader,
    RunRepository,
    ShadowObservationRepository,
)
from finance_research_agent.application.replay_service import replay_published_artifact
from finance_research_agent.domain.models import StoredRun
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    OperatorReleaseAttestation,
    ReleaseCheckReceipt,
    ShadowDayObservation,
)
from finance_research_agent.evaluation.shadow_scorecard import (
    ShadowEvaluationEvidence,
    ShadowPeriod,
    VerifiedShadowRunEvidence,
    _create_shadow_evaluation_evidence,
)


def _require_unique_ids(records: Iterable[object], attribute: str, record_kind: str) -> None:
    identifiers = tuple(getattr(record, attribute) for record in records)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{record_kind} identifiers must be unique")


def _content_hashes(records: Iterable[BaseModel]) -> tuple[str, ...]:
    return tuple(sorted({sha256(canonical_bytes(record)).hexdigest() for record in records}))


def load_shadow_evaluation_evidence(
    period: ShadowPeriod,
    runs: RunRepository,
    artifacts: PublishedArtifactReader,
    feedback: FeedbackRepository,
    observations: ShadowObservationRepository,
    operator_attestations: OperatorReleaseAttestationRepository,
    release_evidence: ReleaseEvidenceReader,
) -> ShadowEvaluationEvidence:
    """Snapshot period evidence without allowing caller-supplied gate decisions."""
    period = ShadowPeriod.model_validate(period, strict=True)
    stored_runs = runs.list_runs(period.start_date, period.end_date)
    if type(stored_runs) is not tuple or any(
        not isinstance(record, StoredRun) for record in stored_runs
    ):
        raise TypeError("run repository must return an immutable tuple of StoredRun values")
    if any(
        not period.start_date <= record.run.market_date <= period.end_date for record in stored_runs
    ):
        raise ValueError("run repository returned a run outside the selected shadow period")
    _require_unique_ids(stored_runs, "run_id", "run")
    stored_runs = tuple(
        sorted(stored_runs, key=lambda item: (item.run.market_date, item.run.revision, item.run_id))
    )
    verified_run_values: list[VerifiedShadowRunEvidence] = []
    run_hash_records: list[BaseModel] = []
    for stored_run in stored_runs:
        run_hash_records.append(stored_run)
        if not stored_run.published:
            if artifacts.get_published_artifact(stored_run.run_id) is not None:
                raise ValueError("unpublished run has an indexed publication receipt")
            verified_run_values.append(VerifiedShadowRunEvidence(stored_run=stored_run))
            continue
        replay = replay_published_artifact(
            artifacts,
            stored_run.run_id,
            current_component_versions(stored_run.run.configuration_snapshot),
        )
        artifact = artifacts.get_published_artifact(stored_run.run_id)
        if artifact is None:
            raise ValueError("published run has no indexed publication receipt")
        if stored_run.run != replay.bundle.run:
            raise ValueError("stored run context differs from the indexed publication")
        run_hash_records.extend((replay.bundle, artifact))
        verified_run_values.append(
            VerifiedShadowRunEvidence(
                stored_run=stored_run,
                published_bundle=replay.bundle,
                published_artifact=artifact,
                replay_result=replay,
            )
        )
    verified_runs = tuple(verified_run_values)

    feedback_records = feedback.list_feedback()
    day_observations = observations.list_observations()
    attestations = operator_attestations.list_attestations()
    release_receipts = release_evidence.list_receipts()
    for records, record_type, record_kind in (
        (feedback_records, RecordedFeedback, "feedback"),
        (day_observations, ShadowDayObservation, "shadow observation"),
        (attestations, OperatorReleaseAttestation, "operator attestation"),
        (release_receipts, ReleaseCheckReceipt, "release receipt"),
    ):
        if type(records) is not tuple or any(not isinstance(item, record_type) for item in records):
            raise TypeError(f"{record_kind} repository must return an immutable typed tuple")
        identifier_field = "feedback_id" if record_type is RecordedFeedback else "id"
        _require_unique_ids(records, identifier_field, record_kind)

    period_observations = tuple(
        sorted(
            (
                item
                for item in day_observations
                if period.start_date <= item.market_date <= period.end_date
            ),
            key=lambda item: (item.market_date, item.recorded_at, item.id),
        )
    )
    source_identities = {
        (item.source_commit_sha256, item.source_tree_sha256) for item in release_receipts
    }
    if len(source_identities) > 1:
        raise ValueError("release receipts must bind one exact source commit and tree")
    source_commit, source_tree = next(iter(source_identities), (None, None))
    content_records = (
        *run_hash_records,
        *feedback_records,
        *period_observations,
        *attestations,
        *release_receipts,
    )
    return _create_shadow_evaluation_evidence(
        period=period,
        origin=None,
        verified_runs=verified_runs,
        feedback_records=tuple(
            sorted(feedback_records, key=lambda item: (item.recorded_at, item.feedback_id))
        ),
        observations=period_observations,
        operator_attestations=tuple(
            sorted(attestations, key=lambda item: (item.recorded_at, item.id))
        ),
        release_check_receipts=tuple(
            sorted(
                release_receipts,
                key=lambda item: (item.check_id, item.python_version, item.id),
            )
        ),
        source_commit_sha256=source_commit,
        source_tree_sha256=source_tree,
        evidence_hashes=_content_hashes(content_records),
    )


__all__ = ["load_shadow_evaluation_evidence"]
