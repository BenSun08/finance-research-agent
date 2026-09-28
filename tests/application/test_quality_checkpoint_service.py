import json
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.domain.enums import (
    DataQualityStatus,
    ExecutionStatus,
    SourceRole,
)
from finance_research_agent.domain.models import RunCheckpoint
from finance_research_agent.domain.quality import evaluate_data_quality
from finance_research_agent.domain.types import FrozenMap


def _frozen_run(repository: FileSystemRunRepository, valid_packet) -> tuple:
    run = valid_packet.run.model_copy(update={"evidence_cutoff_at": None})
    repository.create(run)
    collection_payload = b"canonical collection fixture"
    collection_digest = repository.stage_artifact(
        run.run_id, "market_data_collection", collection_payload
    )
    repository.checkpoint(
        run.run_id,
        RunCheckpoint(
            run_id=run.run_id,
            stage="EVIDENCE_COLLECTED",
            execution_status=run.execution_status,
            data_quality_status=run.data_quality_status,
            delivery_status=run.delivery_status,
            written_at=run.invoked_at + timedelta(seconds=1),
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"market_data_collection": collection_digest}),
            resumable=True,
        ),
    )
    frozen = repository.freeze_evidence(run.run_id, run.invoked_at + timedelta(seconds=2))
    assert frozen.checkpoints[-1].stage == "EVIDENCE_FROZEN"
    return frozen, collection_digest


def _quality_result():
    from finance_research_agent.domain.models import SourceHealth

    return evaluate_data_quality(
        source_roles=tuple(SourceRole),
        source_health=(
            SourceHealth(provider="alpaca", available=True, required=True),
            SourceHealth(provider="market-calendar", available=True, required=True),
            SourceHealth(provider="macro", available=True, required=True),
            SourceHealth(provider="sec_edgar", available=True, required=True),
        ),
        risk_policy=None,
    )


def test_quality_result_is_staged_and_hash_bound_to_frozen_collection(
    tmp_path: Path, valid_packet
) -> None:
    from finance_research_agent.application.quality_checkpoint import (
        checkpoint_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    frozen, collection_digest = _frozen_run(repository, valid_packet)
    result = _quality_result()
    checkpointed_at = frozen.evidence_cutoff_at + timedelta(seconds=1)

    stored = checkpoint_market_data_quality(
        repository,
        frozen,
        result,
        checkpointed_at=checkpointed_at,
    )

    checkpoint = stored.checkpoints[-1]
    assert checkpoint.stage == "QUALITY_EVALUATED"
    assert checkpoint.execution_status is ExecutionStatus.ANALYZING
    assert checkpoint.data_quality_status is result.status
    assert checkpoint.evidence_cutoff_at == frozen.evidence_cutoff_at
    assert checkpoint.written_at == checkpointed_at
    assert checkpoint.artifact_hashes["market_data_collection"] == collection_digest
    quality_bytes = json.dumps(
        result.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    assert repository.read_staged_artifact(frozen.run_id, "data_quality") == quality_bytes
    assert checkpoint.artifact_hashes["data_quality"] == sha256(quality_bytes).hexdigest()
    assert not checkpoint.resumable


def test_quality_checkpoint_resume_reuses_matching_result_without_duplicate_checkpoint(
    tmp_path: Path, valid_packet
) -> None:
    from finance_research_agent.application.quality_checkpoint import (
        checkpoint_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    frozen, _ = _frozen_run(repository, valid_packet)
    result = _quality_result()
    first = checkpoint_market_data_quality(
        repository,
        frozen,
        result,
        checkpointed_at=frozen.evidence_cutoff_at + timedelta(seconds=1),
    )

    resumed = checkpoint_market_data_quality(
        repository,
        first,
        result,
        checkpointed_at=frozen.evidence_cutoff_at + timedelta(seconds=2),
    )

    assert resumed == first
    assert sum(checkpoint.stage == "QUALITY_EVALUATED" for checkpoint in resumed.checkpoints) == 1


def test_quality_checkpoint_rejects_conflicting_resume_result(tmp_path: Path, valid_packet) -> None:
    from finance_research_agent.application.quality_checkpoint import (
        checkpoint_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    frozen, _ = _frozen_run(repository, valid_packet)
    result = _quality_result()
    first = checkpoint_market_data_quality(
        repository,
        frozen,
        result,
        checkpointed_at=frozen.evidence_cutoff_at + timedelta(seconds=1),
    )
    conflicting = result.model_copy(update={"status": DataQualityStatus.FAIL})

    with pytest.raises(ValueError, match="differs from the quality checkpoint"):
        checkpoint_market_data_quality(
            repository,
            first,
            conflicting,
            checkpointed_at=frozen.evidence_cutoff_at + timedelta(seconds=2),
        )


def test_quality_checkpoint_time_cannot_precede_frozen_evidence(
    tmp_path: Path, valid_packet
) -> None:
    from finance_research_agent.application.quality_checkpoint import (
        checkpoint_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    frozen, _ = _frozen_run(repository, valid_packet)

    with pytest.raises(ValueError, match="cannot precede evidence cutoff"):
        checkpoint_market_data_quality(
            repository,
            frozen,
            _quality_result(),
            checkpointed_at=frozen.evidence_cutoff_at - timedelta(seconds=1),
        )


def test_quality_checkpoint_requires_a_collected_market_data_checkpoint(
    tmp_path: Path, valid_packet
) -> None:
    from finance_research_agent.application.quality_checkpoint import (
        checkpoint_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    run = valid_packet.run.model_copy(update={"evidence_cutoff_at": None})
    repository.create(run)
    frozen = repository.freeze_evidence(run.run_id, run.invoked_at + timedelta(seconds=1))

    with pytest.raises(ValueError, match="requires collected market data"):
        checkpoint_market_data_quality(
            repository,
            frozen,
            _quality_result(),
            checkpointed_at=frozen.evidence_cutoff_at + timedelta(seconds=1),
        )
