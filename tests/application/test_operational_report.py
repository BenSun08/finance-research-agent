"""Deterministic operational reports for hard failures before synthesis."""

from datetime import timedelta

import pytest

from finance_research_agent.adapters.filesystem import (
    FileSystemRunRepository,
    PublicationError,
)
from finance_research_agent.application import publication_service
from finance_research_agent.application.replay_service import (
    _recorded_versions,
    replay_published_artifact,
)
from finance_research_agent.domain.enums import DataQualityStatus, ExecutionStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import RunCheckpoint
from finance_research_agent.domain.types import FrozenMap


def test_operational_report_uses_only_run_identity_and_closed_failure_code(
    valid_packet,
) -> None:
    from finance_research_agent.application.operational_report import (
        render_operational_report,
    )

    run = valid_packet.run.model_copy(
        update={"data_quality_status": DataQualityStatus.FAIL}
    )
    report = render_operational_report(run, ErrorCode.MARKET_CALENDAR_UNAVAILABLE)

    assert report == (
        "# Premarket Operational Report\n"
        "\n"
        "Brief origin: OPERATIONAL\n"
        "Run ID: premarket-2026-08-26-r1\n"
        "Market date: 2026-08-26\n"
        "Execution status: PUBLISHED\n"
        "Data quality status: FAIL\n"
        "Delivery status: MANUAL\n"
        "Failure code: MARKET_CALENDAR_UNAVAILABLE\n"
        "\n"
        "No market conclusion or trade plan is available.\n"
        "Human review is required before any decision.\n"
    )


def test_pre_synthesis_fail_publishes_only_operational_artifact(
    tmp_path, valid_packet
) -> None:
    run = valid_packet.run.model_copy(update={
        "execution_status": ExecutionStatus.CREATED,
        "data_quality_status": DataQualityStatus.FAIL,
    })
    repository = FileSystemRunRepository(tmp_path)
    repository.create(run)
    repository.checkpoint(run.run_id, RunCheckpoint(
        run_id=run.run_id,
        stage="CREATED",
        execution_status=ExecutionStatus.CREATED,
        data_quality_status=DataQualityStatus.FAIL,
        delivery_status=run.delivery_status,
        written_at=run.invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({}),
        resumable=True,
    ))
    at = run.invoked_at + timedelta(seconds=1)

    receipt = publication_service.publish_operational_report(
        repository, run, ErrorCode.MARKET_CALENDAR_UNAVAILABLE, at
    )

    assert receipt.run_id == run.run_id
    stored = repository.load(run.run_id)
    assert stored is not None and stored.published
    assert stored.checkpoints[-1].stage == "PUBLISHED"
    bundle = repository.load_published_bundle(run.run_id)
    assert bundle is not None
    assert bundle.run.execution_status is ExecutionStatus.PUBLISHED
    contents = bundle.model_dump(mode="json")["bundle"]
    assert contents["brief_origin"] == "OPERATIONAL"
    assert contents["failure_code"] == "MARKET_CALENDAR_UNAVAILABLE"
    assert contents["performance_telemetry"]
    assert contents["performance_telemetry_sha256"]
    report = repository.get_report(run.run_id)
    assert report is not None and "No market conclusion or trade plan" in report
    replay = replay_published_artifact(repository, run.run_id, _recorded_versions(bundle))
    assert replay.json_matches and replay.markdown_matches
    assert replay.telemetry is not None
    assert publication_service.publish_operational_report(
        repository, run, ErrorCode.MARKET_CALENDAR_UNAVAILABLE, at
    ) == receipt


def test_frozen_global_fail_publishes_without_research_packet(
    tmp_path, valid_packet
) -> None:
    run = valid_packet.run.model_copy(update={
        "execution_status": ExecutionStatus.ANALYZING,
        "data_quality_status": DataQualityStatus.FAIL,
    })
    repository = FileSystemRunRepository(tmp_path)
    repository.create(run)
    repository.checkpoint(run.run_id, RunCheckpoint(
        run_id=run.run_id,
        stage="ANALYZING",
        execution_status=ExecutionStatus.ANALYZING,
        data_quality_status=DataQualityStatus.FAIL,
        delivery_status=run.delivery_status,
        written_at=run.invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({}),
        resumable=True,
    ))
    repository.freeze_evidence(run.run_id, run.evidence_cutoff_at)

    receipt = publication_service.publish_operational_report(
        repository, run, ErrorCode.PROVIDER_UNAVAILABLE,
        run.evidence_cutoff_at + timedelta(seconds=1),
    )

    assert receipt.run_id == run.run_id
    stored = repository.load(run.run_id)
    assert stored is not None and stored.published
    assert [checkpoint.stage for checkpoint in stored.checkpoints][-2:] == [
        "EVIDENCE_FROZEN", "PUBLISHED"
    ]
    assert {
        name for name in stored.checkpoints[-1].artifact_hashes
        if not name.startswith("performance_telemetry_")
    } == {"operational_reason"}
    assert "PROVIDER_UNAVAILABLE" in (repository.get_report(run.run_id) or "")


def test_unfrozen_direct_publication_checkpoint_needs_operational_reason(
    tmp_path, valid_packet
) -> None:
    run = valid_packet.run.model_copy(update={
        "execution_status": ExecutionStatus.CREATED,
        "data_quality_status": DataQualityStatus.FAIL,
    })
    repository = FileSystemRunRepository(tmp_path)
    repository.create(run)
    repository.checkpoint(run.run_id, RunCheckpoint(
        run_id=run.run_id,
        stage="CREATED",
        execution_status=ExecutionStatus.CREATED,
        data_quality_status=DataQualityStatus.FAIL,
        delivery_status=run.delivery_status,
        written_at=run.invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({}),
        resumable=True,
    ))

    with pytest.raises(ValueError, match="operational|publication"):
        repository.checkpoint(run.run_id, RunCheckpoint(
            run_id=run.run_id,
            stage="PUBLISHED",
            execution_status=ExecutionStatus.PUBLISHED,
            data_quality_status=DataQualityStatus.FAIL,
            delivery_status=run.delivery_status,
            written_at=run.invoked_at + timedelta(seconds=1),
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({}),
            resumable=False,
        ))


def test_operational_publication_rejects_uncheckpointed_packet_bytes(
    tmp_path, valid_packet
) -> None:
    run = valid_packet.run.model_copy(update={
        "execution_status": ExecutionStatus.ANALYZING,
        "data_quality_status": DataQualityStatus.FAIL,
    })
    repository = FileSystemRunRepository(tmp_path)
    repository.create(run)
    repository.checkpoint(run.run_id, RunCheckpoint(
        run_id=run.run_id,
        stage="ANALYZING",
        execution_status=ExecutionStatus.ANALYZING,
        data_quality_status=DataQualityStatus.FAIL,
        delivery_status=run.delivery_status,
        written_at=run.invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({}),
        resumable=True,
    ))
    repository.freeze_evidence(run.run_id, run.evidence_cutoff_at)
    repository.stage_artifact(run.run_id, "research_packet", b"orphaned packet bytes")

    with pytest.raises(ValueError, match="packet"):
        publication_service.publish_operational_report(
            repository, run, ErrorCode.PROVIDER_UNAVAILABLE,
            run.evidence_cutoff_at + timedelta(seconds=1),
        )
    stored = repository.load(run.run_id)
    assert stored is not None and not stored.published


def test_direct_operational_checkpoint_rejects_uncheckpointed_packet_bytes(
    tmp_path, valid_packet
) -> None:
    run = valid_packet.run.model_copy(update={
        "execution_status": ExecutionStatus.CREATED,
        "data_quality_status": DataQualityStatus.FAIL,
    })
    repository = FileSystemRunRepository(tmp_path)
    repository.create(run)
    repository.checkpoint(run.run_id, RunCheckpoint(
        run_id=run.run_id,
        stage="CREATED",
        execution_status=ExecutionStatus.CREATED,
        data_quality_status=DataQualityStatus.FAIL,
        delivery_status=run.delivery_status,
        written_at=run.invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({}),
        resumable=True,
    ))
    repository.stage_artifact(run.run_id, "research_packet", b"orphaned packet bytes")
    reason_hash = repository.stage_artifact(
        run.run_id, "operational_reason", b"MARKET_CALENDAR_UNAVAILABLE"
    )

    with pytest.raises(ValueError, match="operational|publication"):
        repository.checkpoint(run.run_id, RunCheckpoint(
            run_id=run.run_id,
            stage="PUBLISHED",
            execution_status=ExecutionStatus.PUBLISHED,
            data_quality_status=DataQualityStatus.FAIL,
            delivery_status=run.delivery_status,
            written_at=run.invoked_at + timedelta(seconds=1),
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"operational_reason": reason_hash}),
            resumable=False,
        ))


def test_operational_publication_rejects_changed_prior_staged_artifact(
    tmp_path, valid_packet
) -> None:
    run = valid_packet.run.model_copy(update={
        "execution_status": ExecutionStatus.CREATED,
        "data_quality_status": DataQualityStatus.FAIL,
    })
    repository = FileSystemRunRepository(tmp_path)
    repository.create(run)
    digest = repository.stage_artifact(run.run_id, "collection_manifest", b"original")
    repository.checkpoint(run.run_id, RunCheckpoint(
        run_id=run.run_id,
        stage="CREATED",
        execution_status=ExecutionStatus.CREATED,
        data_quality_status=DataQualityStatus.FAIL,
        delivery_status=run.delivery_status,
        written_at=run.invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({"collection_manifest": digest}),
        resumable=True,
    ))
    staged = (
        tmp_path / "runs" / "2026" / "2026-08-26" / ".staging" /
        run.run_id / "artifacts" / "collection_manifest.bin"
    )
    staged.write_bytes(b"changed")

    with pytest.raises(ValueError, match="artifact|publication"):
        publication_service.publish_operational_report(
            repository, run, ErrorCode.PROVIDER_UNAVAILABLE,
            run.invoked_at + timedelta(seconds=1),
        )
    assert repository.load(run.run_id) is not None
    assert repository.get_report(run.run_id) is None


def test_operational_publication_recovers_only_with_same_failure_code(
    tmp_path, valid_packet
) -> None:
    run = valid_packet.run.model_copy(update={
        "execution_status": ExecutionStatus.CREATED,
        "data_quality_status": DataQualityStatus.FAIL,
    })
    repository = FileSystemRunRepository(tmp_path)
    repository.create(run)
    repository.checkpoint(run.run_id, RunCheckpoint(
        run_id=run.run_id,
        stage="CREATED",
        execution_status=ExecutionStatus.CREATED,
        data_quality_status=DataQualityStatus.FAIL,
        delivery_status=run.delivery_status,
        written_at=run.invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({}),
        resumable=True,
    ))
    at = run.invoked_at + timedelta(seconds=1)
    repository.inject_failure_before_rename = True
    with pytest.raises(PublicationError, match="injected failure"):
        publication_service.publish_operational_report(
            repository, run, ErrorCode.CONFIGURATION_INVALID, at
        )
    assert repository.get_report(run.run_id) is None
    stored = repository.load(run.run_id)
    assert stored is not None and stored.checkpoints[-1].stage == "PUBLISHED"
    with pytest.raises(ValueError, match="another operational reason"):
        publication_service.publish_operational_report(
            repository, run, ErrorCode.CREDENTIALS_MISSING, at
        )

    repository.inject_failure_before_rename = False
    receipt = publication_service.publish_operational_report(
        repository, run, ErrorCode.CONFIGURATION_INVALID, at
    )
    assert receipt.run_id == run.run_id
    assert repository.get_report(run.run_id) is not None
    stored = repository.load(run.run_id)
    assert stored is not None
    assert [item.stage for item in stored.checkpoints].count("PUBLISHED") == 1


def test_operational_retry_rejects_changed_artifact_after_failed_rename(
    tmp_path, valid_packet
) -> None:
    run = valid_packet.run.model_copy(update={
        "execution_status": ExecutionStatus.CREATED,
        "data_quality_status": DataQualityStatus.FAIL,
    })
    repository = FileSystemRunRepository(tmp_path)
    repository.create(run)
    digest = repository.stage_artifact(run.run_id, "collection_manifest", b"original")
    repository.checkpoint(run.run_id, RunCheckpoint(
        run_id=run.run_id,
        stage="CREATED",
        execution_status=ExecutionStatus.CREATED,
        data_quality_status=DataQualityStatus.FAIL,
        delivery_status=run.delivery_status,
        written_at=run.invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({"collection_manifest": digest}),
        resumable=True,
    ))
    at = run.invoked_at + timedelta(seconds=1)
    repository.inject_failure_before_rename = True
    with pytest.raises(PublicationError, match="injected failure"):
        publication_service.publish_operational_report(
            repository, run, ErrorCode.PROVIDER_UNAVAILABLE, at
        )
    staged = (
        tmp_path / "runs" / "2026" / "2026-08-26" / ".staging" /
        run.run_id / "artifacts" / "collection_manifest.bin"
    )
    staged.write_bytes(b"changed")
    repository.inject_failure_before_rename = False

    with pytest.raises(PublicationError, match="artifact"):
        publication_service.publish_operational_report(
            repository, run, ErrorCode.PROVIDER_UNAVAILABLE, at
        )
    assert repository.get_report(run.run_id) is None
    stored = repository.load(run.run_id)
    assert stored is not None and not stored.published
