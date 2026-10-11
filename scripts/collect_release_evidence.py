"""Collect exact-head GitHub Actions step receipts into the private local data root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from typing import Literal, Protocol, cast

from finance_research_agent.adapters.shadow_evidence import (
    _release_collection_watermark_path,
    _ReleaseCollectionWatermark,
)
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    ReleaseCheckConclusion,
    ReleaseCheckId,
    ReleaseCheckReceipt,
)
from finance_research_agent.settings import Settings

REPOSITORY = "BenSun08/finance-research-agent"
WORKFLOW_FILE = "ci.yml"
PYTHON_VERSIONS = ("3.12", "3.13", "3.14")
CI_STEP_NAMES = {
    ReleaseCheckId.FORMAT: "Check formatting",
    ReleaseCheckId.LINT: "Check Ruff",
    ReleaseCheckId.TYPES: "Check mypy",
    ReleaseCheckId.UNIT_AND_INTEGRATION_TESTS: "Check unit and integration tests",
    ReleaseCheckId.SCENARIO_AND_FAILURE_MATRIX: "Check scenarios and failure matrix",
    ReleaseCheckId.BRANCH_COVERAGE: "Check whole-package coverage",
    ReleaseCheckId.SCHEMA_DRIFT: "Check generated schemas",
    ReleaseCheckId.DOCS_AND_EXAMPLES: "Check docs and examples",
    ReleaseCheckId.SECRET_SCAN: "Check tracked secrets",
    ReleaseCheckId.NETWORK_BOUNDARY: "Check offline network boundary",
    ReleaseCheckId.PLUGIN_WHEEL_SDIST: "Check plugin wheel and sdist",
    ReleaseCheckId.FRESH_WHEEL_INSTALL: "Check fresh wheel installation",
    ReleaseCheckId.DOCTOR: "Check local doctor",
    ReleaseCheckId.COMPATIBILITY: "Check compatibility consumers",
}
_GIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_JOB_NAME = re.compile(r"^verify \((3\.12|3\.13|3\.14)\)$")
_ROOT = Path(__file__).resolve().parents[1]


class ActionsApiReader(Protocol):
    """Read GitHub API JSON from a fixed, read-only client boundary."""

    def get_json(self, endpoint: str, query: Mapping[str, str]) -> object: ...


class CollectionUnavailable(RuntimeError):
    """A bounded local or GitHub read could not provide trustworthy evidence."""


@dataclass(frozen=True)
class SourceIdentity:
    git_commit: str
    commit_sha256: str
    tree_sha256: str


@dataclass(frozen=True)
class ActionsRunIdentity:
    run_id: str
    workflow_id: str
    repository_id: str


@dataclass(frozen=True)
class ReleaseCollection:
    source: SourceIdentity
    actions_run_id: str | None
    run_attempt: int | None
    receipts: tuple[ReleaseCheckReceipt, ...]
    missing: tuple[tuple[ReleaseCheckId, str], ...]
    pending_reason: str | None = None

    @property
    def status(self) -> Literal["PASS", "FAIL", "PENDING"]:
        if any(item.conclusion is not ReleaseCheckConclusion.SUCCESS for item in self.receipts):
            return "FAIL"
        if self.missing or self.pending_reason is not None:
            return "PENDING"
        return "PASS"


class _GhApiReader:
    def get_json(self, endpoint: str, query: Mapping[str, str]) -> object:
        command = ["gh", "api", "--method", "GET", "--paginate", "--slurp", endpoint]
        for name, value in sorted(query.items()):
            command.extend(("-f", f"{name}={value}"))
        try:
            result = subprocess.run(
                command,
                cwd=_ROOT,
                capture_output=True,
                check=False,
                timeout=30,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CollectionUnavailable("GitHub API is unavailable") from exc
        if result.returncode != 0:
            raise CollectionUnavailable("GitHub API is unavailable")
        try:
            return json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CollectionUnavailable("GitHub API response is malformed") from exc


def _git_output(*arguments: str, binary: bool = False) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=_ROOT,
            capture_output=True,
            check=False,
            timeout=5,
            text=not binary,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectionUnavailable("local Git identity is unavailable") from exc
    if result.returncode != 0:
        raise CollectionUnavailable("local Git identity is unavailable")
    if binary:
        return cast(bytes, result.stdout)
    return cast(str, result.stdout).rstrip("\r\n")


def _source_identity(source_commit: str) -> SourceIdentity:
    if source_commit == "HEAD":
        commit = cast(str, _git_output("rev-parse", "--verify", "HEAD^{commit}"))
    elif _GIT_SHA.fullmatch(source_commit):
        commit = cast(
            str,
            _git_output("rev-parse", "--verify", f"{source_commit}^{{commit}}"),
        )
        if commit != source_commit:
            raise CollectionUnavailable("requested Git commit is unavailable")
    else:
        raise CollectionUnavailable("source commit must be HEAD or a full Git object id")

    tree = cast(str, _git_output("rev-parse", "--verify", f"{commit}^{{tree}}"))
    commit_content = cast(bytes, _git_output("cat-file", "commit", commit, binary=True))
    tree_content = cast(bytes, _git_output("cat-file", "tree", tree, binary=True))
    return SourceIdentity(
        git_commit=commit,
        commit_sha256=hashlib.sha256(commit_content).hexdigest(),
        tree_sha256=hashlib.sha256(tree_content).hexdigest(),
    )


def _pages(payload: object) -> tuple[dict[str, object], ...]:
    values: tuple[object, ...]
    if isinstance(payload, list):
        values = tuple(payload)
    elif isinstance(payload, dict):
        values = (payload,)
    else:
        raise CollectionUnavailable("GitHub API response is malformed")
    if any(not isinstance(item, dict) for item in values):
        raise CollectionUnavailable("GitHub API response is malformed")
    return cast(tuple[dict[str, object], ...], values)


def _records(pages: tuple[dict[str, object], ...], field: str) -> tuple[dict[str, object], ...]:
    values: list[dict[str, object]] = []
    for page in pages:
        items = page.get(field)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise CollectionUnavailable("GitHub API response is malformed")
        values.extend(cast(list[dict[str, object]], items))
    return tuple(values)


def _positive_identifier(value: object) -> str | None:
    if type(value) not in (str, int):
        return None
    text = str(value)
    if not text.isascii() or not text.isdecimal() or int(text) <= 0:
        return None
    return text


def _actions_run_identity(run: dict[str, object]) -> ActionsRunIdentity | None:
    repository = run.get("repository")
    repository_id = (
        _positive_identifier(repository.get("id")) if isinstance(repository, dict) else None
    )
    run_id = _positive_identifier(run.get("id"))
    workflow_id = _positive_identifier(run.get("workflow_id"))
    if run_id is None or workflow_id is None or repository_id is None:
        return None
    return ActionsRunIdentity(
        run_id=run_id,
        workflow_id=workflow_id,
        repository_id=repository_id,
    )


def _missing_matrix() -> tuple[tuple[ReleaseCheckId, str], ...]:
    return tuple((check_id, version) for check_id in ReleaseCheckId for version in PYTHON_VERSIONS)


def _pending_collection(source: SourceIdentity, reason: str) -> ReleaseCollection:
    return ReleaseCollection(
        source=source,
        actions_run_id=None,
        run_attempt=None,
        receipts=(),
        missing=_missing_matrix(),
        pending_reason=reason,
    )


def _selected_run(
    pages: tuple[dict[str, object], ...], source_commit: str
) -> dict[str, object] | None:
    candidates: list[dict[str, object]] = []
    for run in _records(pages, "workflow_runs"):
        repository = run.get("repository")
        if (
            run.get("head_sha") == source_commit
            and isinstance(repository, dict)
            and repository.get("full_name") == REPOSITORY
            and _actions_run_identity(run) is not None
        ):
            candidates.append(run)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda run: (
            int(_positive_identifier(run.get("run_number")) or "0"),
            int(_positive_identifier(run.get("id")) or "0"),
        ),
    )


def _matrix_version(job: dict[str, object]) -> str | None:
    name = job.get("name")
    match = _JOB_NAME.fullmatch(name) if isinstance(name, str) else None
    return match.group(1) if match is not None else None


def _completed_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(UTC)


def _response_sha256(
    *,
    run_id: str,
    workflow_id: str,
    repository_id: str,
    source_commit: str,
    attempt: int,
    job_id: str,
    job_name: str,
    job_conclusion: object,
    step_number: str,
    step_name: str,
    step_status: object,
    step_conclusion: object,
    completed_at: str,
    python_version: str,
) -> str:
    response = {
        "actions_job_id": job_id,
        "actions_run_id": run_id,
        "actions_step_number": step_number,
        "completed_at": completed_at,
        "head_sha": source_commit,
        "job_conclusion": job_conclusion,
        "job_name": job_name,
        "python_version": python_version,
        "repository_id": repository_id,
        "step_conclusion": step_conclusion,
        "step_name": step_name,
        "step_status": step_status,
        "workflow_id": workflow_id,
        "workflow_run_attempt": attempt,
    }
    encoded = json.dumps(
        response,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _receipt(
    *,
    source: SourceIdentity,
    run: dict[str, object],
    attempt: int,
    job: dict[str, object],
    step: dict[str, object],
    check_id: ReleaseCheckId,
    python_version: str,
) -> ReleaseCheckReceipt | None:
    identity = _actions_run_identity(run)
    job_id = _positive_identifier(job.get("id"))
    step_number = _positive_identifier(step.get("number"))
    job_name = job.get("name")
    step_name = step.get("name")
    completed_at_value = step.get("completed_at")
    conclusion_value = step.get("conclusion")
    step_status = step.get("status")
    if (
        identity is None
        or job_id is None
        or step_number is None
        or not isinstance(job_name, str)
        or not isinstance(step_name, str)
        or not isinstance(completed_at_value, str)
        or not isinstance(conclusion_value, str)
        or step_status != "completed"
    ):
        return None
    completed_at = _completed_at(completed_at_value)
    if completed_at is None:
        return None
    try:
        conclusion = ReleaseCheckConclusion(conclusion_value)
    except ValueError:
        return None
    digest = _response_sha256(
        run_id=identity.run_id,
        workflow_id=identity.workflow_id,
        repository_id=identity.repository_id,
        source_commit=source.git_commit,
        attempt=attempt,
        job_id=job_id,
        job_name=job_name,
        job_conclusion=job.get("conclusion"),
        step_number=step_number,
        step_name=step_name,
        step_status=step_status,
        step_conclusion=conclusion_value,
        completed_at=completed_at_value,
        python_version=python_version,
    )
    receipt_id = f"gha:{identity.run_id}:{job_id}:{attempt}:{step_number}"
    return ReleaseCheckReceipt(
        id=receipt_id,
        check_id=check_id,
        source_commit_sha256=source.commit_sha256,
        source_tree_sha256=source.tree_sha256,
        repository_id=identity.repository_id,
        workflow_id=identity.workflow_id,
        actions_run_id=identity.run_id,
        actions_job_id=job_id,
        actions_step_id=f"{attempt}:{step_number}",
        python_version=python_version,
        completed_at=completed_at,
        conclusion=conclusion,
        response_sha256=digest,
    )


def collect_release_evidence(
    source_commit: str,
    reader: ActionsApiReader,
) -> ReleaseCollection:
    """Read exact-head CI steps from an injected read-only GitHub API reader."""
    source = _source_identity(source_commit)
    runs_endpoint = f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW_FILE}/runs"
    try:
        run_pages = _pages(
            reader.get_json(runs_endpoint, {"head_sha": source.git_commit, "per_page": "100"})
        )
        run = _selected_run(run_pages, source.git_commit)
        if run is None:
            return _pending_collection(source, "no exact-head workflow run")
        identity = _actions_run_identity(run)
        if run.get("status") != "completed":
            return _pending_collection(source, "exact-head workflow run is incomplete")
        attempt_value = _positive_identifier(run.get("run_attempt"))
        attempt = int(attempt_value or "0")
        if identity is None or attempt <= 0:
            return _pending_collection(source, "exact-head workflow identity is incomplete")

        jobs_endpoint = f"repos/{REPOSITORY}/actions/runs/{identity.run_id}/jobs"
        job_pages = _pages(reader.get_json(jobs_endpoint, {"per_page": "100"}))
        jobs = _records(job_pages, "jobs")
    except CollectionUnavailable:
        return _pending_collection(source, "GitHub Actions evidence is unavailable")

    jobs_by_version: dict[str, list[dict[str, object]]] = {
        version: [] for version in PYTHON_VERSIONS
    }
    for job in jobs:
        version = _matrix_version(job)
        if version is not None:
            jobs_by_version[version].append(job)

    receipts: list[ReleaseCheckReceipt] = []
    missing: list[tuple[ReleaseCheckId, str]] = []
    for check_id, expected_name in CI_STEP_NAMES.items():
        for python_version in PYTHON_VERSIONS:
            matching_jobs = jobs_by_version[python_version]
            if len(matching_jobs) != 1 or matching_jobs[0].get("status") != "completed":
                missing.append((check_id, python_version))
                continue
            job = matching_jobs[0]
            raw_steps = job.get("steps")
            if not isinstance(raw_steps, list) or any(
                not isinstance(item, dict) for item in raw_steps
            ):
                missing.append((check_id, python_version))
                continue
            named_steps = tuple(
                cast(dict[str, object], item)
                for item in raw_steps
                if item.get("name") == expected_name
            )
            if len(named_steps) != 1:
                missing.append((check_id, python_version))
                continue
            receipt = _receipt(
                source=source,
                run=run,
                attempt=attempt,
                job=job,
                step=named_steps[0],
                check_id=check_id,
                python_version=python_version,
            )
            if receipt is None:
                missing.append((check_id, python_version))
            else:
                receipts.append(receipt)
    return ReleaseCollection(
        source=source,
        actions_run_id=identity.run_id,
        run_attempt=attempt,
        receipts=tuple(receipts),
        missing=tuple(missing),
    )


def _receipt_payload(receipts: tuple[ReleaseCheckReceipt, ...]) -> bytes:
    records = [item.model_dump(mode="json") for item in receipts]
    return json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _private_directory(path: Path, *, create: bool = False) -> bool:
    if path.is_symlink():
        return False
    if not path.exists():
        if not create:
            return False
        try:
            path.mkdir(mode=0o700)
        except OSError:
            return False
    return path.is_dir() and not path.is_symlink() and not stat.S_IMODE(path.stat().st_mode) & 0o077


def _evaluation_root() -> Path:
    settings = Settings.from_sources(secret_provider=lambda _name: None)
    data_root = settings.data_dir.expanduser().absolute()
    if not _private_directory(data_root):
        raise CollectionUnavailable("configured data root is not private")
    diagnostics_root = data_root / "diagnostics"
    evaluation_root = diagnostics_root / "evaluation"
    if not _private_directory(diagnostics_root, create=True):
        raise CollectionUnavailable("private evaluation root is unavailable")
    if not _private_directory(evaluation_root, create=True):
        raise CollectionUnavailable("private evaluation root is unavailable")
    return evaluation_root


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _release_collection_lock(root: Path) -> Iterator[None]:
    lock_path = root / ".release-evidence.lock"
    descriptor = -1
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        lock_stat = os.fstat(descriptor)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
            raise CollectionUnavailable("release evidence lock is unsafe")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "r+b") as lock_file:
            descriptor = -1
            flock(lock_file.fileno(), LOCK_EX)
            try:
                yield
            finally:
                flock(lock_file.fileno(), LOCK_UN)
    except OSError as exc:
        raise CollectionUnavailable("release evidence collection is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _existing_receipt_matches(destination: Path, payload: bytes) -> bool:
    return (
        not destination.is_symlink()
        and destination.is_file()
        and not stat.S_IMODE(destination.stat().st_mode) & 0o077
        and destination.read_bytes() == payload
    )


def _store_receipts(collection: ReleaseCollection) -> Path:
    if collection.actions_run_id is None or collection.run_attempt is None:
        raise CollectionUnavailable("no completed exact-head workflow run")
    root = _evaluation_root()
    name = (
        f"release-checks-{collection.source.commit_sha256}-"
        f"{collection.source.tree_sha256}-{collection.actions_run_id}-"
        f"{collection.run_attempt}.json"
    )
    destination = root / name
    payload = _receipt_payload(collection.receipts)
    if destination.is_symlink():
        raise CollectionUnavailable("release receipt destination is unsafe")
    if destination.exists():
        if not _existing_receipt_matches(destination, payload):
            raise CollectionUnavailable("release receipt already exists with different content")
        return destination

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".release-checks-", suffix=".tmp", dir=root
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if not _existing_receipt_matches(destination, payload):
                raise CollectionUnavailable("release receipt already exists with different content")
        _fsync_directory(root)
    except OSError as exc:
        raise CollectionUnavailable("could not store private release receipts") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _store_collection_watermark(
    collection: ReleaseCollection,
    *,
    status: Literal["PASS", "FAIL", "PENDING"],
) -> Path:
    first_receipt = collection.receipts[0] if collection.receipts else None
    if status != "PENDING" and (
        first_receipt is None or collection.actions_run_id is None or collection.run_attempt is None
    ):
        raise CollectionUnavailable("release result lacks exact-head receipt identity")
    watermark = _ReleaseCollectionWatermark(
        source_commit_sha256=collection.source.commit_sha256,
        source_tree_sha256=collection.source.tree_sha256,
        repository_id=first_receipt.repository_id if first_receipt is not None else None,
        workflow_id=first_receipt.workflow_id if first_receipt is not None else None,
        actions_run_id=collection.actions_run_id,
        run_attempt=collection.run_attempt,
        status=status,
    )
    payload = canonical_bytes(watermark)
    root = _evaluation_root()
    destination = _release_collection_watermark_path(
        root,
        collection.source.commit_sha256,
        collection.source.tree_sha256,
    )
    if destination.is_symlink():
        raise CollectionUnavailable("release status destination is unsafe")
    try:
        destination.unlink(missing_ok=True)
        _fsync_directory(root)
    except OSError as exc:
        raise CollectionUnavailable("could not invalidate previous private release status") from exc

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".release-status-", suffix=".tmp", dir=root
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(root)
    except OSError as exc:
        raise CollectionUnavailable("could not store private release status") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-commit",
        default="HEAD",
        help="exact Git commit to collect (defaults to the checked-out HEAD)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _source_identity(arguments.source_commit)
    except CollectionUnavailable:
        print("Release evidence: PENDING (source identity is unavailable)")
        return 0

    try:
        root = _evaluation_root()
        with _release_collection_lock(root):
            try:
                collection = collect_release_evidence(arguments.source_commit, _GhApiReader())
            except CollectionUnavailable:
                print("Release evidence: PENDING (source identity is unavailable)")
                return 0
            _store_collection_watermark(collection, status="PENDING")
            if collection.receipts:
                _store_receipts(collection)
            if collection.status != "PENDING":
                _store_collection_watermark(collection, status=collection.status)
    except (CollectionUnavailable, OSError, ValueError):
        print("Release evidence: PENDING (private receipt storage is unavailable)")
        return 0

    if collection.status == "FAIL":
        failed = sum(
            receipt.conclusion is not ReleaseCheckConclusion.SUCCESS
            for receipt in collection.receipts
        )
        print(f"Release evidence: FAIL ({failed} unsuccessful exact-head steps)")
        return 1
    if collection.status == "PENDING":
        print(f"Release evidence: PENDING ({len(collection.missing)} matrix checks missing)")
        return 0
    print(f"Release evidence: PASS ({len(collection.receipts)} exact-head step receipts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
