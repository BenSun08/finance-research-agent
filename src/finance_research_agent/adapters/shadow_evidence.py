"""Exclusive canonical-file repositories for private shadow evidence."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter, ValidationError

from finance_research_agent.application.ports import (
    FeedbackRepository,
    ReleaseEvidenceReader,
    RunRepository,
)
from finance_research_agent.domain.models import Identifier
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    OperatorReleaseAttestation,
    ReleaseCheckReceipt,
    ShadowDayObservation,
)

_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
_RELEASE_RECEIPTS_ADAPTER = TypeAdapter(tuple[ReleaseCheckReceipt, ...])
_RELEASE_BATCH_NAME = re.compile(
    r"^release-checks-([0-9a-f]{64})-([0-9a-f]{64})-([1-9][0-9]*)-([1-9][0-9]*)\.json$"
)


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
            item.feedback_id == feedback_id for item in self._feedback_repository.list_feedback()
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


class FileSystemReleaseEvidenceReader(ReleaseEvidenceReader):
    """Read the newest private, canonical batch written by the release collector."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def list_receipts(self) -> tuple[ReleaseCheckReceipt, ...]:
        if not _private_directory_available_or_missing(self._root):
            return ()
        candidates = tuple(
            (path, _release_batch_filename_parts(path))
            for path in sorted(self._root.glob("release-checks-*.json"), key=lambda item: item.name)
        )
        if not candidates:
            return ()
        latest_order = max((int(parts[2]), int(parts[3])) for _path, parts in candidates)
        latest = tuple(
            path for path, parts in candidates if (int(parts[2]), int(parts[3])) == latest_order
        )
        if len(latest) != 1:
            raise ValueError("multiple release evidence batches share the latest Actions attempt")
        return _read_release_batch(latest[0])


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


def _private_directory_available_or_missing(root: Path) -> bool:
    if root.is_symlink():
        raise ValueError("release evidence root must be a private local directory")
    if not root.exists():
        return False
    if not root.is_dir() or stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise ValueError("release evidence root must be a private local directory")
    return True


def _read_release_batch(path: Path) -> tuple[ReleaseCheckReceipt, ...]:
    commit_sha256, tree_sha256, run_id, attempt = _release_batch_filename_parts(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("release evidence batch must be a regular local file")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("release evidence batch must be private")
    try:
        payload = path.read_bytes()
        receipts = _RELEASE_RECEIPTS_ADAPTER.validate_json(payload, strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError("release evidence batch is malformed") from exc
    if not receipts:
        raise ValueError("release evidence batch must contain at least one receipt")
    if _release_batch_bytes(receipts) != payload:
        raise ValueError("release evidence batch is not in canonical form")
    if any(
        receipt.source_commit_sha256 != commit_sha256
        or receipt.source_tree_sha256 != tree_sha256
        or receipt.actions_run_id != run_id
        or not _release_step_matches_batch(receipt, run_id, attempt)
        for receipt in receipts
    ):
        raise ValueError("release evidence receipt does not match its batch identity")
    return receipts


def _release_batch_filename_parts(path: Path) -> tuple[str, str, str, str]:
    match = _RELEASE_BATCH_NAME.fullmatch(path.name)
    if match is None:
        raise ValueError("release evidence filename is invalid")
    return cast(tuple[str, str, str, str], match.groups())


def _release_step_matches_batch(receipt: ReleaseCheckReceipt, run_id: str, attempt: str) -> bool:
    parts = receipt.actions_step_id.split(":")
    if len(parts) != 2:
        return False
    receipt_attempt, step_number = parts
    return (
        receipt_attempt == attempt
        and step_number.isascii()
        and step_number.isdecimal()
        and int(step_number) > 0
        and receipt.id == f"gha:{run_id}:{receipt.actions_job_id}:{receipt_attempt}:{step_number}"
    )


def _release_batch_bytes(receipts: tuple[ReleaseCheckReceipt, ...]) -> bytes:
    records = [item.model_dump(mode="json") for item in receipts]
    return json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _fsync_directory(root: Path) -> None:
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "FileSystemReleaseEvidenceReader",
    "FileSystemOperatorReleaseAttestationRepository",
    "FileSystemShadowObservationRepository",
]
