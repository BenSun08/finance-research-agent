"""Persist deterministic market-data quality beside its frozen collection."""

import json
from datetime import datetime
from hashlib import sha256

from finance_research_agent.application.performance_telemetry import (
    checkpoint_with_telemetry,
    load_checkpoint_telemetry,
    load_latest_checkpoint_telemetry,
)
from finance_research_agent.application.ports import RunRepository
from finance_research_agent.domain.enums import ExecutionStatus
from finance_research_agent.domain.models import (
    PerformanceTelemetry,
    RunCheckpoint,
    StoredRun,
)
from finance_research_agent.domain.quality import DataQualityResult
from finance_research_agent.domain.types import FrozenMap

_COLLECTION_ARTIFACT = "market_data_collection"
_QUALITY_ARTIFACT = "data_quality"
_COLLECTION_STAGE = "EVIDENCE_COLLECTED"
_FROZEN_STAGE = "EVIDENCE_FROZEN"
_QUALITY_STAGE = "QUALITY_EVALUATED"


def _canonical_quality_bytes(result: DataQualityResult) -> bytes:
    return json.dumps(
        result.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def checkpoint_market_data_quality(
    repository: RunRepository,
    frozen_run: StoredRun,
    result: DataQualityResult,
    *,
    checkpointed_at: datetime,
    telemetry: PerformanceTelemetry | None = None,
) -> StoredRun:
    """Bind the evaluated status to the frozen collection for safe restart."""
    if not isinstance(frozen_run, StoredRun):
        raise TypeError("frozen_run must be StoredRun")
    if not isinstance(result, DataQualityResult):
        raise TypeError("result must be DataQualityResult")
    cutoff = frozen_run.evidence_cutoff_at
    if cutoff is None or frozen_run.run.evidence_cutoff_at != cutoff:
        raise ValueError("quality checkpoint requires frozen evidence")
    offset = checkpointed_at.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("quality checkpoint time must be timezone-aware UTC")
    if checkpointed_at < cutoff:
        raise ValueError("quality checkpoint cannot precede evidence cutoff")

    stored = repository.load(frozen_run.run_id)
    if stored is None or stored.published:
        raise ValueError("quality checkpoint requires an unpublished stored run")
    if stored != frozen_run:
        raise ValueError("quality checkpoint requires the current stored run")
    if not stored.checkpoints:
        raise ValueError("quality checkpoint requires an EVIDENCE_FROZEN checkpoint")

    latest = stored.checkpoints[-1]
    collection_checkpoint = next(
        (item for item in reversed(stored.checkpoints) if item.stage == _COLLECTION_STAGE),
        None,
    )
    if collection_checkpoint is None:
        raise ValueError("quality checkpoint requires collected market data")
    collection_names = set(collection_checkpoint.artifact_hashes)
    telemetry_names = {
        name for name in collection_names if name.startswith("performance_telemetry_")
    }
    if collection_names != {_COLLECTION_ARTIFACT} | telemetry_names:
        raise ValueError("collected checkpoint is missing its collection artifact hash")
    if telemetry_names:
        load_checkpoint_telemetry(repository, collection_checkpoint)
    collection_digest = collection_checkpoint.artifact_hashes[_COLLECTION_ARTIFACT]
    collection_bytes = repository.read_staged_artifact(
        stored.run_id, _COLLECTION_ARTIFACT
    )
    if collection_bytes is None or sha256(collection_bytes).hexdigest() != collection_digest:
        raise ValueError("collected market data does not match its checkpoint hash")

    quality_bytes = _canonical_quality_bytes(result)
    quality_digest = sha256(quality_bytes).hexdigest()
    if telemetry is None:
        telemetry = load_latest_checkpoint_telemetry(repository, stored)
    if latest.stage == _QUALITY_STAGE:
        staged_quality = repository.read_staged_artifact(stored.run_id, _QUALITY_ARTIFACT)
        if (
            latest.execution_status is not ExecutionStatus.ANALYZING
            or latest.data_quality_status is not result.status
            or latest.evidence_cutoff_at != cutoff
            or {
                name: digest
                for name, digest in latest.artifact_hashes.items()
                if not name.startswith("performance_telemetry_")
            }
            != {
                _COLLECTION_ARTIFACT: collection_digest,
                _QUALITY_ARTIFACT: quality_digest,
            }
            or staged_quality != quality_bytes
            or latest.resumable
        ):
            raise ValueError("resume result differs from the quality checkpoint")
        if telemetry is not None and load_checkpoint_telemetry(repository, latest) != telemetry:
            raise ValueError("resume telemetry differs from the quality checkpoint")
        return stored
    if latest.stage != _FROZEN_STAGE or latest.evidence_cutoff_at != cutoff:
        raise ValueError("quality checkpoint must follow EVIDENCE_FROZEN")
    if checkpointed_at < latest.written_at:
        raise ValueError("quality checkpoint cannot precede current checkpoint")

    staged_digest = repository.stage_artifact(stored.run_id, _QUALITY_ARTIFACT, quality_bytes)
    if staged_digest != quality_digest:
        raise RuntimeError("repository returned an invalid quality artifact digest")
    checkpoint = RunCheckpoint(
        run_id=stored.run_id,
        stage=_QUALITY_STAGE,
        execution_status=ExecutionStatus.ANALYZING,
        data_quality_status=result.status,
        delivery_status=latest.delivery_status,
        written_at=checkpointed_at,
        evidence_cutoff_at=cutoff,
        artifact_hashes=FrozenMap(
            {
                _COLLECTION_ARTIFACT: collection_digest,
                _QUALITY_ARTIFACT: quality_digest,
            }
        ),
        resumable=False,
    )
    if telemetry is not None:
        checkpoint = checkpoint_with_telemetry(repository, checkpoint, telemetry)
    repository.checkpoint_if_current(
        stored.run_id,
        checkpoint,
        expected_count=len(stored.checkpoints),
    )
    updated = repository.load(stored.run_id)
    if updated is None or updated.checkpoints[-1].stage != _QUALITY_STAGE:
        raise RuntimeError("quality checkpoint could not be reloaded")
    return updated
