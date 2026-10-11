from __future__ import annotations

import json
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from finance_research_agent.adapters.shadow_evidence import (
    FileSystemOperatorReleaseAttestationRepository,
    FileSystemReleaseEvidenceReader,
    FileSystemShadowObservationRepository,
)
from finance_research_agent.domain.enums import Capability
from finance_research_agent.domain.models import Sha256
from finance_research_agent.domain.regime import Regime
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.evaluation.shadow_records import (
    OperatorReleaseAttestation,
    OperatorReleaseCheckId,
    OperatorReleaseResult,
    ReleaseCheckConclusion,
    ReleaseCheckId,
    ReleaseCheckReceipt,
    ShadowDayAvailability,
    ShadowDayObservation,
    ShadowExecutiveEventState,
    ShadowExecutiveIdentification,
)

_SOURCE_COMMIT_SHA256 = "a" * 64
_SOURCE_TREE_SHA256 = "b" * 64
_REPOSITORY_ID = "789"
_WORKFLOW_ID = "101"


class _Runs:
    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known or set()

    def load(self, run_id: str):
        return object() if run_id in self.known else None


class _Feedback:
    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known or set()

    def list_feedback(self):
        return tuple(SimpleNamespace(feedback_id=item) for item in sorted(self.known))


def _observation(observation_id: str = "shadow-day-1") -> ShadowDayObservation:
    return ShadowDayObservation(
        id=observation_id,
        market_date=date(2026, 10, 9),
        recorded_at=datetime(2026, 10, 10, 0, 0, tzinfo=UTC),
        session_id="session-2026-10-09",
        availability=ShadowDayAvailability.ONLINE,
        observed_run_ids=("premarket-2026-10-09-r1",),
        observed_feedback_ids=("feedback-1",),
        executive_identification=ShadowExecutiveIdentification(
            market_posture=Regime.NEUTRAL,
            event_state=ShadowExecutiveEventState.NO_MATERIAL_EVENTS,
            event_ids=(),
            priority_symbols=("AAPL",),
            disabled_capabilities=(Capability.EVENT_RISK_CHECK_AVAILABLE,),
        ),
    )


def _attestation() -> OperatorReleaseAttestation:
    return OperatorReleaseAttestation(
        id="attestation-desktop-1",
        check_id=OperatorReleaseCheckId.DESKTOP_INSTALL,
        result=OperatorReleaseResult.PASS,
        operator_id="operator-1",
        recorded_at=datetime(2026, 10, 10, 1, 0, tzinfo=UTC),
        package_version="0.5.0.dev0",
        skill_version="sha256:" + "b" * 64,
        source_commit_sha256="c" * 64,
        source_tree_sha256="d" * 64,
        evidence_sha256="e" * 64,
    )


def _release_receipt() -> ReleaseCheckReceipt:
    return ReleaseCheckReceipt(
        id="gha:123:456:1:2",
        check_id=ReleaseCheckId.LINT,
        source_commit_sha256=_SOURCE_COMMIT_SHA256,
        source_tree_sha256=_SOURCE_TREE_SHA256,
        repository_id=_REPOSITORY_ID,
        workflow_id=_WORKFLOW_ID,
        actions_run_id="123",
        actions_job_id="456",
        actions_step_id="1:2",
        python_version="3.12",
        completed_at=datetime(2026, 10, 10, 2, 0, tzinfo=UTC),
        conclusion=ReleaseCheckConclusion.SUCCESS,
        response_sha256="c" * 64,
    )


def _release_reader(
    root: Path,
    *,
    source_commit_sha256: Sha256 = _SOURCE_COMMIT_SHA256,
    source_tree_sha256: Sha256 = _SOURCE_TREE_SHA256,
) -> FileSystemReleaseEvidenceReader:
    return FileSystemReleaseEvidenceReader(
        root,
        source_commit_sha256=source_commit_sha256,
        source_tree_sha256=source_tree_sha256,
        repository_id=_REPOSITORY_ID,
        workflow_id=_WORKFLOW_ID,
    )


def _write_release_watermark(
    root: Path,
    *,
    source_commit_sha256: str = _SOURCE_COMMIT_SHA256,
    source_tree_sha256: str = _SOURCE_TREE_SHA256,
    repository_id: str | None = _REPOSITORY_ID,
    workflow_id: str | None = _WORKFLOW_ID,
    actions_run_id: str | None = "123",
    run_attempt: int | None = 1,
    status: str = "PASS",
) -> Path:
    lock_path = root / ".release-evidence.lock"
    lock_path.touch(mode=0o600, exist_ok=True)
    lock_path.chmod(0o600)
    path = root / f"release-status-{source_commit_sha256}-{source_tree_sha256}.json"
    payload = json.dumps(
        {
            "schema_version": "0.1",
            "source_commit_sha256": source_commit_sha256,
            "source_tree_sha256": source_tree_sha256,
            "repository_id": repository_id,
            "workflow_id": workflow_id,
            "actions_run_id": actions_run_id,
            "run_attempt": run_attempt,
            "status": status,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _write_release_receipt_batch(root: Path, receipts: tuple[ReleaseCheckReceipt, ...]) -> Path:
    first = receipts[0]
    attempt = first.actions_step_id.split(":", maxsplit=1)[0]
    path = root / (
        f"release-checks-{first.source_commit_sha256}-{first.source_tree_sha256}-"
        f"{first.actions_run_id}-{attempt}.json"
    )
    payload = json.dumps(
        [receipt.model_dump(mode="json") for receipt in receipts],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    path.write_bytes(payload)
    path.chmod(0o600)
    _write_release_watermark(
        root,
        source_commit_sha256=first.source_commit_sha256,
        source_tree_sha256=first.source_tree_sha256,
        actions_run_id=first.actions_run_id,
        run_attempt=int(attempt),
    )
    return path


def test_observation_repository_writes_canonical_bytes_and_resolves_trusted_refs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shadow-days"
    repository = FileSystemShadowObservationRepository(
        root,
        run_repository=_Runs({"premarket-2026-10-09-r1"}),
        feedback_repository=_Feedback({"feedback-1"}),
    )
    record = _observation()

    repository.append_observation(record)

    path = root / "shadow-day-1.json"
    assert path.read_bytes() == canonical_bytes(record)
    assert repository.get_observation(record.id) == record
    assert repository.get_observation("shadow-day-missing") is None
    assert repository.list_observations() == (record,)
    assert repository.has_run_id("premarket-2026-10-09-r1") is True
    assert repository.has_run_id("premarket-2026-10-09-r2") is False
    assert repository.has_feedback_id("feedback-1") is True
    assert repository.has_feedback_id("feedback-2") is False


def test_observation_repository_never_overwrites_an_existing_id(tmp_path: Path) -> None:
    repository = FileSystemShadowObservationRepository(
        tmp_path / "shadow-days", run_repository=_Runs(), feedback_repository=_Feedback()
    )
    first = _observation()
    repository.append_observation(first)

    with pytest.raises(FileExistsError):
        repository.append_observation(_observation().model_copy(update={"session_id": "changed"}))

    assert repository.get_observation(first.id) == first


def test_observation_repository_rejects_noncanonical_and_malformed_files(tmp_path: Path) -> None:
    root = tmp_path / "shadow-days"
    repository = FileSystemShadowObservationRepository(
        root, run_repository=_Runs(), feedback_repository=_Feedback()
    )
    record = _observation()
    root.mkdir()
    (root / "wrong-name.json").write_bytes(canonical_bytes(record))
    with pytest.raises(ValueError, match="filename does not match"):
        repository.list_observations()
    (root / "wrong-name.json").unlink()

    (root / f"{record.id}.json").write_bytes(b" " + canonical_bytes(record))

    with pytest.raises(ValueError, match="not in canonical form"):
        repository.list_observations()

    (root / f"{record.id}.json").write_text("{not json}", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        repository.get_observation(record.id)


def test_observation_repository_rejects_symlink_root_and_record(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    linked_root = FileSystemShadowObservationRepository(
        root_link, run_repository=_Runs(), feedback_repository=_Feedback()
    )
    with pytest.raises(ValueError, match="root must be a local directory"):
        linked_root.list_observations()

    broken_root_link = tmp_path / "broken-root-link"
    broken_root_link.symlink_to(tmp_path / "absent-directory", target_is_directory=True)
    broken_linked_root = FileSystemShadowObservationRepository(
        broken_root_link,
        run_repository=_Runs(),
        feedback_repository=_Feedback(),
    )
    with pytest.raises(ValueError, match="root must be a local directory"):
        broken_linked_root.list_observations()

    root_file = tmp_path / "root-file"
    root_file.write_text("not a directory", encoding="utf-8")
    file_root_repository = FileSystemShadowObservationRepository(
        root_file,
        run_repository=_Runs(),
        feedback_repository=_Feedback(),
    )
    with pytest.raises(ValueError, match="root must be a local directory"):
        file_root_repository.list_observations()

    root = tmp_path / "shadow-days"
    root.mkdir()
    (root / "shadow-day-1.json").symlink_to(tmp_path / "absent.json")
    repository = FileSystemShadowObservationRepository(
        root, run_repository=_Runs(), feedback_repository=_Feedback()
    )
    with pytest.raises(ValueError, match="regular local file"):
        repository.get_observation("shadow-day-1")


def test_observation_lookup_rejects_identifiers_that_escape_its_root(tmp_path: Path) -> None:
    root = tmp_path / "shadow-days"
    root.mkdir()
    record = _observation()
    (tmp_path / f"{record.id}.json").write_bytes(canonical_bytes(record))
    repository = FileSystemShadowObservationRepository(
        root, run_repository=_Runs(), feedback_repository=_Feedback()
    )

    with pytest.raises(ValueError, match="identifier is invalid"):
        repository.get_observation(f"../{record.id}")


def test_operator_attestations_are_immutable_and_filename_bound(tmp_path: Path) -> None:
    repository = FileSystemOperatorReleaseAttestationRepository(tmp_path / "attestations")
    attestation = _attestation()
    repository.append_attestation(attestation)

    path = tmp_path / "attestations" / f"{attestation.id}.json"
    assert path.read_bytes() == canonical_bytes(attestation)
    assert repository.list_attestations() == (attestation,)
    assert sha256(path.read_bytes()).hexdigest() == sha256(canonical_bytes(attestation)).hexdigest()
    with pytest.raises(FileExistsError):
        repository.append_attestation(
            attestation.model_copy(update={"operator_id": "different-operator"})
        )


def test_release_evidence_reader_loads_canonical_private_receipt_batch(tmp_path: Path) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    receipt = _release_receipt()
    _write_release_receipt_batch(root, (receipt,))
    reader = _release_reader(root)

    assert reader.list_receipts() == (receipt,)


def test_release_evidence_reader_selects_latest_attempt_for_requested_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    earlier_run_receipt = _release_receipt()
    latest_run_receipt = earlier_run_receipt.model_copy(
        update={
            "id": "gha:125:457:1:3",
            "actions_run_id": "125",
            "actions_job_id": "457",
            "actions_step_id": "1:3",
            "response_sha256": "d" * 64,
        }
    )
    latest_attempt_receipt = latest_run_receipt.model_copy(
        update={
            "id": "gha:125:457:2:3",
            "actions_step_id": "2:3",
            "response_sha256": "e" * 64,
        }
    )
    newer_other_source = earlier_run_receipt.model_copy(
        update={
            "id": "gha:999:458:1:4",
            "source_commit_sha256": "0" * 64,
            "actions_run_id": "999",
            "actions_job_id": "458",
            "actions_step_id": "1:4",
            "response_sha256": "f" * 64,
        }
    )
    _write_release_receipt_batch(root, (latest_run_receipt,))
    _write_release_receipt_batch(root, (earlier_run_receipt,))
    _write_release_receipt_batch(root, (latest_attempt_receipt,))
    _write_release_receipt_batch(root, (newer_other_source,))
    (root / "other-diagnostic.txt").write_text("leave outside reader contract", encoding="utf-8")

    assert _release_reader(root).list_receipts() == (latest_attempt_receipt,)


def test_release_evidence_reader_returns_empty_when_requested_source_is_absent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    other_source = _release_receipt().model_copy(
        update={
            "source_commit_sha256": "f" * 64,
            "response_sha256": "d" * 64,
        }
    )
    _write_release_receipt_batch(root, (other_source,))

    assert _release_reader(root).list_receipts() == ()


def test_release_evidence_reader_ignores_retained_receipts_without_a_current_watermark(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    receipt = _release_receipt()
    _write_release_receipt_batch(root, (receipt,))
    (root / f"release-status-{_SOURCE_COMMIT_SHA256}-{_SOURCE_TREE_SHA256}.json").unlink()

    assert _release_reader(root).list_receipts() == ()


def test_release_evidence_reader_ignores_watermarks_without_collection_lock(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    receipt = _release_receipt()
    _write_release_receipt_batch(root, (receipt,))
    lock_path = root / ".release-evidence.lock"
    lock_path.touch(mode=0o600)
    lock_path.unlink()

    assert _release_reader(root).list_receipts() == ()


def test_release_evidence_reader_uses_pending_watermark_over_retained_receipts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    _write_release_receipt_batch(root, (_release_receipt(),))
    _write_release_watermark(
        root,
        repository_id=None,
        workflow_id=None,
        actions_run_id=None,
        run_attempt=None,
        status="PENDING",
    )

    assert _release_reader(root).list_receipts() == ()


def test_release_evidence_reader_rejects_a_watermark_for_another_source_or_workflow(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    watermark_path = _write_release_watermark(root, workflow_id="102")

    with pytest.raises(ValueError, match="configured repository and workflow"):
        _release_reader(root).list_receipts()

    watermark = json.loads(watermark_path.read_bytes())
    watermark["workflow_id"] = _WORKFLOW_ID
    watermark["source_commit_sha256"] = "f" * 64
    watermark_path.write_bytes(
        json.dumps(
            watermark,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    )
    with pytest.raises(ValueError, match="does not match its source"):
        _release_reader(root).list_receipts()


def test_release_evidence_reader_requires_a_complete_pass_watermark(tmp_path: Path) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    _write_release_watermark(
        root,
        repository_id=None,
        workflow_id=None,
        actions_run_id=None,
        run_attempt=None,
    )

    with pytest.raises(ValueError, match="watermark is malformed"):
        _release_reader(root).list_receipts()


def test_release_evidence_reader_rejects_pass_watermark_without_run_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    _write_release_watermark(root, actions_run_id=None, run_attempt=None)

    with pytest.raises(ValueError, match="watermark is malformed"):
        _release_reader(root).list_receipts()


def test_release_evidence_reader_returns_empty_when_watermark_batch_is_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    _write_release_watermark(root, actions_run_id="999")

    assert _release_reader(root).list_receipts() == ()


def test_release_evidence_reader_rejects_permissive_symlink_and_malformed_watermarks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    watermark = _write_release_watermark(root)

    watermark.chmod(0o644)
    with pytest.raises(ValueError, match="watermark must be private"):
        _release_reader(root).list_receipts()
    watermark.chmod(0o600)

    real_status = tmp_path / "real-status.json"
    real_status.write_bytes(watermark.read_bytes())
    watermark.unlink()
    watermark.symlink_to(real_status)
    with pytest.raises(ValueError, match="watermark must be a regular local file"):
        _release_reader(root).list_receipts()
    watermark.unlink()

    watermark.write_bytes(b"{malformed}")
    watermark.chmod(0o600)
    with pytest.raises(ValueError, match="watermark is malformed"):
        _release_reader(root).list_receipts()

    _write_release_watermark(root)
    watermark.write_bytes(b" " + watermark.read_bytes())
    with pytest.raises(ValueError, match="watermark is not in canonical form"):
        _release_reader(root).list_receipts()

    _write_release_watermark(root)
    read_bytes = Path.read_bytes

    def fail_watermark_read(source: Path) -> bytes:
        if source == watermark:
            raise PermissionError("private status")
        return read_bytes(source)

    monkeypatch.setattr(Path, "read_bytes", fail_watermark_read)
    with pytest.raises(ValueError, match="watermark is malformed"):
        _release_reader(root).list_receipts()


def test_release_evidence_reader_rejects_invalid_source_hash_configuration(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"

    with pytest.raises(ValueError, match="source commit hash is invalid"):
        FileSystemReleaseEvidenceReader(
            root,
            source_commit_sha256="not-a-sha256",
            source_tree_sha256=_SOURCE_TREE_SHA256,
            repository_id=_REPOSITORY_ID,
            workflow_id=_WORKFLOW_ID,
        )

    with pytest.raises(ValueError, match="source tree hash is invalid"):
        FileSystemReleaseEvidenceReader(
            root,
            source_commit_sha256=_SOURCE_COMMIT_SHA256,
            source_tree_sha256="not-a-sha256",
            repository_id=_REPOSITORY_ID,
            workflow_id=_WORKFLOW_ID,
        )


@pytest.mark.parametrize(
    ("identity_field", "identity_value"),
    (("repository_id", "790"), ("workflow_id", "102")),
)
def test_release_evidence_reader_rejects_receipts_from_another_repository_or_workflow(
    tmp_path: Path,
    identity_field: str,
    identity_value: str,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    receipt = _release_receipt().model_copy(update={identity_field: identity_value})
    _write_release_receipt_batch(root, (receipt,))

    reader = FileSystemReleaseEvidenceReader(
        root,
        source_commit_sha256=_SOURCE_COMMIT_SHA256,
        source_tree_sha256=_SOURCE_TREE_SHA256,
        repository_id=_REPOSITORY_ID,
        workflow_id=_WORKFLOW_ID,
    )

    with pytest.raises(ValueError, match="does not match the configured repository and workflow"):
        reader.list_receipts()


@pytest.mark.parametrize(
    ("repository_id", "workflow_id", "error"),
    (
        ("invalid id", _WORKFLOW_ID, "repository id is invalid"),
        (_REPOSITORY_ID, "invalid id", "workflow id is invalid"),
    ),
)
def test_release_evidence_reader_rejects_invalid_repository_workflow_configuration(
    tmp_path: Path,
    repository_id: str,
    workflow_id: str,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        FileSystemReleaseEvidenceReader(
            tmp_path / "release-evidence",
            source_commit_sha256=_SOURCE_COMMIT_SHA256,
            source_tree_sha256=_SOURCE_TREE_SHA256,
            repository_id=repository_id,
            workflow_id=workflow_id,
        )


def test_release_evidence_reader_returns_empty_for_a_missing_root(tmp_path: Path) -> None:
    reader = _release_reader(tmp_path / "missing-release-evidence")

    assert reader.list_receipts() == ()


def test_release_evidence_reader_returns_empty_for_a_private_empty_root(tmp_path: Path) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)

    assert _release_reader(root).list_receipts() == ()


def test_release_evidence_reader_rejects_an_empty_collector_batch(tmp_path: Path) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    receipt = _release_receipt()
    path = root / (
        f"release-checks-{receipt.source_commit_sha256}-{receipt.source_tree_sha256}-"
        f"{receipt.actions_run_id}-1.json"
    )
    path.write_bytes(b"[]")
    path.chmod(0o600)
    _write_release_watermark(
        root,
        source_commit_sha256=receipt.source_commit_sha256,
        source_tree_sha256=receipt.source_tree_sha256,
        actions_run_id=receipt.actions_run_id,
        run_attempt=1,
    )

    with pytest.raises(ValueError, match="must contain at least one receipt"):
        _release_reader(root).list_receipts()


def test_release_evidence_reader_rejects_malformed_and_noncanonical_batches(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    path = _write_release_receipt_batch(root, (_release_receipt(),))

    path.write_bytes(b" " + path.read_bytes())
    with pytest.raises(ValueError, match="not in canonical form"):
        _release_reader(root).list_receipts()

    path.write_bytes(b"{not json}")
    with pytest.raises(ValueError, match="malformed"):
        _release_reader(root).list_receipts()


def test_release_evidence_reader_binds_receipt_identity_to_batch_filename(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    receipt = _release_receipt()
    path = root / (
        f"release-checks-{'f' * 64}-{receipt.source_tree_sha256}-{receipt.actions_run_id}-1.json"
    )
    batch = json.dumps(
        [receipt.model_dump(mode="json")],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    path.write_bytes(batch)
    path.chmod(0o600)
    _write_release_watermark(
        root,
        source_commit_sha256="f" * 64,
        source_tree_sha256=receipt.source_tree_sha256,
        actions_run_id=receipt.actions_run_id,
        run_attempt=1,
    )

    with pytest.raises(ValueError, match="does not match its batch identity"):
        _release_reader(
            root,
            source_commit_sha256="f" * 64,
            source_tree_sha256=receipt.source_tree_sha256,
        ).list_receipts()


def test_release_evidence_reader_rejects_permissive_roots_and_batch_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o750)
    with pytest.raises(ValueError, match="root must be a private local directory"):
        _release_reader(root).list_receipts()

    root.chmod(0o700)
    path = _write_release_receipt_batch(root, (_release_receipt(),))
    path.chmod(0o640)
    with pytest.raises(ValueError, match="batch must be private"):
        _release_reader(root).list_receipts()

    file_root = tmp_path / "release-evidence-file"
    file_root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be a private local directory"):
        _release_reader(file_root).list_receipts()


def test_release_evidence_reader_rejects_symlink_roots_and_batches(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    target.chmod(0o700)
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="root must be a private local directory"):
        _release_reader(root_link).list_receipts()

    receipt = _release_receipt()
    real_batch = _write_release_receipt_batch(target, (receipt,))
    root_batch = tmp_path / "release-evidence"
    root_batch.mkdir(mode=0o700)
    root_batch.chmod(0o700)
    batch_link = root_batch / real_batch.name
    batch_link.symlink_to(real_batch)
    _write_release_watermark(
        root_batch,
        source_commit_sha256=receipt.source_commit_sha256,
        source_tree_sha256=receipt.source_tree_sha256,
        actions_run_id=receipt.actions_run_id,
        run_attempt=1,
    )
    with pytest.raises(ValueError, match="regular local file"):
        _release_reader(root_batch).list_receipts()


def test_release_evidence_reader_rejects_invalid_names_and_nonfile_batches(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    invalid_name = root / "release-checks-invalid.json"
    invalid_name.write_bytes(b"[]")
    invalid_name.chmod(0o600)
    assert _release_reader(root).list_receipts() == ()

    invalid_name.unlink()
    receipt = _release_receipt()
    batch_directory = root / (
        f"release-checks-{receipt.source_commit_sha256}-{receipt.source_tree_sha256}-"
        f"{receipt.actions_run_id}-1.json"
    )
    batch_directory.mkdir(mode=0o700)
    _write_release_watermark(
        root,
        source_commit_sha256=receipt.source_commit_sha256,
        source_tree_sha256=receipt.source_tree_sha256,
        actions_run_id=receipt.actions_run_id,
        run_attempt=1,
    )
    with pytest.raises(ValueError, match="regular local file"):
        _release_reader(root).list_receipts()


def test_release_evidence_reader_rejects_a_receipt_with_malformed_step_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    receipt = _release_receipt().model_copy(
        update={"id": "gha:123:456:1:2:3", "actions_step_id": "1:2:3"}
    )
    _write_release_receipt_batch(root, (receipt,))

    with pytest.raises(ValueError, match="does not match its batch identity"):
        _release_reader(root).list_receipts()


def test_release_evidence_reader_reports_unreadable_batch_as_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "release-evidence"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    path = _write_release_receipt_batch(root, (_release_receipt(),))
    read_bytes = Path.read_bytes

    def fail_batch_read(source: Path) -> bytes:
        if source == path:
            raise PermissionError("private batch")
        return read_bytes(source)

    monkeypatch.setattr(Path, "read_bytes", fail_batch_read)

    with pytest.raises(ValueError, match="batch is malformed"):
        _release_reader(root).list_receipts()


def test_repositories_return_empty_when_fixed_data_directories_do_not_exist(
    tmp_path: Path,
) -> None:
    observations = FileSystemShadowObservationRepository(
        tmp_path / "missing-days", run_repository=_Runs(), feedback_repository=_Feedback()
    )
    attestations = FileSystemOperatorReleaseAttestationRepository(tmp_path / "missing-attestations")

    assert observations.list_observations() == ()
    assert observations.get_observation("shadow-day-1") is None
    assert attestations.list_attestations() == ()


def test_failed_atomic_file_creation_leaves_no_record_or_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shadow-days"
    repository = FileSystemShadowObservationRepository(
        root, run_repository=_Runs(), feedback_repository=_Feedback()
    )

    def fail_before_temp_file(*args: object, **kwargs: object) -> None:
        raise PermissionError("fixture cannot create a temp file")

    with monkeypatch.context() as context:
        context.setattr(
            "finance_research_agent.adapters.shadow_evidence.tempfile.mkstemp",
            fail_before_temp_file,
        )
        with pytest.raises(OSError, match="could not append shadow observation"):
            repository.append_observation(_observation())

    assert list(root.iterdir()) == []


def test_failed_exclusive_link_cleans_up_the_staged_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shadow-days"
    repository = FileSystemShadowObservationRepository(
        root, run_repository=_Runs(), feedback_repository=_Feedback()
    )

    def fail_link(source: object, destination: object) -> None:
        raise PermissionError("fixture cannot link the record")

    with monkeypatch.context() as context:
        context.setattr("finance_research_agent.adapters.shadow_evidence.os.link", fail_link)
        with pytest.raises(OSError, match="could not append shadow observation"):
            repository.append_observation(_observation())

    assert list(root.iterdir()) == []
