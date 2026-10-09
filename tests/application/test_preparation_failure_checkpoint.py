"""The post-freeze failure path cannot replace protected run state."""

from hashlib import sha256

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.domain.enums import DataQualityStatus, DeliveryStatus, ExecutionStatus
from finance_research_agent.domain.types import FrozenMap
from tests.unit.test_filesystem_store import _context


@pytest.fixture
def frozen(tmp_path):
    repository = FileSystemRunRepository(tmp_path)
    context = _context().model_copy(update={"evidence_cutoff_at": None})
    repository.create(context)
    stored = repository.freeze_evidence(context.run_id, _context().evidence_cutoff_at)
    digest = repository.stage_artifact(context.run_id, "operational_reason", b"INTERNAL_ERROR")
    checkpoint = stored.checkpoints[-1].model_copy(
        update={
            "stage": "PREPARATION_FAILED",
            "data_quality_status": DataQualityStatus.FAIL,
            "artifact_hashes": FrozenMap({"operational_reason": digest}),
            "resumable": False,
        }
    )
    return repository, stored, checkpoint


def test_valid_failure_transition_preserves_frozen_identity(frozen):
    repository, stored, checkpoint = frozen
    repository.checkpoint_if_current(stored.run_id, checkpoint, len(stored.checkpoints))
    result = repository.load(stored.run_id)
    assert result.run == stored.run
    assert result.checkpoints[-1] == checkpoint


@pytest.mark.parametrize(
    "changes",
    [
        {"resumable": True},
        {"execution_status": ExecutionStatus.ANALYZING},
        {"delivery_status": DeliveryStatus.ON_TIME},
        {"data_quality_status": DataQualityStatus.PASS},
        {"artifact_hashes": FrozenMap({})},
        {"artifact_hashes": FrozenMap({"operational_reason": "0" * 64})},
    ],
)
def test_failure_transition_rejects_unbound_or_changed_state(frozen, changes):
    repository, stored, checkpoint = frozen
    with pytest.raises(ValueError):
        repository.checkpoint_if_current(
            stored.run_id, checkpoint.model_copy(update=changes), len(stored.checkpoints)
        )
    assert repository.load(stored.run_id) == stored


def test_failure_transition_rejects_non_closed_reason(frozen, tmp_path):
    repository, stored, checkpoint = frozen
    path = next(tmp_path.rglob("operational_reason.bin"))
    path.write_bytes(b"arbitrary-reason")
    changed = checkpoint.model_copy(
        update={
            "artifact_hashes": FrozenMap(
                {"operational_reason": sha256(path.read_bytes()).hexdigest()}
            ),
        }
    )
    with pytest.raises(ValueError):
        repository.checkpoint_if_current(stored.run_id, changed, len(stored.checkpoints))


def test_failure_transition_rejects_staged_packet(frozen):
    repository, stored, checkpoint = frozen
    repository.stage_artifact(stored.run_id, "research_packet", b"immutable-packet")
    with pytest.raises(ValueError):
        repository.checkpoint_if_current(stored.run_id, checkpoint, len(stored.checkpoints))
