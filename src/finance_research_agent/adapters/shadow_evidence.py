"""Exclusive canonical-file repositories for private shadow evidence."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter, ValidationError

from finance_research_agent.application.ports import FeedbackRepository, RunRepository
from finance_research_agent.domain.models import Identifier
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    OperatorReleaseAttestation,
    ShadowDayObservation,
)

_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)


class FileSystemShadowObservationRepository:
    """Append-only day notes with trusted run and feedback reference checks."""

    def __init__(
        self,
        root: Path,
        *,
        run_repository: RunRepository,
        feedback_repository: FeedbackRepository,
    ) -> None:
        self._root = Path(root)
        self._run_repository = run_repository
        self._feedback_repository = feedback_repository

    def has_run_id(self, run_id: str) -> bool:
        return self._run_repository.load(run_id) is not None

    def has_feedback_id(self, feedback_id: str) -> bool:
        return any(
            item.feedback_id == feedback_id
            for item in self._feedback_repository.list_feedback()
        )

    def get_observation(self, observation_id: str) -> ShadowDayObservation | None:
        return _read_one(self._root, observation_id, ShadowDayObservation, "shadow observation")

    def append_observation(self, observation: ShadowDayObservation) -> None:
        observation = ShadowDayObservation.model_validate(observation, strict=True)
        _append(self._root, observation, "shadow observation")

    def list_observations(self) -> tuple[ShadowDayObservation, ...]:
        return _read_all(self._root, ShadowDayObservation, "shadow observation")


class FileSystemOperatorReleaseAttestationRepository:
    """Append-only store for self-attested operator check outcomes."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def append_attestation(self, attestation: OperatorReleaseAttestation) -> None:
        attestation = OperatorReleaseAttestation.model_validate(attestation, strict=True)
        _append(self._root, attestation, "operator release attestation")

    def list_attestations(self) -> tuple[OperatorReleaseAttestation, ...]:
        return _read_all(self._root, OperatorReleaseAttestation, "operator release attestation")


def _append(
    root: Path,
    record: ShadowDayObservation | OperatorReleaseAttestation,
    record_kind: str,
) -> None:
    _ensure_root(root, create=True)
    destination = root / f"{record.id}.json"
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".shadow-evidence-", suffix=".tmp", dir=root
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_bytes(record))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
        _fsync_directory(root)
    except FileExistsError:
        raise
    except OSError as exc:
        raise OSError(f"could not append {record_kind}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_one[RecordT: ShadowDayObservation | OperatorReleaseAttestation](
    root: Path,
    record_id: str,
    record_model: type[RecordT],
    record_kind: str,
) -> RecordT | None:
    try:
        record_id = _IDENTIFIER_ADAPTER.validate_python(record_id, strict=True)
    except ValidationError as exc:
        raise ValueError("shadow evidence record identifier is invalid") from exc
    if not _root_is_available_or_missing(root):
        return None
    path = root / f"{record_id}.json"
    if not path.exists() and not path.is_symlink():
        return None
    return _read_file(path, record_model, record_kind)


def _read_all[RecordT: ShadowDayObservation | OperatorReleaseAttestation](
    root: Path,
    record_model: type[RecordT],
    record_kind: str,
) -> tuple[RecordT, ...]:
    if not _root_is_available_or_missing(root):
        return ()
    records = tuple(
        _read_file(path, record_model, record_kind)
        for path in sorted(root.glob("*.json"), key=lambda item: item.name)
    )
    return records


def _read_file[RecordT: ShadowDayObservation | OperatorReleaseAttestation](
    path: Path, record_model: type[RecordT], record_kind: str
) -> RecordT:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{record_kind} record must be a regular local file")
    try:
        payload = path.read_bytes()
        record = record_model.model_validate_json(payload, strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{record_kind} record is malformed") from exc
    if path.name != f"{record.id}.json":
        raise ValueError(f"{record_kind} filename does not match its identifier")
    if payload != canonical_bytes(record):
        raise ValueError(f"{record_kind} record is not in canonical form")
    return cast(RecordT, record)


def _ensure_root(root: Path, *, create: bool) -> None:
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("shadow evidence repository root must be a local directory")


def _root_is_available_or_missing(root: Path) -> bool:
    if root.is_symlink():
        raise ValueError("shadow evidence repository root must be a local directory")
    if not root.exists():
        return False
    _ensure_root(root, create=False)
    return True


def _fsync_directory(root: Path) -> None:
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "FileSystemOperatorReleaseAttestationRepository",
    "FileSystemShadowObservationRepository",
]
