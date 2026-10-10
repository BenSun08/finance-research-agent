from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256

from finance_research_agent.application.config_service import (
    ConfigService,
    DirectoryConfigurationRepository,
)
from finance_research_agent.application.feedback_service import RecordedFeedback
from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.performance_telemetry import RunTelemetryRecorder
from finance_research_agent.application.reduced_report import (
    render_reduced_report,
    render_reduced_report_base,
)
from finance_research_agent.domain.enums import (
    BriefOrigin,
    DataQualityStatus,
    ExecutionStatus,
    ReducedReportReason,
)
from finance_research_agent.domain.models import (
    PublishedArtifact,
    PublishedRunBundle,
    RunCheckpoint,
    StoredRun,
)
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    ReleaseCheckConclusion,
    ReleaseCheckId,
    ReleaseCheckReceipt,
    ShadowDayAvailability,
    ShadowDayObservation,
)
from finance_research_agent.evaluation.shadow_scorecard import ShadowPeriod


class _Runs:
    def __init__(
        self,
        records: tuple[StoredRun, ...] = (),
        staged_artifacts: dict[tuple[str, str], bytes] | None = None,
    ) -> None:
        self.requested_period: tuple[date, date] | None = None
        self.records = records
        self.staged_artifacts = staged_artifacts or {}

    def list_runs(self, market_date_start: date, market_date_end: date) -> tuple[StoredRun, ...]:
        self.requested_period = (market_date_start, market_date_end)
        return self.records

    def load(self, run_id: str) -> StoredRun | None:
        return next((item for item in self.records if item.run.run_id == run_id), None)

    def read_staged_artifact(self, run_id: str, artifact_name: str) -> bytes | None:
        return self.staged_artifacts.get((run_id, artifact_name))


class _Artifacts:
    def __init__(self) -> None:
        self.bundle: PublishedRunBundle | None = None
        self.report: str | None = None
        self.artifact: PublishedArtifact | None = None

    def load_published_bundle(self, run_id: str) -> PublishedRunBundle | None:
        return self.bundle

    def get_report(self, run_id: str) -> str | None:
        return self.report

    def get_published_artifact(self, run_id: str) -> PublishedArtifact | None:
        return self.artifact


class _Feedback:
    def __init__(self, records: tuple[RecordedFeedback, ...] = ()) -> None:
        self.records = records

    def list_feedback(self) -> tuple[RecordedFeedback, ...]:
        return self.records


class _Observations:
    def __init__(self, records: tuple[ShadowDayObservation, ...] = ()) -> None:
        self.records = records

    def list_observations(self) -> tuple[ShadowDayObservation, ...]:
        return self.records


class _Attestations:
    def list_attestations(self) -> tuple[()]:
        return ()


class _ReleaseEvidence:
    def __init__(self, receipts: tuple[ReleaseCheckReceipt, ...] = ()) -> None:
        self.read_count = 0
        self.receipts = receipts

    def list_receipts(self) -> tuple[ReleaseCheckReceipt, ...]:
        self.read_count += 1
        return self.receipts


def test_loader_returns_empty_snapshot_for_exact_inclusive_period() -> None:
    from finance_research_agent.application import shadow_evidence_loader

    period = ShadowPeriod(start_date=date(2026, 10, 5), end_date=date(2026, 10, 9))
    runs = _Runs()
    release_evidence = _ReleaseEvidence()

    evidence = shadow_evidence_loader.load_shadow_evaluation_evidence(
        period,
        runs,  # type: ignore[arg-type]
        _Artifacts(),  # type: ignore[arg-type]
        _Feedback(),  # type: ignore[arg-type]
        _Observations(),  # type: ignore[arg-type]
        _Attestations(),  # type: ignore[arg-type]
        release_evidence,  # type: ignore[arg-type]
    )

    assert runs.requested_period == (date(2026, 10, 5), date(2026, 10, 9))
    assert evidence.period == period
    assert evidence.verified_runs == ()
    assert evidence.published_runs == ()
    assert evidence.feedback_records == ()
    assert evidence.observations == ()
    assert evidence.operator_attestations == ()
    assert evidence.release_check_receipts == ()
    assert evidence.source_commit_sha256 is None
    assert evidence.source_tree_sha256 is None
    assert evidence.evidence_hashes == ()
    assert release_evidence.read_count == 1


def _recorded_versions(run):
    from finance_research_agent.domain.models import ComponentVersions

    configuration = run.configuration_snapshot
    return ComponentVersions(
        core_version=run.core_version,
        mcp_contract_version=run.mcp_contract_version,
        plugin_version=run.plugin_version,
        skill_version=run.skill_version,
        prompt_version=run.prompt_version,
        report_template_version=run.report_template_version,
        schema_versions=run.schema_versions,
        watchlist_version=configuration.watchlist_version,
        regime_policy_version=configuration.regime_policy_version,
        setup_policy_version=configuration.setup_policy_version,
        risk_policy_version=configuration.risk_policy_version,
        source_policy_version=configuration.source_policy_version,
    )


def _personal_reduced_publication(valid_packet, tmp_path):
    from pathlib import Path

    from finance_research_agent.domain.enums import ExecutionStatus

    examples = Path(__file__).parents[2] / "config" / "examples"
    configuration_snapshot = ConfigService(
        DirectoryConfigurationRepository(examples, tmp_path / "config-staging")
    ).validate_and_snapshot()
    run = valid_packet.run.model_copy(update={"configuration_snapshot": configuration_snapshot})
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
    )
    recorder = RunTelemetryRecorder(monotonic_ns=lambda: 0)
    recorder.record_http_exchange("alpaca", 1, 256)
    telemetry = recorder.snapshot(
        research_packet_bytes=packet.synthesis_constraints.serialized_bytes
    )
    reason = ReducedReportReason.SYNTHESIS_UNAVAILABLE
    report = render_reduced_report(packet, reason)
    bundle = PublishedRunBundle(
        run=packet.run.model_copy(update={"execution_status": ExecutionStatus.PUBLISHED}),
        bundle=FrozenMap(
            {
                "research_packet": packet.model_dump(mode="json"),
                "brief_origin": BriefOrigin.DETERMINISTIC_REDUCED.value,
                "reduced_report_reason": reason.value,
                "reduced_report_staged_sha256": sha256(
                    render_reduced_report_base(packet)
                ).hexdigest(),
                "reduced_plans": (),
                "validation_reports": (),
                "performance_telemetry": telemetry.model_dump(mode="json"),
                "performance_telemetry_sha256": sha256(canonical_bytes(telemetry)).hexdigest(),
            }
        ),
        report_markdown=report,
        markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
    )
    artifact = PublishedArtifact(
        run_id=bundle.run.run_id,
        bundle_sha256=sha256(canonical_bytes(bundle)).hexdigest(),
        markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
        published_at=bundle.run.invoked_at + timedelta(minutes=2),
    )
    return bundle, artifact


def test_loader_verifies_and_replays_every_published_run(valid_packet, monkeypatch) -> None:
    from finance_research_agent.application import shadow_evidence_loader

    packet = valid_packet
    reason = ReducedReportReason.SYNTHESIS_UNAVAILABLE
    report = render_reduced_report(packet, reason)
    bundle = PublishedRunBundle(
        run=packet.run.model_copy(update={"execution_status": ExecutionStatus.PUBLISHED}),
        bundle=FrozenMap(
            {
                "research_packet": packet.model_dump(mode="json"),
                "brief_origin": BriefOrigin.DETERMINISTIC_REDUCED.value,
                "reduced_report_reason": reason.value,
                "reduced_report_staged_sha256": sha256(
                    render_reduced_report_base(packet)
                ).hexdigest(),
                "reduced_plans": (),
                "validation_reports": (),
            }
        ),
        report_markdown=report,
        markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
    )
    artifact = PublishedArtifact(
        run_id=bundle.run.run_id,
        bundle_sha256=sha256(canonical_bytes(bundle)).hexdigest(),
        markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
        published_at=bundle.run.invoked_at + timedelta(minutes=1),
    )
    runs = _Runs((StoredRun(run=bundle.run, published=True),))
    artifacts = _Artifacts()
    artifacts.bundle = bundle
    artifacts.report = report
    artifacts.artifact = artifact
    monkeypatch.setattr(
        shadow_evidence_loader,
        "current_component_versions",
        lambda snapshot: _recorded_versions(bundle.run),
    )

    evidence = shadow_evidence_loader.load_shadow_evaluation_evidence(
        ShadowPeriod(start_date=bundle.run.market_date, end_date=bundle.run.market_date),
        runs,  # type: ignore[arg-type]
        artifacts,  # type: ignore[arg-type]
        _Feedback(),  # type: ignore[arg-type]
        _Observations(),  # type: ignore[arg-type]
        _Attestations(),  # type: ignore[arg-type]
        _ReleaseEvidence(),  # type: ignore[arg-type]
    )

    assert evidence.published_runs == (bundle,)
    assert evidence.verified_runs[0].published_artifact == artifact
    assert evidence.verified_runs[0].replay_result is not None
    assert evidence.verified_runs[0].replay_result.json_matches is True
    assert evidence.verified_runs[0].replay_result.component_version_mismatches == ()
    assert evidence.verified_runs[0].replay_result.markdown_matches is True
    assert evidence.evidence_hashes


def test_loader_derives_normal_provider_and_uses_original_receipt_duration(
    valid_packet, tmp_path, monkeypatch
) -> None:
    from finance_research_agent.application import shadow_evidence_loader
    from finance_research_agent.domain.models import PerformanceTelemetry
    from finance_research_agent.evaluation.shadow_scorecard import ShadowEvaluationOrigin

    bundle, artifact = _personal_reduced_publication(valid_packet, tmp_path)
    artifacts = _Artifacts()
    artifacts.bundle = bundle
    artifacts.report = bundle.report_markdown
    artifacts.artifact = artifact
    monkeypatch.setattr(
        shadow_evidence_loader,
        "current_component_versions",
        lambda snapshot: _recorded_versions(bundle.run),
    )
    telemetry = PerformanceTelemetry.model_validate(
        bundle.model_dump(mode="json")["bundle"]["performance_telemetry"], strict=True
    )
    telemetry_payload = canonical_bytes(telemetry)
    telemetry_digest = sha256(telemetry_payload).hexdigest()
    terminal_checkpoint = RunCheckpoint(
        run_id=bundle.run.run_id,
        stage="PUBLISHED",
        execution_status=ExecutionStatus.PUBLISHED,
        data_quality_status=bundle.run.data_quality_status,
        delivery_status=bundle.run.delivery_status,
        written_at=artifact.published_at + timedelta(minutes=5),
        evidence_cutoff_at=bundle.run.evidence_cutoff_at,
        artifact_hashes=FrozenMap({f"performance_telemetry_{telemetry_digest}": telemetry_digest}),
        resumable=False,
    )
    runs = _Runs(
        (StoredRun(run=bundle.run, checkpoints=(terminal_checkpoint,), published=True),),
        {(bundle.run.run_id, f"performance_telemetry_{telemetry_digest}"): telemetry_payload},
    )

    evidence = shadow_evidence_loader.load_shadow_evaluation_evidence(
        ShadowPeriod(start_date=bundle.run.market_date, end_date=bundle.run.market_date),
        runs,  # type: ignore[arg-type]
        artifacts,  # type: ignore[arg-type]
        _Feedback(),  # type: ignore[arg-type]
        _Observations(),  # type: ignore[arg-type]
        _Attestations(),  # type: ignore[arg-type]
        _ReleaseEvidence(),  # type: ignore[arg-type]
    )

    verified_run = evidence.verified_runs[0]
    assert verified_run.normal_provider is True
    assert verified_run.terminal_at == artifact.published_at
    assert verified_run.duration_seconds == 120
    assert evidence.origin is ShadowEvaluationOrigin.PERSONAL_SHADOW


def test_loader_uses_final_failure_checkpoint_for_normal_provider_duration(
    valid_packet, tmp_path
) -> None:
    from finance_research_agent.application import shadow_evidence_loader
    from finance_research_agent.evaluation.shadow_scorecard import ShadowEvaluationOrigin

    bundle, _ = _personal_reduced_publication(valid_packet, tmp_path)
    failed_run = bundle.run.model_copy(
        update={
            "execution_status": ExecutionStatus.FAILED,
            "data_quality_status": DataQualityStatus.FAIL,
        }
    )
    recorder = RunTelemetryRecorder(monotonic_ns=lambda: 0)
    recorder.record_http_exchange("alpaca", 2, 512)
    telemetry = recorder.snapshot()
    payload = json.dumps(
        telemetry.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    digest = sha256(payload).hexdigest()
    checkpoint = RunCheckpoint(
        run_id=failed_run.run_id,
        stage="FAILED",
        execution_status=ExecutionStatus.FAILED,
        data_quality_status=DataQualityStatus.FAIL,
        delivery_status=failed_run.delivery_status,
        written_at=failed_run.invoked_at + timedelta(minutes=5),
        evidence_cutoff_at=failed_run.evidence_cutoff_at,
        artifact_hashes=FrozenMap({f"performance_telemetry_{digest}": digest}),
        resumable=False,
    )
    stored_run = StoredRun(run=failed_run, checkpoints=(checkpoint,))
    runs = _Runs(
        (stored_run,),
        {(failed_run.run_id, f"performance_telemetry_{digest}"): payload},
    )

    evidence = shadow_evidence_loader.load_shadow_evaluation_evidence(
        ShadowPeriod(start_date=failed_run.market_date, end_date=failed_run.market_date),
        runs,  # type: ignore[arg-type]
        _Artifacts(),  # type: ignore[arg-type]
        _Feedback(),  # type: ignore[arg-type]
        _Observations(),  # type: ignore[arg-type]
        _Attestations(),  # type: ignore[arg-type]
        _ReleaseEvidence(),  # type: ignore[arg-type]
    )

    verified_run = evidence.verified_runs[0]
    assert evidence.origin is ShadowEvaluationOrigin.PERSONAL_SHADOW
    assert verified_run.normal_provider is True
    assert verified_run.terminal_at == checkpoint.written_at
    assert verified_run.duration_seconds == 300


def test_loader_rejects_feedback_for_an_unknown_run() -> None:
    import pytest

    from finance_research_agent.application import shadow_evidence_loader

    feedback = RecordedFeedback(
        feedback_id="feedback-orphan",
        run_id="premarket-2026-10-05-r1",
        bundle_sha256="a" * 64,
        recorded_at=datetime(2026, 10, 5, 12, tzinfo=UTC),
        clarity_score=3,
        evidence_score=3,
        usefulness_score=3,
        citation_reviews=(),
    )

    with pytest.raises(ValueError, match="feedback references an unknown run"):
        shadow_evidence_loader.load_shadow_evaluation_evidence(
            ShadowPeriod(start_date=date(2026, 10, 5), end_date=date(2026, 10, 9)),
            _Runs(),  # type: ignore[arg-type]
            _Artifacts(),  # type: ignore[arg-type]
            _Feedback((feedback,)),  # type: ignore[arg-type]
            _Observations(),  # type: ignore[arg-type]
            _Attestations(),  # type: ignore[arg-type]
            _ReleaseEvidence(),  # type: ignore[arg-type]
        )


def test_loader_rejects_day_observations_with_unknown_run_references() -> None:
    import pytest

    from finance_research_agent.application import shadow_evidence_loader

    observation = ShadowDayObservation(
        id="shadow-day-orphan",
        market_date=date(2026, 10, 5),
        recorded_at=datetime(2026, 10, 5, 16, tzinfo=UTC),
        session_id="session-2026-10-05",
        availability=ShadowDayAvailability.ONLINE,
        observed_run_ids=("premarket-2026-10-05-r1",),
    )

    with pytest.raises(ValueError, match="observation references an unknown run"):
        shadow_evidence_loader.load_shadow_evaluation_evidence(
            ShadowPeriod(start_date=date(2026, 10, 5), end_date=date(2026, 10, 5)),
            _Runs(),  # type: ignore[arg-type]
            _Artifacts(),  # type: ignore[arg-type]
            _Feedback(),  # type: ignore[arg-type]
            _Observations((observation,)),  # type: ignore[arg-type]
            _Attestations(),  # type: ignore[arg-type]
            _ReleaseEvidence(),  # type: ignore[arg-type]
        )


def test_loader_rejects_duplicate_release_check_matrix_evidence() -> None:
    import pytest

    from finance_research_agent.application import shadow_evidence_loader

    common = {
        "check_id": ReleaseCheckId.LINT,
        "source_commit_sha256": "a" * 64,
        "source_tree_sha256": "b" * 64,
        "repository_id": "12345",
        "workflow_id": "67890",
        "python_version": "3.13",
        "completed_at": datetime(2026, 10, 11, 1, tzinfo=UTC),
        "conclusion": ReleaseCheckConclusion.SUCCESS,
        "response_sha256": "c" * 64,
    }
    first = ReleaseCheckReceipt(
        **(
            common
            | {
                "id": "receipt-lint-one",
                "actions_run_id": "run-1",
                "actions_job_id": "job-1",
                "actions_step_id": "step-1",
            }
        )
    )
    second = ReleaseCheckReceipt(
        **(
            common
            | {
                "id": "receipt-lint-two",
                "actions_run_id": "run-2",
                "actions_job_id": "job-2",
                "actions_step_id": "step-2",
            }
        )
    )

    with pytest.raises(ValueError, match="duplicate release check matrix evidence"):
        shadow_evidence_loader.load_shadow_evaluation_evidence(
            ShadowPeriod(start_date=date(2026, 10, 5), end_date=date(2026, 10, 9)),
            _Runs(),  # type: ignore[arg-type]
            _Artifacts(),  # type: ignore[arg-type]
            _Feedback(),  # type: ignore[arg-type]
            _Observations(),  # type: ignore[arg-type]
            _Attestations(),  # type: ignore[arg-type]
            _ReleaseEvidence((first, second)),  # type: ignore[arg-type]
        )
