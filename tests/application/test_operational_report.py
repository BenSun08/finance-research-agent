"""Deterministic operational reports for hard failures before synthesis."""

from datetime import timedelta

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.application import publication_service
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
    assert bundle.model_dump(mode="json")["bundle"] == {
        "brief_origin": "OPERATIONAL",
        "failure_code": "MARKET_CALENDAR_UNAVAILABLE",
    }
    report = repository.get_report(run.run_id)
    assert report is not None and "No market conclusion or trade plan" in report
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
    assert stored.checkpoints[-1].artifact_hashes.keys() == {"operational_reason"}
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
