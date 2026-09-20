"""Durable, path-safe storage primitives for immutable Product A runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from threading import RLock

from finance_research_agent.domain.enums import (
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    InvocationType,
    RunType,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.market_calendar import format_run_id
from finance_research_agent.domain.models import (
    ComponentVersions,
    ConfigurationSnapshot,
    PublishedArtifact,
    PublishedRunBundle,
    RunCheckpoint,
    RunContext,
    RunKey,
    RunLease,
    StoredRun,
)
from finance_research_agent.domain.types import FrozenMap, canonical_bytes, utc_datetime

_RUN_ID = re.compile(r"^premarket-(\d{4}-\d{2}-\d{2})-r([1-9]\d*)$")
_LEASE_DURATION = timedelta(minutes=15)
_LEASE_PROCESS_LOCK = RLock()


class PathNotAllowedError(ValueError):
    """Raised when an externally supplied identifier cannot address storage."""

    code = ErrorCode.PATH_NOT_ALLOWED


class LeaseHeldError(RuntimeError):
    """Raised when a logical run family has a live lease owned by another token."""


class PublicationError(RuntimeError):
    """Raised when complete publication cannot be committed."""


def _path_error() -> PathNotAllowedError:
    return PathNotAllowedError(f"{ErrorCode.PATH_NOT_ALLOWED}: unsafe run identifier")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class FileSystemRunRepository:
    """Store run state beneath one resolved data root.

    This adapter deliberately owns no provider, collection, analysis, or
    publication-service behavior. It only persists caller-supplied values and
    makes the final directory visible after a complete atomic rename.
    """

    inject_failure_before_rename = False
    inject_failure_during_index_update = False

    def __init__(self, data_root: Path) -> None:
        self.root = Path(data_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("config", "runs", "reports", "cache", "diagnostics", "logs"):
            self._safe_path(name).mkdir(parents=True, exist_ok=True)

    def _safe_path(self, *parts: str) -> Path:
        candidate = self.root.joinpath(*parts).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise _path_error() from error
        return candidate

    @staticmethod
    def _validated_run_id(run_id: str) -> tuple[date, int]:
        if not isinstance(run_id, str) or any(
            ord(character) < 32 or ord(character) == 127 for character in run_id
        ):
            raise _path_error()
        if Path(run_id).is_absolute() or "/" in run_id or "\\" in run_id or ".." in run_id:
            raise _path_error()
        match = _RUN_ID.fullmatch(run_id)
        if match is None:
            raise _path_error()
        try:
            parsed_date = date.fromisoformat(match.group(1))
            revision = int(match.group(2))
        except ValueError as error:
            raise _path_error() from error
        return parsed_date, revision

    def _paths(self, run_id: str) -> tuple[Path, Path, Path]:
        market_date, _ = self._validated_run_id(run_id)
        year = f"{market_date.year:04d}"
        day = market_date.isoformat()
        staging = self._safe_path("runs", year, day, ".staging", run_id)
        final = self._safe_path("runs", year, day, run_id)
        lease = self._safe_path("runs", year, day, ".lease.json")
        return staging, final, lease

    def _lease_lock_path(self, market_date: date) -> Path:
        return self._safe_path(
            "runs", f"{market_date.year:04d}", market_date.isoformat(), ".lease.lock"
        )

    @contextmanager
    def _lease_lock(self, market_date: date) -> Iterator[None]:
        lock_path = self._lease_lock_path(market_date)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with _LEASE_PROCESS_LOCK, lock_path.open("a+b") as handle:
            flock(handle.fileno(), LOCK_EX)
            try:
                yield
            finally:
                flock(handle.fileno(), LOCK_UN)

    @staticmethod
    def _require_utc(value: datetime) -> datetime:
        try:
            return utc_datetime(value)
        except (TypeError, ValueError) as error:
            raise ValueError("timestamp must be timezone-aware UTC with zero offset") from error

    def _atomic_write(self, target: Path, payload: bytes) -> None:
        target = target.resolve()
        try:
            target.relative_to(self.root)
        except ValueError as error:
            raise _path_error() from error
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        self._fsync_directory(target.parent)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _confined_path(self, parent: Path, *parts: str) -> Path:
        resolved_parent = parent.resolve()
        try:
            resolved_parent.relative_to(self.root)
        except ValueError as error:
            raise _path_error() from error
        target = parent.joinpath(*parts).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as error:
            raise _path_error() from error
        return target

    def _read_confined(self, parent: Path, *parts: str) -> bytes:
        target = self._confined_path(parent, *parts)
        if not target.is_file():
            raise FileNotFoundError(target)
        return target.read_bytes()

    def _default_context(self, market_date: date, revision: int, now: datetime) -> RunContext:
        versions = ComponentVersions(
            core_version="0.5.0.dev0",
            mcp_contract_version="0.1",
            plugin_version="0.1",
            skill_version="0.1",
            prompt_version="0.1",
            report_template_version="0.1",
            schema_versions=FrozenMap({"run-context": "0.1"}),
            watchlist_version="0",
            regime_policy_version="0",
            setup_policy_version="0",
            risk_policy_version="0",
            source_policy_version="0",
        )
        configuration = ConfigurationSnapshot(
            content_hash_sha256="0" * 64,
            file_hashes=FrozenMap({}),
            watchlist_version="0",
            regime_policy_version="0",
            setup_policy_version="0",
            risk_policy_version="0",
            source_policy_version="0",
        )
        return RunContext(
            run_id=format_run_id(market_date, revision),
            run_type=RunType.PREMARKET,
            market_date=market_date,
            revision=revision,
            invoked_at=now,
            evidence_cutoff_at=now,
            execution_status=ExecutionStatus.CREATED,
            data_quality_status=DataQualityStatus.PASS,
            delivery_status=DeliveryStatus.MANUAL,
            configuration_snapshot=configuration,
            core_version=versions.core_version,
            mcp_contract_version=versions.mcp_contract_version,
            plugin_version=versions.plugin_version,
            skill_version=versions.skill_version,
            prompt_version=versions.prompt_version,
            report_template_version=versions.report_template_version,
            schema_versions=versions.schema_versions,
        )

    def _load_context_from(self, run_id: str, path: Path) -> RunContext | None:
        try:
            payload = self._read_confined(path, "run.json")
        except FileNotFoundError:
            return None
        return RunContext.model_validate_json(payload)

    def _existing_revision_ids(self, market_date: date) -> tuple[str, ...]:
        year = f"{market_date.year:04d}"
        day = market_date.isoformat()
        family = self._safe_path("runs", year, day)
        result: set[str] = set()
        for parent in (family, family / ".staging"):
            if not parent.is_dir():
                continue
            for child in parent.iterdir():
                if child.name == ".staging":
                    continue
                if _RUN_ID.fullmatch(child.name):
                    self._paths(child.name)
                    result.add(child.name)
        return tuple(sorted(result))

    def allocate_revision(
        self, market_date: date, invocation: InvocationType, now: datetime
    ) -> RunContext:
        now = self._require_utc(now)
        if not isinstance(invocation, InvocationType):
            raise ValueError("invocation must be a declared InvocationType")
        existing = self._existing_revision_ids(market_date)
        if invocation is InvocationType.SCHEDULED:
            revision = 1
            run_id = format_run_id(market_date, revision)
            if run_id in existing:
                loaded = self.load(run_id)
                if loaded is not None:
                    return loaded.run
        else:
            revision = max(
                (self._validated_run_id(run_id)[1] for run_id in existing), default=0
            ) + 1
        context = self._default_context(market_date, revision, now)
        self.create(context)
        return context

    def create(self, context: RunContext) -> None:
        staging, final, _ = self._paths(context.run_id)
        if final.exists():
            raise PublicationError("immutable run revision is already published")
        if staging.exists():
            existing = self._load_context_from(context.run_id, staging)
            if existing is not None and existing != context:
                raise PublicationError("immutable run revision already exists")
            if existing is not None:
                return
        staging.mkdir(parents=True, exist_ok=True)
        self._atomic_write(staging / "run.json", canonical_bytes(context))

    def _load_from_path(self, run_id: str, path: Path, published: bool) -> StoredRun | None:
        context = self._load_context_from(run_id, path)
        if context is None:
            return None
        checkpoint_dir = self._confined_path(path, "checkpoints")
        checkpoints: list[RunCheckpoint] = []
        if checkpoint_dir.is_dir():
            for checkpoint_path in sorted(checkpoint_dir.glob("*.json")):
                checkpoint_path = self._confined_path(checkpoint_dir, checkpoint_path.name)
                checkpoints.append(
                    RunCheckpoint.model_validate_json(checkpoint_path.read_bytes())
                )
        cutoff: datetime | None = None
        try:
            cutoff_bytes = self._read_confined(path, "frozen-evidence.json")
        except FileNotFoundError:
            cutoff_bytes = None
        if cutoff_bytes is not None:
            cutoff_payload = json.loads(cutoff_bytes)
            cutoff = self._require_utc(datetime.fromisoformat(cutoff_payload["evidence_cutoff_at"]))
        return StoredRun(
            run=context,
            checkpoints=tuple(checkpoints),
            evidence_cutoff_at=cutoff,
            published=published,
        )

    @staticmethod
    def _stored_packet_hash(stored: StoredRun) -> str | None:
        for checkpoint in reversed(stored.checkpoints):
            if "research_packet" in checkpoint.artifact_hashes:
                return checkpoint.artifact_hashes["research_packet"]
        return None

    def load(self, run_id: str) -> StoredRun | None:
        staging, final, _ = self._paths(run_id)
        if staging.is_dir():
            return self._load_from_path(run_id, staging, False)
        if final.is_dir() and self._published_payloads(run_id, final) is not None:
            return self._load_from_path(run_id, final, True)
        return None

    def checkpoint(self, run_id: str, checkpoint: RunCheckpoint) -> None:
        staging, _, _ = self._paths(run_id)
        if checkpoint.run_id != run_id:
            raise ValueError("checkpoint run_id must match the stored run")
        stored = self.load(run_id)
        if stored is None or not staging.is_dir():
            raise ValueError("run staging does not exist")
        if stored.evidence_cutoff_at is not None and checkpoint.evidence_cutoff_at not in (
            None,
            stored.evidence_cutoff_at,
        ):
            raise ValueError(f"{ErrorCode.EVIDENCE_CUTOFF_VIOLATION}: new revision required")
        if stored.evidence_cutoff_at is not None:
            if checkpoint.evidence_cutoff_at != stored.evidence_cutoff_at:
                raise ValueError(f"{ErrorCode.EVIDENCE_CUTOFF_VIOLATION}: new revision required")
            if checkpoint.stage != "EVIDENCE_FROZEN":
                stored_packet_hash = self._stored_packet_hash(stored)
                valid_retry = (
                    checkpoint.stage in {"AWAITING_SYNTHESIS", "VALIDATING"}
                    and checkpoint.execution_status.value == checkpoint.stage
                    and not checkpoint.resumable
                    and tuple(checkpoint.artifact_hashes) == ("research_packet",)
                    and stored_packet_hash is not None
                    and checkpoint.artifact_hashes["research_packet"] == stored_packet_hash
                )
                if not valid_retry:
                    raise ValueError(
                        f"{ErrorCode.EVIDENCE_CUTOFF_VIOLATION}: new revision required"
                    )
        checkpoint_dir = self._safe_path(
            "runs", f"{stored.run.market_date.year:04d}", stored.run.market_date.isoformat(),
            ".staging", run_id, "checkpoints"
        )
        sequence = len(tuple(checkpoint_dir.glob("*.json"))) + 1 if checkpoint_dir.exists() else 1
        target = checkpoint_dir / f"{sequence:04d}-{checkpoint.stage}.json"
        self._atomic_write(target, canonical_bytes(checkpoint))

    def freeze_evidence(self, run_id: str, cutoff_at: datetime) -> StoredRun:
        staging, _, _ = self._paths(run_id)
        stored = self.load(run_id)
        if stored is None or not staging.is_dir():
            raise ValueError("run staging does not exist")
        if stored.evidence_cutoff_at is not None:
            raise ValueError("evidence cutoff is immutable; create a new revision")
        cutoff_at = self._require_utc(cutoff_at)
        self._atomic_write(
            staging / "frozen-evidence.json",
            json.dumps(
                {"evidence_cutoff_at": cutoff_at.isoformat().replace("+00:00", "Z")},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        self.checkpoint(
            run_id,
            RunCheckpoint(
                run_id=run_id,
                stage="EVIDENCE_FROZEN",
                execution_status=stored.run.execution_status,
                written_at=cutoff_at,
                evidence_cutoff_at=cutoff_at,
                artifact_hashes=FrozenMap({}),
                resumable=False,
            ),
        )
        result = self.load(run_id)
        assert result is not None
        return result

    def acquire_lease(self, key: RunKey, now: datetime) -> RunLease:
        now = self._require_utc(now)
        market_date = key.market_date
        _, _, lease_path = self._paths(format_run_id(market_date, 1))
        with self._lease_lock(market_date):
            if lease_path.is_file():
                current = RunLease.model_validate_json(lease_path.read_bytes())
                if current.expires_at > now:
                    raise LeaseHeldError("live run lease is already held")
                diagnostic = self._safe_path(
                    "diagnostics", "leases", f"{market_date.year:04d}", market_date.isoformat(),
                    f"{current.token}.json"
                )
                self._atomic_write(diagnostic, lease_path.read_bytes())
            lease = RunLease(
                key=key,
                token=uuid.uuid4().hex,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now + _LEASE_DURATION,
                process_id=os.getpid(),
                host=os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or "localhost",
            )
            self._atomic_write(lease_path, canonical_bytes(lease))
            return lease

    def heartbeat(self, lease: RunLease, now: datetime) -> RunLease:
        now = self._require_utc(now)
        _, _, lease_path = self._paths(format_run_id(lease.key.market_date, 1))
        if not lease_path.is_file():
            raise LeaseHeldError("run lease is not present")
        current = RunLease.model_validate_json(lease_path.read_bytes())
        if current.token != lease.token or current.expires_at <= now:
            raise LeaseHeldError("run lease is no longer owned")
        renewed = RunLease(
            key=current.key,
            token=current.token,
            acquired_at=current.acquired_at,
            heartbeat_at=now,
            expires_at=now + current.duration,
            process_id=current.process_id,
            host=current.host,
        )
        self._atomic_write(lease_path, canonical_bytes(renewed))
        return renewed

    def _publication_paths(self, run_id: str) -> tuple[Path, Path, Path, Path]:
        staging, final, _ = self._paths(run_id)
        market_date, _ = self._validated_run_id(run_id)
        report_root = self._safe_path(
            "reports", f"{market_date.year:04d}", market_date.isoformat()
        )
        return staging, final, report_root / "index.json", report_root / "latest.json"

    def _orphan_path(self, run_id: str) -> Path:
        market_date, _ = self._validated_run_id(run_id)
        return self._safe_path(
            "diagnostics", "orphans", f"{market_date.year:04d}", market_date.isoformat(), run_id
        )

    def _quarantine_orphan(self, run_id: str, final: Path) -> None:
        orphan = self._orphan_path(run_id)
        if orphan.exists():
            raise PublicationError("diagnostic orphan already exists")
        orphan.parent.mkdir(parents=True, exist_ok=True)
        os.replace(final, orphan)
        self._fsync_directory(orphan.parent)
        self._fsync_directory(final.parent)

    def _published_payloads(self, run_id: str, final: Path) -> tuple[bytes, bytes] | None:
        market_date, _ = self._validated_run_id(run_id)
        report_root = self._safe_path(
            "reports", f"{market_date.year:04d}", market_date.isoformat()
        )
        try:
            index_payload = json.loads(self._read_confined(report_root, "index.json"))
            index_entry = index_payload[run_id]
            report_bytes = self._read_confined(final, "report.md")
            bundle_bytes = self._read_confined(final, "bundle.json")
            if index_entry["markdown_sha256"] != _sha256(report_bytes):
                return None
            if index_entry["bundle_sha256"] != _sha256(bundle_bytes):
                return None
        except FileNotFoundError:
            return None
        except PathNotAllowedError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return report_bytes, bundle_bytes

    def publish_atomically(self, bundle: PublishedRunBundle) -> PublishedArtifact:
        staging, final, index_path, latest_path = self._publication_paths(bundle.run.run_id)
        if not staging.is_dir():
            raise PublicationError("run-specific staging directory is absent")
        if final.exists():
            raise PublicationError("immutable final run directory already exists")
        staged_context = self._load_context_from(bundle.run.run_id, staging)
        if staged_context is None:
            raise PublicationError("staged run context is absent")
        if staged_context != bundle.run:
            raise PublicationError("PublishedRunBundle RunContext differs from staged RunContext")
        report_bytes = bundle.report_markdown.encode("utf-8")
        markdown_sha256 = _sha256(report_bytes)
        if bundle.markdown_sha256 is None:
            raise PublicationError("declared markdown SHA-256 is required")
        if bundle.markdown_sha256 != markdown_sha256:
            raise PublicationError("report SHA-256 does not match canonical report bytes")
        bundle_bytes = canonical_bytes(bundle)
        bundle_sha256 = _sha256(bundle_bytes)
        self._atomic_write(staging / "bundle.json", bundle_bytes)
        self._atomic_write(staging / "report.md", report_bytes)
        if _sha256((staging / "bundle.json").read_bytes()) != bundle_sha256:
            raise PublicationError("bundle SHA-256 changed before publication")
        if _sha256((staging / "report.md").read_bytes()) != markdown_sha256:
            raise PublicationError("report SHA-256 changed before publication")
        self._fsync_directory(staging)
        self._fsync_directory(staging.parent)
        if self.inject_failure_before_rename:
            raise PublicationError("injected failure before atomic publication rename")
        renamed = False
        try:
            os.replace(staging, final)
            renamed = True
            self._fsync_directory(final.parent)
            if self.inject_failure_during_index_update:
                raise OSError("injected failure during publication index update")
            index_payload: dict[str, dict[str, str]] = {}
            try:
                index_bytes = self._read_confined(index_path.parent, index_path.name)
            except FileNotFoundError:
                index_bytes = None
            if index_bytes is not None:
                index_payload = json.loads(index_bytes)
            index_payload[bundle.run.run_id] = {
                "bundle_sha256": bundle_sha256,
                "markdown_sha256": markdown_sha256,
            }
            latest_run_id = max(
                index_payload,
                key=lambda run_id: self._validated_run_id(run_id)[1],
            )
            self._atomic_write(
                index_path,
                json.dumps(index_payload, sort_keys=True, separators=(",", ":")).encode(),
            )
            self._atomic_write(
                latest_path,
                json.dumps({"run_id": latest_run_id}, separators=(",", ":")).encode(),
            )
        except Exception as error:
            if renamed and final.exists():
                try:
                    self._quarantine_orphan(bundle.run.run_id, final)
                except Exception as quarantine_error:
                    raise PublicationError(
                        "publication index update failed and orphan quarantine failed"
                    ) from quarantine_error
                if isinstance(error, PathNotAllowedError):
                    raise
                raise PublicationError(
                    "publication index update failed; orphan quarantined"
                ) from error
            if isinstance(error, PathNotAllowedError):
                raise
            raise PublicationError("publication index update failed") from error
        return PublishedArtifact(
            run_id=bundle.run.run_id,
            bundle_sha256=bundle_sha256,
            markdown_sha256=markdown_sha256,
            published_at=datetime.now(UTC),
        )

    def get_latest(self, market_date: date) -> str | None:
        report_root = self._safe_path(
            "reports", f"{market_date.year:04d}", market_date.isoformat()
        )
        try:
            latest_payload = json.loads(self._read_confined(report_root, "latest.json"))
        except FileNotFoundError:
            return None
        except PathNotAllowedError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        try:
            run_id = latest_payload["run_id"]
            stored_date, _ = self._validated_run_id(run_id)
            if stored_date != market_date:
                return None
            stored = self.load(run_id)
        except PathNotAllowedError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return (
            run_id
            if stored is not None and stored.published and self.get_report(run_id) is not None
            else None
        )

    def get_report(self, run_id: str) -> str | None:
        _, final, _ = self._paths(run_id)
        payloads = self._published_payloads(run_id, final)
        if payloads is None:
            return None
        report_bytes, _ = payloads
        try:
            return report_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def load_published_bundle(self, run_id: str) -> PublishedRunBundle | None:
        _, final, _ = self._paths(run_id)
        payloads = self._published_payloads(run_id, final)
        if payloads is None:
            return None
        _, bundle_bytes = payloads
        return PublishedRunBundle.model_validate_json(bundle_bytes)

    def diagnostic_staging_exists(self, run_id: str) -> bool:
        staging, _, _ = self._paths(run_id)
        return staging.is_dir()

    def diagnostic_orphan_exists(self, run_id: str) -> bool:
        return self._orphan_path(run_id).is_dir()
