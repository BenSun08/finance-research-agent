"""Load one immutable shadow evidence snapshot from fixed application ports."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from hashlib import sha256
from math import ceil

from pydantic import BaseModel

from finance_research_agent.application.component_versions import current_component_versions
from finance_research_agent.application.config_service import configuration_from_snapshot
from finance_research_agent.application.feedback_service import RecordedFeedback
from finance_research_agent.application.performance_telemetry import (
    load_latest_checkpoint_telemetry,
)
from finance_research_agent.application.ports import (
    FeedbackRepository,
    OperatorReleaseAttestationRepository,
    PublishedArtifactReader,
    ReleaseEvidenceReader,
    RunRepository,
    ShadowObservationRepository,
)
from finance_research_agent.application.replay_service import (
    ArtifactReplayResult,
    replay_published_artifact,
)
from finance_research_agent.domain.enums import BriefOrigin, ExecutionStatus, SourceRole
from finance_research_agent.domain.models import (
    PerformanceTelemetry,
    PublishedArtifact,
    PublishedRunBundle,
    RunContext,
    StoredRun,
)
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    OperatorReleaseAttestation,
    ReleaseCheckReceipt,
    ShadowDayObservation,
)
from finance_research_agent.evaluation.shadow_scorecard import (
    ShadowEvaluationEvidence,
    ShadowEvaluationOrigin,
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


def _validate_observation_corrections(
    observations: tuple[ShadowDayObservation, ...],
) -> None:
    by_id = {item.id: item for item in observations}
    for correction in observations:
        target_id = correction.corrects_observation_id
        if target_id is None:
            continue
        previous = by_id.get(target_id)
        if previous is None:
            raise ValueError("observation correction references an unknown prior record")
        expected_digest = sha256(canonical_bytes(previous)).hexdigest()
        if correction.corrects_observation_sha256 != expected_digest:
            raise ValueError("observation correction digest does not match the prior record")
        if correction.market_date != previous.market_date:
            raise ValueError("observation correction changed the market date")
        if correction.recorded_at <= previous.recorded_at:
            raise ValueError("observation correction must follow its prior record")


def _source_scope_is_verified(run: RunContext) -> bool | None:
    snapshot = run.configuration_snapshot
    if snapshot.policies is None:
        return None
    try:
        configuration = configuration_from_snapshot(snapshot)
    except (KeyError, TypeError, ValueError):
        raise ValueError("run has an invalid frozen source-policy snapshot") from None
    expected_roles = {SourceRole.MARKET_DATA, SourceRole.MARKET_CALENDAR}
    if set(configuration.source.quality_source_roles) != expected_roles:
        return None
    return True


def _verified_run_telemetry(
    runs: RunRepository,
    stored_run: StoredRun,
    replay_telemetry: PerformanceTelemetry | None,
) -> PerformanceTelemetry | None:
    checkpoint_telemetry = load_latest_checkpoint_telemetry(runs, stored_run)
    if (
        stored_run.published
        and checkpoint_telemetry is not None
        and replay_telemetry != checkpoint_telemetry
    ):
        raise ValueError("published telemetry differs from its terminal checkpoint")
    return replay_telemetry or checkpoint_telemetry


def _normal_provider_attempt(
    run: RunContext, telemetry: PerformanceTelemetry | None
) -> bool | None:
    if _source_scope_is_verified(run) is not True or telemetry is None:
        return None
    expected_providers = {"alpaca", "market-calendar"}
    if set(telemetry.provider_request_counts) != expected_providers:
        return None
    if set(telemetry.response_bytes_by_provider) != expected_providers:
        return None
    return telemetry.provider_request_counts["alpaca"] > 0


def _terminal_time(verified_run: VerifiedShadowRunEvidence) -> datetime | None:
    bundle = verified_run.published_bundle
    artifact = verified_run.published_artifact
    if bundle is not None and artifact is not None:
        report_contents = bundle.bundle
        is_research_report = (
            "brief_draft" in report_contents
            or report_contents.get("brief_origin") == BriefOrigin.DETERMINISTIC_REDUCED.value
        )
        if is_research_report:
            return artifact.published_at
    checkpoints = verified_run.stored_run.checkpoints
    if not checkpoints:
        return None
    terminal = checkpoints[-1]
    if terminal.resumable or terminal.execution_status not in {
        ExecutionStatus.PUBLISHED,
        ExecutionStatus.FAILED,
        ExecutionStatus.SKIPPED,
    }:
        return None
    return terminal.written_at


def _duration_seconds(started_at: datetime, terminal_at: datetime | None) -> int | None:
    if terminal_at is None:
        return None
    elapsed = terminal_at - started_at
    if elapsed < timedelta(0):
        raise ValueError("verified run terminal time predates its invocation")
    return ceil(elapsed.total_seconds())


def _with_run_measurements(
    runs: RunRepository,
    stored_run: StoredRun,
    *,
    published_bundle: PublishedRunBundle | None = None,
    published_artifact: PublishedArtifact | None = None,
    replay_result: ArtifactReplayResult | None = None,
) -> VerifiedShadowRunEvidence:
    replay_telemetry = replay_result.telemetry if replay_result is not None else None
    telemetry = _verified_run_telemetry(runs, stored_run, replay_telemetry)
    normal_provider = _normal_provider_attempt(stored_run.run, telemetry)
    partial = VerifiedShadowRunEvidence(
        stored_run=stored_run,
        published_bundle=published_bundle,
        published_artifact=published_artifact,
        replay_result=replay_result,
        telemetry=telemetry,
        normal_provider=normal_provider,
    )
    terminal_at = _terminal_time(partial)
    return VerifiedShadowRunEvidence(
        stored_run=stored_run,
        published_bundle=published_bundle,
        published_artifact=published_artifact,
        replay_result=replay_result,
        telemetry=telemetry,
        normal_provider=normal_provider,
        terminal_at=terminal_at,
        duration_seconds=_duration_seconds(stored_run.run.invoked_at, terminal_at),
    )


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
            verified_run_values.append(_with_run_measurements(runs, stored_run))
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
            _with_run_measurements(
                runs,
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
    release_matrix_keys = tuple((item.check_id, item.python_version) for item in release_receipts)
    if len(release_matrix_keys) != len(set(release_matrix_keys)):
        raise ValueError("duplicate release check matrix evidence")

    verified_by_run_id = {item.stored_run.run_id: item for item in verified_runs}
    period_feedback: list[RecordedFeedback] = []
    for feedback_record in feedback_records:
        verified_run = verified_by_run_id.get(feedback_record.run_id)
        if verified_run is None:
            referenced_run = runs.load(feedback_record.run_id)
            if referenced_run is None:
                raise ValueError("feedback references an unknown run")
            if period.start_date <= referenced_run.run.market_date <= period.end_date:
                raise ValueError("run repository omitted a feedback-referenced period run")
            continue
        if verified_run.published_artifact is None:
            raise ValueError("feedback references an unpublished run")
        if feedback_record.bundle_sha256 != verified_run.published_artifact.bundle_sha256:
            raise ValueError("feedback bundle digest differs from its verified publication")
        if feedback_record.recorded_at < verified_run.published_artifact.published_at:
            raise ValueError("feedback predates its verified publication")
        period_feedback.append(feedback_record)

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
    _validate_observation_corrections(day_observations)
    feedback_by_id = {item.feedback_id: item for item in feedback_records}
    for observation in period_observations:
        for run_id in observation.observed_run_ids:
            observed_run_evidence = verified_by_run_id.get(run_id)
            stored_observed_run = (
                observed_run_evidence.stored_run
                if observed_run_evidence is not None
                else runs.load(run_id)
            )
            if stored_observed_run is None:
                raise ValueError("observation references an unknown run")
            if stored_observed_run.run.market_date != observation.market_date:
                raise ValueError("observation run reference has a different market date")
            if observed_run_evidence is None:
                raise ValueError("run repository omitted an observation-referenced period run")
        for feedback_id in observation.observed_feedback_ids:
            observed_feedback = feedback_by_id.get(feedback_id)
            if observed_feedback is None:
                raise ValueError("observation references unknown feedback")
            feedback_run_evidence = verified_by_run_id.get(observed_feedback.run_id)
            stored_feedback_run = (
                feedback_run_evidence.stored_run
                if feedback_run_evidence is not None
                else runs.load(observed_feedback.run_id)
            )
            if stored_feedback_run is None:
                raise ValueError("observation feedback references an unknown run")
            if stored_feedback_run.run.market_date != observation.market_date:
                raise ValueError("observation feedback reference has a different market date")
            if feedback_run_evidence is None:
                raise ValueError("run repository omitted an observation-feedback period run")
    source_identities = {
        (item.source_commit_sha256, item.source_tree_sha256) for item in release_receipts
    }
    if len(source_identities) > 1:
        raise ValueError("release receipts must bind one exact source commit and tree")
    source_commit, source_tree = next(iter(source_identities), (None, None))
    content_records = (
        *run_hash_records,
        *period_feedback,
        *period_observations,
        *attestations,
        *release_receipts,
    )
    return _create_shadow_evaluation_evidence(
        period=period,
        origin=(
            ShadowEvaluationOrigin.PERSONAL_SHADOW
            if any(item.normal_provider is True for item in verified_runs)
            else None
        ),
        verified_runs=verified_runs,
        feedback_records=tuple(
            sorted(period_feedback, key=lambda item: (item.recorded_at, item.feedback_id))
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
