from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256

from finance_research_agent.application.feedback_service import RecordedFeedback
from finance_research_agent.application.reduced_report import (
    render_reduced_report,
    render_reduced_report_base,
)
from finance_research_agent.domain.enums import (
    BriefOrigin,
    ExecutionStatus,
    ReducedReportReason,
)
from finance_research_agent.domain.models import (
    PublishedArtifact,
    PublishedRunBundle,
    StoredRun,
)
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    ShadowDayAvailability,
    ShadowDayObservation,
)
from finance_research_agent.evaluation.shadow_scorecard import ShadowPeriod


class _Runs:
    def __init__(self, records: tuple[StoredRun, ...] = ()) -> None:
        self.requested_period: tuple[date, date] | None = None
        self.records = records

    def list_runs(self, market_date_start: date, market_date_end: date) -> tuple[StoredRun, ...]:
        self.requested_period = (market_date_start, market_date_end)
        return self.records

    def load(self, run_id: str) -> StoredRun | None:
        return next((item for item in self.records if item.run.run_id == run_id), None)


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
    def __init__(self) -> None:
        self.read_count = 0

    def list_receipts(self) -> tuple[()]:
        self.read_count += 1
        return ()


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
