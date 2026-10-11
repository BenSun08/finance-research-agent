"""Exclusive canonical-file repositories for private shadow evidence."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from fcntl import LOCK_SH, LOCK_UN, flock
from pathlib import Path
from typing import BinaryIO, Literal, cast

from pydantic import PositiveInt, TypeAdapter, ValidationError

from finance_research_agent.application.ports import (
    FeedbackRepository,
    ReleaseEvidenceReader,
    RunRepository,
)
from finance_research_agent.domain.models import Identifier, Sha256, StrictModel
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    OperatorReleaseAttestation,
    ReleaseCheckReceipt,
    ShadowDayObservation,
)

_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
_SHA256_ADAPTER = TypeAdapter(Sha256)
_RELEASE_RECEIPTS_ADAPTER = TypeAdapter(tuple[ReleaseCheckReceipt, ...])
_RELEASE_BATCH_NAME = re.compile(
    r"^release-checks-([0-9a-f]{64})-([0-9a-f]{64})-([1-9][0-9]*)-([1-9][0-9]*)\.json$"
)


class _ReleaseCollectionWatermark(StrictModel):
    """Private pointer to the latest collection result for one exact source."""

    source_commit_sha256: Sha256
    source_tree_sha256: Sha256
    repository_id: Identifier | None = None
    workflow_id: Identifier | None = None
    actions_run_id: Identifier | None = None
    run_attempt: PositiveInt | None = None
    status: Literal["PASS", "FAIL", "PENDING"]


class _ReleaseEvidenceLockState(StrictModel):
    """Durable invalidation markers protected by the collection lock."""

    pending_sources: tuple[tuple[Sha256, Sha256], ...] = ()


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
    """Read the newest private batch for one configured source and workflow."""

    def __init__(
        self,
        root: Path,
        *,
        source_commit_sha256: Sha256,
        source_tree_sha256: Sha256,
        repository_id: Identifier,
        workflow_id: Identifier,
    ) -> None:
        self._root = Path(root)
        try:
            self._source_commit_sha256 = _SHA256_ADAPTER.validate_python(
                source_commit_sha256, strict=True
            )
        except ValidationError as exc:
            raise ValueError("release evidence source commit hash is invalid") from exc
        try:
            self._source_tree_sha256 = _SHA256_ADAPTER.validate_python(
                source_tree_sha256, strict=True
            )
        except ValidationError as exc:
            raise ValueError("release evidence source tree hash is invalid") from exc
        try:
            self._repository_id = _IDENTIFIER_ADAPTER.validate_python(repository_id, strict=True)
        except ValidationError as exc:
            raise ValueError("release evidence repository id is invalid") from exc
        try:
            self._workflow_id = _IDENTIFIER_ADAPTER.validate_python(workflow_id, strict=True)
        except ValidationError as exc:
            raise ValueError("release evidence workflow id is invalid") from exc

    def list_receipts(self) -> tuple[ReleaseCheckReceipt, ...]:
        if not _private_directory_available_or_missing(self._root):
            return ()
        with _release_evidence_read_lock(self._root) as pending_sources:
            if (
                pending_sources is None
                or (
                    self._source_commit_sha256,
                    self._source_tree_sha256,
                )
                in pending_sources
            ):
                return ()
            return self._list_receipts_locked()

    def _list_receipts_locked(self) -> tuple[ReleaseCheckReceipt, ...]:
        watermark_path = _release_collection_watermark_path(
            self._root,
            self._source_commit_sha256,
            self._source_tree_sha256,
        )
        if not watermark_path.exists() and not watermark_path.is_symlink():
            return ()
        watermark = _read_release_collection_watermark(
            watermark_path,
            source_commit_sha256=self._source_commit_sha256,
            source_tree_sha256=self._source_tree_sha256,
            repository_id=self._repository_id,
            workflow_id=self._workflow_id,
        )
        if watermark.status == "PENDING":
            return ()
        if watermark.actions_run_id is None or watermark.run_attempt is None:
            raise ValueError("release evidence watermark is malformed")
        latest = self._root / (
            f"release-checks-{self._source_commit_sha256}-{self._source_tree_sha256}-"
            f"{watermark.actions_run_id}-{watermark.run_attempt}.json"
        )
        if not latest.exists() and not latest.is_symlink():
            return ()
        return _read_release_batch(
            latest,
            repository_id=self._repository_id,
            workflow_id=self._workflow_id,
        )


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


def _release_collection_watermark_path(
    root: Path, source_commit_sha256: str, source_tree_sha256: str
) -> Path:
    return root / f"release-status-{source_commit_sha256}-{source_tree_sha256}.json"


@contextmanager
def _release_evidence_read_lock(
    root: Path,
) -> Iterator[frozenset[tuple[str, str]] | None]:
    lock_path = root / ".release-evidence.lock"
    try:
        descriptor = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        yield None
        return
    except OSError as exc:
        raise ValueError("release evidence collection lock is unavailable") from exc

    with os.fdopen(descriptor, "rb") as lock_file:
        lock_stat = os.fstat(lock_file.fileno())
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_nlink != 1
            or stat.S_IMODE(lock_stat.st_mode) & 0o077
        ):
            raise ValueError("release evidence collection lock is unsafe")
        if not stat.S_IMODE(lock_stat.st_mode) & stat.S_IWUSR or not os.access(lock_path, os.W_OK):
            yield None
            return
        try:
            flock(lock_file.fileno(), LOCK_SH)
        except OSError as exc:
            raise ValueError("release evidence collection lock is unavailable") from exc
        try:
            payload = lock_file.read()
            if not payload:
                yield None
                return
            try:
                state = _read_release_evidence_lock_state(payload)
            except ValueError:
                yield None
            else:
                yield frozenset(state.pending_sources)
        finally:
            flock(lock_file.fileno(), LOCK_UN)


def _read_release_evidence_lock_state(payload: bytes) -> _ReleaseEvidenceLockState:
    if not payload:
        raise ValueError("release evidence collection lock state is malformed")
    try:
        state = _ReleaseEvidenceLockState.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise ValueError("release evidence collection lock state is malformed") from exc
    canonical_sources = tuple(sorted(set(state.pending_sources)))
    if state.pending_sources != canonical_sources or canonical_bytes(state) != payload:
        raise ValueError("release evidence collection lock state is not canonical")
    return state


def _update_release_evidence_pending_source(
    lock_file: BinaryIO,
    *,
    source_commit_sha256: str,
    source_tree_sha256: str,
    pending: bool,
) -> None:
    try:
        lock_file.seek(0)
        current = _read_release_evidence_lock_state(lock_file.read())
        sources = set(current.pending_sources)
        identity = (source_commit_sha256, source_tree_sha256)
        if pending:
            sources.add(identity)
        else:
            sources.discard(identity)
        state = _ReleaseEvidenceLockState(pending_sources=tuple(sorted(sources)))
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(canonical_bytes(state))
        lock_file.flush()
        os.fsync(lock_file.fileno())
    except (OSError, ValueError) as exc:
        raise ValueError("release evidence collection lock state is unavailable") from exc


def _read_release_collection_watermark(
    path: Path,
    *,
    source_commit_sha256: str,
    source_tree_sha256: str,
    repository_id: str,
    workflow_id: str,
) -> _ReleaseCollectionWatermark:
    if path.is_symlink() or not path.is_file():
        raise ValueError("release evidence watermark must be a regular local file")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("release evidence watermark must be private")
    try:
        payload = path.read_bytes()
        watermark = _ReleaseCollectionWatermark.model_validate_json(payload, strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError("release evidence watermark is malformed") from exc
    if payload != canonical_bytes(watermark):
        raise ValueError("release evidence watermark is not in canonical form")
    if (
        watermark.source_commit_sha256 != source_commit_sha256
        or watermark.source_tree_sha256 != source_tree_sha256
    ):
        raise ValueError("release evidence watermark does not match its source")
    if (watermark.repository_id is not None and watermark.repository_id != repository_id) or (
        watermark.workflow_id is not None and watermark.workflow_id != workflow_id
    ):
        raise ValueError(
            "release evidence watermark does not match the configured repository and workflow"
        )
    if watermark.status != "PENDING" and (
        watermark.repository_id is None or watermark.workflow_id is None
    ):
        raise ValueError("release evidence watermark is malformed")
    return watermark


def _read_release_batch(
    path: Path, *, repository_id: str, workflow_id: str
) -> tuple[ReleaseCheckReceipt, ...]:
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
    if any(
        receipt.repository_id != repository_id or receipt.workflow_id != workflow_id
        for receipt in receipts
    ):
        raise ValueError(
            "release evidence receipt does not match the configured repository and workflow"
        )
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
