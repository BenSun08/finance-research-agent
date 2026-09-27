import pytest

from finance_research_agent.application.run_state import compose_publication_context
from finance_research_agent.domain.enums import (
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
)
from finance_research_agent.domain.models import RunCheckpoint
from finance_research_agent.domain.types import FrozenMap


def test_publication_context_uses_checkpoint_statuses_and_preserves_run_identity(
    valid_packet,
) -> None:
    run = valid_packet.run
    checkpoint = RunCheckpoint(
        run_id=run.run_id,
        stage="PUBLISHED",
        execution_status=ExecutionStatus.PUBLISHED,
        data_quality_status=DataQualityStatus.DEGRADED,
        delivery_status=DeliveryStatus.DELAYED,
        written_at=run.evidence_cutoff_at,
        evidence_cutoff_at=run.evidence_cutoff_at,
        artifact_hashes=FrozenMap({}),
        resumable=False,
    )

    published = compose_publication_context(run, checkpoint)

    assert published == run.model_copy(
        update={
            "execution_status": checkpoint.execution_status,
            "data_quality_status": checkpoint.data_quality_status,
            "delivery_status": checkpoint.delivery_status,
        }
    )
    assert published.configuration_snapshot is run.configuration_snapshot
    assert published.schema_versions is run.schema_versions


def test_publication_context_rejects_a_checkpoint_from_another_run(valid_packet) -> None:
    run = valid_packet.run
    checkpoint = RunCheckpoint(
        run_id="premarket-2026-08-27-r1",
        stage="PUBLISHED",
        execution_status=ExecutionStatus.PUBLISHED,
        data_quality_status=DataQualityStatus.PASS,
        delivery_status=DeliveryStatus.ON_TIME,
        written_at=run.evidence_cutoff_at,
        evidence_cutoff_at=run.evidence_cutoff_at,
        artifact_hashes=FrozenMap({}),
        resumable=False,
    )

    with pytest.raises(ValueError, match="checkpoint run_id"):
        compose_publication_context(run, checkpoint)
