"""Persistence tests for append-only filesystem feedback records."""

from datetime import UTC, datetime

import pytest

from finance_research_agent.adapters.feedback import FileSystemFeedbackRepository
from finance_research_agent.application.feedback_service import RecordedFeedback
from finance_research_agent.domain.types import canonical_bytes


def _feedback(feedback_id: str) -> RecordedFeedback:
    return RecordedFeedback(
        feedback_id=feedback_id,
        run_id="premarket-2026-09-29-r1",
        bundle_sha256="a" * 64,
        recorded_at=datetime(2026, 9, 29, 13, 0, tzinfo=UTC),
        clarity_score=3,
        evidence_score=4,
        usefulness_score=5,
        notes=None,
        citation_reviews=(),
    )


def test_feedback_records_append_and_reload_as_immutable_snapshots(tmp_path) -> None:
    repository = FileSystemFeedbackRepository(tmp_path / "evaluation")
    first = _feedback("feedback-1")
    second = _feedback("feedback-2")

    repository.append_feedback(first)
    repository.append_feedback(second)

    assert repository.list_feedback() == (first, second)
    assert len(tuple((tmp_path / "evaluation").glob("*.json"))) == 2


def test_feedback_ids_cannot_overwrite_existing_records(tmp_path) -> None:
    repository = FileSystemFeedbackRepository(tmp_path / "evaluation")
    first = _feedback("feedback-1")
    repository.append_feedback(first)

    with pytest.raises(FileExistsError):
        repository.append_feedback(first.model_copy(update={"clarity_score": 1}))

    assert repository.list_feedback() == (first,)


def test_feedback_repository_construction_and_empty_read_do_not_create_root(tmp_path) -> None:
    root = tmp_path / "not-created"
    repository = FileSystemFeedbackRepository(root)

    assert repository.list_feedback() == ()
    assert not root.exists()


def test_feedback_repository_rejects_tampered_records(tmp_path) -> None:
    root = tmp_path / "evaluation"
    root.mkdir()
    (root / "feedback-1.json").write_text("{}", encoding="utf-8")
    repository = FileSystemFeedbackRepository(root)

    with pytest.raises(ValueError, match="feedback record"):
        repository.list_feedback()


def test_feedback_repository_rejects_filename_identifier_mismatch(tmp_path) -> None:
    root = tmp_path / "evaluation"
    repository = FileSystemFeedbackRepository(root)
    repository.append_feedback(_feedback("feedback-1"))
    (root / "feedback-1.json").rename(root / "feedback-2.json")

    with pytest.raises(ValueError, match="filename does not match"):
        repository.list_feedback()


def test_feedback_repository_rejects_a_symlink_root(tmp_path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    repository = FileSystemFeedbackRepository(linked)

    with pytest.raises(ValueError, match="local directory"):
        repository.list_feedback()


def test_legacy_feedback_bytes_remain_canonical_and_are_not_rewritten(tmp_path) -> None:
    root = tmp_path / "evaluation"
    root.mkdir()
    legacy_bytes = (
        b'{"bundle_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"citation_reviews":[],"clarity_score":3,"evidence_score":4,'
        b'"feedback_id":"feedback-legacy","notes":null,'
        b'"recorded_at":"2026-09-29T13:00:00Z",'
        b'"run_id":"premarket-2026-09-29-r1","usefulness_score":5}'
    )
    path = root / "feedback-legacy.json"
    path.write_bytes(legacy_bytes)
    repository = FileSystemFeedbackRepository(root)

    [record] = repository.list_feedback()

    assert record.schema_version == "0.1"
    assert canonical_bytes(record) == legacy_bytes
    assert path.read_bytes() == legacy_bytes
