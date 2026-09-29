"""Append-only local persistence for Product A feedback snapshots."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from finance_research_agent.application.feedback_service import RecordedFeedback
from finance_research_agent.domain.types import canonical_bytes


class FileSystemFeedbackRepository:
    """Persist each immutable feedback item as one exclusively-created JSON file."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def append_feedback(self, feedback: RecordedFeedback) -> None:
        """Atomically add one record without replacing an earlier identifier."""
        feedback = RecordedFeedback.model_validate(feedback, strict=True)
        self._ensure_root()
        target = self._root / f"{feedback.feedback_id}.json"
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".feedback-", suffix=".tmp", dir=self._root
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(canonical_bytes(feedback))
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, target)
            self._fsync_directory()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def list_feedback(self) -> tuple[RecordedFeedback, ...]:
        """Load immutable records; reject symlinks, tampering, and name drift."""
        if not self._root.exists():
            return ()
        if self._root.is_symlink() or not self._root.is_dir():
            raise ValueError("feedback repository root must be a local directory")

        records: list[RecordedFeedback] = []
        for path in sorted(self._root.glob("*.json"), key=lambda item: item.name):
            if path.is_symlink() or not path.is_file():
                raise ValueError("feedback record must be a regular local file")
            try:
                record = RecordedFeedback.model_validate_json(path.read_bytes(), strict=True)
            except (OSError, ValueError) as exc:
                raise ValueError("feedback record is malformed") from exc
            if path.name != f"{record.feedback_id}.json":
                raise ValueError("feedback record filename does not match its identifier")
            records.append(record)
        return tuple(records)

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        if self._root.is_symlink() or not self._root.is_dir():
            raise ValueError("feedback repository root must be a local directory")

    def _fsync_directory(self) -> None:
        descriptor = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = ["FileSystemFeedbackRepository"]
