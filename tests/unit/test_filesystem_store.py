import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Thread

import pytest

from finance_research_agent.adapters.filesystem import (
    FileSystemRunRepository,
    LeaseHeldError,
    PathNotAllowedError,
    PublicationError,
)
from finance_research_agent.domain.enums import (
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    InvocationType,
    RunType,
)
from finance_research_agent.domain.models import (
    ComponentVersions,
    ConfigurationSnapshot,
    PublishedRunBundle,
    RunCheckpoint,
    RunContext,
    RunContextSeed,
    RunKey,
    RunLease,
)
from finance_research_agent.domain.types import FrozenMap

NOW = datetime(2026, 8, 19, 12, 45, tzinfo=UTC)


def _context(revision: int = 1) -> RunContext:
    versions = ComponentVersions(
        core_version="0.5.0.dev0",
        mcp_contract_version="0.1",
        plugin_version="0.1",
        skill_version="0.1",
        prompt_version="0.1",
        report_template_version="0.1",
        schema_versions=FrozenMap({"run-context": "0.1"}),
        watchlist_version="1",
        regime_policy_version="1",
        setup_policy_version="1",
        risk_policy_version="1",
        source_policy_version="1",
    )
    return RunContext(
        run_id=f"premarket-2026-08-19-r{revision}",
        run_type=RunType.PREMARKET,
        market_date=date(2026, 8, 19),
        revision=revision,
        invoked_at=NOW,
        evidence_cutoff_at=NOW + timedelta(minutes=10),
        execution_status=ExecutionStatus.CREATED,
        data_quality_status=DataQualityStatus.PASS,
        delivery_status=DeliveryStatus.MANUAL,
        configuration_snapshot=ConfigurationSnapshot(
            content_hash_sha256="a" * 64,
            file_hashes=FrozenMap({"watchlist.yaml": "b" * 64}),
            watchlist_version="1",
            regime_policy_version="1",
            setup_policy_version="1",
            risk_policy_version="1",
            source_policy_version="1",
        ),
        core_version=versions.core_version,
        mcp_contract_version=versions.mcp_contract_version,
        plugin_version=versions.plugin_version,
        skill_version=versions.skill_version,
        prompt_version=versions.prompt_version,
        report_template_version=versions.report_template_version,
        schema_versions=versions.schema_versions,
    )


def _seed() -> RunContextSeed:
    configuration = ConfigurationSnapshot(
        content_hash_sha256="a" * 64,
        file_hashes=FrozenMap({"watchlist.yaml": "b" * 64}),
        watchlist_version="1",
        regime_policy_version="1",
        setup_policy_version="1",
        risk_policy_version="1",
        source_policy_version="1",
    )
    return RunContextSeed(
        market_date=date(2026, 8, 19),
        invoked_at=NOW,
        delivery_status=DeliveryStatus.MANUAL,
        configuration_snapshot=configuration,
        component_versions=ComponentVersions(
            core_version="0.5.0.dev0",
            mcp_contract_version="0.1",
            plugin_version="0.1",
            skill_version="0.1",
            prompt_version="0.1",
            report_template_version="0.1",
            schema_versions=FrozenMap({"run-context": "0.1"}),
            watchlist_version="1",
            regime_policy_version="1",
            setup_policy_version="1",
            risk_policy_version="1",
            source_policy_version="1",
        ),
    )


def test_missed_run_record_is_durable_and_idempotent_by_market_date(tmp_path: Path) -> None:
    from finance_research_agent.domain import models

    record_model = getattr(models, "MissedRunRecord", None)
    assert record_model is not None, "after-close diagnostics need a typed missed-run record"
    first_record = record_model(
        market_date=date(2026, 8, 19),
        detected_at=datetime(2026, 8, 19, 20, 0, tzinfo=UTC),
        regular_close_at=datetime(2026, 8, 19, 20, 0, tzinfo=UTC),
        reason_code="MISSED_WINDOW",
    )
    later_record = first_record.model_copy(
        update={"detected_at": datetime(2026, 8, 19, 20, 1, tzinfo=UTC)}
    )
    repository = FileSystemRunRepository(tmp_path)
    recorder = getattr(repository, "record_missed_run", None)
    reader = getattr(repository, "get_missed_run", None)
    assert callable(recorder), "repository must durably record missed-run diagnostics"
    assert callable(reader), "repository must read the immutable missed-run diagnostic"

    assert recorder(first_record) == first_record
    assert recorder(later_record) == first_record
    assert reader(date(2026, 8, 19)) == first_record
    assert FileSystemRunRepository(tmp_path, create_layout=False).get_missed_run(
        date(2026, 8, 19)
    ) == first_record
    with pytest.raises(TypeError, match="market_date must be a date"):
        repository.get_missed_run("2026-08-19")


def test_missed_run_read_and_write_reject_in_root_symlink_aliases(tmp_path: Path) -> None:
    from finance_research_agent.domain.models import MissedRunRecord

    repository = FileSystemRunRepository(tmp_path)
    requested_date = date(2026, 8, 19)
    target_date = date(2026, 8, 20)
    target = MissedRunRecord(
        market_date=target_date,
        detected_at=datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
        regular_close_at=datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
        reason_code="MISSED_WINDOW",
    )
    requested = target.model_copy(
        update={
            "market_date": requested_date,
            "detected_at": datetime(2026, 8, 19, 20, 0, tzinfo=UTC),
            "regular_close_at": datetime(2026, 8, 19, 20, 0, tzinfo=UTC),
        }
    )
    repository.record_missed_run(target)
    requested_path = (
        tmp_path / "diagnostics/missed-runs/2026/2026-08-19.json"
    )
    requested_path.parent.mkdir(parents=True, exist_ok=True)
    requested_path.symlink_to(tmp_path / "diagnostics/missed-runs/2026/2026-08-20.json")

    with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
        repository.get_missed_run(requested_date)
    with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
        repository.record_missed_run(requested)


def test_missed_run_reader_rejects_record_for_a_different_market_date(tmp_path: Path) -> None:
    from finance_research_agent.domain.models import MissedRunRecord

    repository = FileSystemRunRepository(tmp_path)
    requested_date = date(2026, 8, 19)
    other_date = date(2026, 8, 20)
    record = MissedRunRecord(
        market_date=other_date,
        detected_at=datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
        regular_close_at=datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
        reason_code="MISSED_WINDOW",
    )
    record_path = tmp_path / "diagnostics/missed-runs/2026/2026-08-19.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_text(record.model_dump_json(), encoding="utf-8")

    with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
        repository.get_missed_run(requested_date)


def test_missed_run_record_rejects_detection_before_regular_close() -> None:
    from finance_research_agent.domain import models

    record_model = getattr(models, "MissedRunRecord", None)
    assert record_model is not None
    with pytest.raises(ValueError, match="at or after regular close"):
        record_model(
            market_date=date(2026, 8, 19),
            detected_at=datetime(2026, 8, 19, 19, 59, tzinfo=UTC),
            regular_close_at=datetime(2026, 8, 19, 20, 0, tzinfo=UTC),
            reason_code="MISSED_WINDOW",
        )


def test_missed_run_record_requires_regular_close_on_market_date() -> None:
    from finance_research_agent.domain import models

    record_model = getattr(models, "MissedRunRecord", None)
    assert record_model is not None
    with pytest.raises(ValueError, match="regular_close_at must fall on the market date"):
        record_model(
            market_date=date(2026, 8, 19),
            detected_at=datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
            regular_close_at=datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
            reason_code="MISSED_WINDOW",
        )


def _bundle(context: RunContext, report: str = "# Synthetic report\n") -> PublishedRunBundle:
    return PublishedRunBundle(
        run=context,
        bundle=FrozenMap({"kind": "minimal-frozen-run", "run_id": context.run_id}),
        report_markdown=report,
        markdown_sha256=hashlib.sha256(report.encode()).hexdigest(),
    )


@pytest.mark.parametrize(
    ("corruption", "message"),
    (
        ("bundle_hash", "conflicting receipt"),
        ("published_at", "receipt time is invalid"),
    ),
)
def test_publication_refuses_to_overwrite_conflicting_index_receipt(
    tmp_path: Path, corruption: str, message: str
) -> None:
    from finance_research_agent.domain.types import canonical_bytes

    context = _context()
    bundle = _bundle(context)
    bundle_hash = hashlib.sha256(canonical_bytes(bundle)).hexdigest()
    report_hash = hashlib.sha256(bundle.report_markdown.encode()).hexdigest()
    entry = {
        "bundle_sha256": bundle_hash,
        "markdown_sha256": report_hash,
        "published_at": NOW.isoformat(),
    }
    if corruption == "bundle_hash":
        entry["bundle_sha256"] = "0" * 64
    else:
        entry["published_at"] = "invalid-timestamp"
    expected_index = {context.run_id: dict(entry)}

    repository = FileSystemRunRepository(tmp_path)
    repository.create(context)
    index_path = (
        tmp_path
        / "reports"
        / str(context.market_date.year)
        / context.market_date.isoformat()
        / "index.json"
    )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(expected_index), encoding="utf-8")

    with pytest.raises(PublicationError, match="publication index update failed") as raised:
        repository.publish_atomically(bundle)
    assert raised.value.__cause__ is not None
    assert message in str(raised.value.__cause__)

    assert json.loads(index_path.read_bytes()) == expected_index
    assert repository.get_published_artifact(context.run_id) is None
    assert repository.diagnostic_orphan_exists(context.run_id)


def test_lease_uses_expiry_and_heartbeat_not_file_existence(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    key = RunKey(run_type=RunType.PREMARKET, market_date=date(2026, 8, 19))
    lease = repository.acquire_lease(key, NOW)

    with pytest.raises(LeaseHeldError):
        repository.acquire_lease(key, NOW + timedelta(seconds=1))

    heartbeat = repository.heartbeat(lease, NOW + timedelta(seconds=2))
    assert heartbeat.token == lease.token
    assert heartbeat.heartbeat_at == NOW + timedelta(seconds=2)
    resumed = repository.acquire_lease(
        key,
        heartbeat.expires_at + timedelta(seconds=1),
    )
    assert resumed.token != lease.token
    assert len(tuple((tmp_path / "diagnostics").rglob("*.json"))) >= 1


def test_concurrent_live_lease_claim_has_exactly_one_winner(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    contender = FileSystemRunRepository(tmp_path)
    key = RunKey(run_type=RunType.PREMARKET, market_date=date(2026, 8, 19))
    both_callers_ready = Barrier(2)

    def attempt(repository_instance: FileSystemRunRepository) -> str:
        both_callers_ready.wait()
        try:
            repository_instance.acquire_lease(key, NOW)
        except LeaseHeldError:
            return "held"
        return "acquired"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(attempt, (repository, contender)))

    assert sorted(outcomes) == ["acquired", "held"]


def test_stale_heartbeat_cannot_resurrect_replaced_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    key = RunKey(run_type=RunType.PREMARKET, market_date=date(2026, 8, 19))
    original = repository.acquire_lease(key, NOW)
    lease_path = tmp_path / "runs/2026/2026-08-19/.lease.json"
    heartbeat_write_started = Event()
    release_heartbeat_write = Event()
    original_atomic_write = repository._atomic_write

    def gated_atomic_write(target: Path, payload: bytes) -> None:
        candidate = RunLease.model_validate_json(payload)
        if target == lease_path and candidate.token == original.token:
            heartbeat_write_started.set()
            assert release_heartbeat_write.wait(2)
        original_atomic_write(target, payload)

    monkeypatch.setattr(repository, "_atomic_write", gated_atomic_write)

    heartbeat_error: list[BaseException] = []

    def heartbeat() -> None:
        try:
            repository.heartbeat(original, NOW + timedelta(seconds=1))
        except BaseException as error:  # pragma: no cover - assertion below reports it
            heartbeat_error.append(error)

    heartbeat_thread = Thread(target=heartbeat)
    heartbeat_thread.start()
    assert heartbeat_write_started.wait(2)

    replacement: list[RunLease] = []
    replacement_done = Event()

    def replace() -> None:
        replacement.append(
            repository.acquire_lease(key, original.expires_at + timedelta(seconds=1))
        )
        replacement_done.set()

    replacement_thread = Thread(target=replace)
    replacement_thread.start()
    assert not replacement_done.wait(0.5)

    release_heartbeat_write.set()
    heartbeat_thread.join(2)
    replacement_thread.join(2)

    assert not heartbeat_error
    assert len(replacement) == 1
    current = RunLease.model_validate_json(lease_path.read_bytes())
    assert current.token == replacement[0].token
    assert current.token != original.token


def test_concurrent_manual_revision_allocations_are_unique(tmp_path: Path) -> None:
    repositories = tuple(FileSystemRunRepository(tmp_path) for _ in range(4))

    def allocate(repository: FileSystemRunRepository) -> str:
        return repository.allocate_revision(_seed(), InvocationType.MANUAL).run_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        run_ids = tuple(executor.map(allocate, repositories))

    assert sorted(run_ids) == [
        "premarket-2026-08-19-r1",
        "premarket-2026-08-19-r2",
        "premarket-2026-08-19-r3",
        "premarket-2026-08-19-r4",
    ]


def test_concurrent_create_cannot_overwrite_an_immutable_revision(tmp_path: Path) -> None:
    repositories = tuple(FileSystemRunRepository(tmp_path) for _ in range(2))
    first = _context()
    second = first.model_copy(update={"execution_status": ExecutionStatus.COLLECTING})

    def create(args: tuple[FileSystemRunRepository, RunContext]) -> str:
        repository, context = args
        try:
            repository.create(context)
        except PublicationError:
            return "rejected"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(create, zip(repositories, (first, second))))

    assert sorted(outcomes) == ["created", "rejected"]
    stored = repositories[0].load(first.run_id)
    assert stored is not None
    assert stored.run.execution_status in {
        first.execution_status,
        second.execution_status,
    }


def test_concurrent_checkpoints_preserve_append_only_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    checkpoints = (
        RunCheckpoint(
            run_id=context.run_id,
            stage="COLLECTING",
            execution_status=ExecutionStatus.COLLECTING,
            data_quality_status=DataQualityStatus.DEGRADED,
            delivery_status=DeliveryStatus.DELAYED,
            written_at=NOW,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"config": "c" * 64}),
            resumable=True,
        ),
        RunCheckpoint(
            run_id=context.run_id,
            stage="ANALYZING",
            execution_status=ExecutionStatus.ANALYZING,
            data_quality_status=DataQualityStatus.PASS,
            delivery_status=DeliveryStatus.MANUAL,
            written_at=NOW + timedelta(seconds=1),
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"config": "d" * 64}),
            resumable=True,
        ),
    )

    first_write_started = Event()
    second_write_started = Event()
    release_first_write = Event()
    write_count = 0
    original_atomic_write = repository._atomic_write

    def gated_atomic_write(target: Path, payload: bytes) -> None:
        nonlocal write_count
        if target.parent.name == "checkpoints":
            write_count += 1
            if write_count == 1:
                first_write_started.set()
                assert release_first_write.wait(2)
            else:
                second_write_started.set()
        original_atomic_write(target, payload)

    monkeypatch.setattr(repository, "_atomic_write", gated_atomic_write)

    def write(checkpoint: RunCheckpoint) -> None:
        repository.checkpoint(context.run_id, checkpoint)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(write, checkpoint) for checkpoint in checkpoints)
        assert first_write_started.wait(2)
        reached_second_write = second_write_started.wait(0.5)
        release_first_write.set()
        assert not reached_second_write
        tuple(future.result() for future in futures)

    stored = repository.load(context.run_id)
    assert stored is not None
    assert {checkpoint.stage for checkpoint in stored.checkpoints} == {
        "COLLECTING",
        "ANALYZING",
    }
    assert len(futures) == 2


def test_checkpoint_and_cutoff_are_persisted_and_cutoff_is_immutable(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    repository.checkpoint(
        context.run_id,
        RunCheckpoint(
            run_id=context.run_id,
            stage="COLLECTING",
            execution_status=ExecutionStatus.COLLECTING,
            data_quality_status=DataQualityStatus.DEGRADED,
            delivery_status=DeliveryStatus.DELAYED,
            written_at=NOW,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"config": "c" * 64}),
            resumable=True,
        ),
    )
    stored = repository.freeze_evidence(context.run_id, NOW + timedelta(minutes=13))

    assert stored.evidence_cutoff_at == NOW + timedelta(minutes=13)
    assert stored.checkpoints[-1].stage == "EVIDENCE_FROZEN"
    assert stored.checkpoints[-1].execution_status is ExecutionStatus.COLLECTING
    assert stored.checkpoints[-1].data_quality_status is DataQualityStatus.DEGRADED
    assert stored.checkpoints[-1].delivery_status is DeliveryStatus.DELAYED
    with pytest.raises(ValueError, match="new revision"):
        repository.freeze_evidence(context.run_id, NOW + timedelta(minutes=14))


def test_evidence_freeze_binds_actual_cutoff_to_reloaded_run_context(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context().model_copy(update={"evidence_cutoff_at": None})
    repository.create(context)

    before_freeze = repository.load(context.run_id)
    assert before_freeze is not None
    assert before_freeze.run.evidence_cutoff_at is None

    cutoff = NOW + timedelta(minutes=13)
    frozen = repository.freeze_evidence(context.run_id, cutoff)
    reloaded = FileSystemRunRepository(tmp_path).load(context.run_id)

    assert frozen.evidence_cutoff_at == cutoff
    assert frozen.run.evidence_cutoff_at == cutoff
    assert frozen.run.run_id == context.run_id
    assert frozen.run.invoked_at == context.invoked_at
    assert reloaded == frozen
    with pytest.raises(ValueError, match="immutable"):
        repository.freeze_evidence(context.run_id, cutoff + timedelta(seconds=1))


def test_evidence_freeze_rejects_a_cutoff_before_run_invocation(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context().model_copy(update={"evidence_cutoff_at": None})
    repository.create(context)

    with pytest.raises(ValueError, match="cannot precede run invocation"):
        repository.freeze_evidence(context.run_id, NOW - timedelta(seconds=1))

    stored = repository.load(context.run_id)
    assert stored is not None
    assert stored.evidence_cutoff_at is None
    assert stored.run.evidence_cutoff_at is None


def test_staged_artifact_is_hash_addressed_readable_and_immutable(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    payload = b'{"kind":"research_packet","version":"0.1"}'

    digest = repository.stage_artifact(context.run_id, "research_packet", payload)

    assert digest == hashlib.sha256(payload).hexdigest()
    assert repository.read_staged_artifact(context.run_id, "research_packet") == payload
    assert repository.stage_artifact(context.run_id, "research_packet", payload) == digest
    with pytest.raises(ValueError, match="immutable staged artifact"):
        repository.stage_artifact(context.run_id, "research_packet", b"different")
    assert repository.read_staged_artifact(context.run_id, "research_packet") == payload


def test_staged_artifact_rejects_caller_paths_and_requires_a_live_staging_run(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()

    with pytest.raises(ValueError, match="artifact name"):
        repository.stage_artifact(context.run_id, "../escape", b"payload")
    with pytest.raises(ValueError, match="nonempty bytes"):
        repository.stage_artifact(context.run_id, "research_packet", b"")
    with pytest.raises(ValueError, match="nonempty bytes"):
        repository.stage_artifact(context.run_id, "research_packet", bytearray(b"payload"))
    with pytest.raises(ValueError, match="run staging does not exist"):
        repository.stage_artifact(context.run_id, "research_packet", b"payload")
    assert repository.read_staged_artifact(context.run_id, "research_packet") is None


def test_published_run_has_no_staged_artifact_write_surface(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    repository.publish_atomically(_bundle(context))

    with pytest.raises(ValueError, match="run staging does not exist"):
        repository.stage_artifact(context.run_id, "research_packet", b"payload")


def test_staged_artifact_rejects_internal_symlink_aliases(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    staging = tmp_path / "runs/2026/2026-08-19/.staging" / context.run_id
    artifact_directory = staging / "artifacts"
    artifact_directory.mkdir()
    (artifact_directory / "alias.bin").symlink_to(staging / "run.json")

    with pytest.raises(PathNotAllowedError):
        repository.stage_artifact(context.run_id, "alias", b"payload")


def test_staged_artifact_rejects_symlink_to_published_run_directory(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    published_context = _context(revision=1)
    staging_context = _context(revision=2)
    repository.create(published_context)
    repository.publish_atomically(_bundle(published_context))
    repository.create(staging_context)
    staging, published, _ = repository._paths(staging_context.run_id)
    (staging / "artifacts").symlink_to(published, target_is_directory=True)

    with pytest.raises(PathNotAllowedError):
        repository.stage_artifact(staging_context.run_id, "hidden", b"payload")

    assert not (published / "hidden.bin").exists()


def test_staged_artifact_rejects_symlinked_run_staging_directory(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    published_context = _context(revision=1)
    staging_context = _context(revision=2)
    repository.create(published_context)
    repository.publish_atomically(_bundle(published_context))
    repository.create(staging_context)
    _, published, _ = repository._paths(published_context.run_id)
    staging_link = tmp_path / "runs/2026/2026-08-19/.staging" / staging_context.run_id
    (staging_link / "run.json").unlink()
    staging_link.rmdir()
    staging_link.symlink_to(published, target_is_directory=True)

    with pytest.raises(PathNotAllowedError):
        repository.stage_artifact(staging_context.run_id, "hidden", b"payload")
    with pytest.raises(PathNotAllowedError):
        repository.read_staged_artifact(staging_context.run_id, "bundle")

    assert not (published / "hidden.bin").exists()


def test_freeze_holds_run_lock_while_committing_cutoff_and_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    repository.checkpoint(
        context.run_id,
        RunCheckpoint(
            run_id=context.run_id,
            stage="COLLECTING",
            execution_status=ExecutionStatus.COLLECTING,
            data_quality_status=DataQualityStatus.PASS,
            delivery_status=DeliveryStatus.MANUAL,
            written_at=NOW,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"config": "c" * 64}),
            resumable=True,
        ),
    )
    cutoff_write_started = Event()
    continue_freeze = Event()
    checkpoint_started = Event()
    checkpoint_lock_attempted = Event()
    checkpoint_finished = Event()
    original_atomic_write = repository._atomic_write
    original_run_lock = repository._run_lock

    def gated_atomic_write(target: Path, payload: bytes) -> None:
        if target.name == "frozen-evidence.json":
            cutoff_write_started.set()
            assert continue_freeze.wait(3)
        original_atomic_write(target, payload)

    @contextmanager
    def tracked_run_lock(market_date: date):
        if checkpoint_started.is_set():
            checkpoint_lock_attempted.set()
        with original_run_lock(market_date):
            yield

    monkeypatch.setattr(repository, "_atomic_write", gated_atomic_write)
    monkeypatch.setattr(repository, "_run_lock", tracked_run_lock)

    def append_checkpoint() -> None:
        checkpoint_started.set()
        try:
            repository.checkpoint(
                context.run_id,
                RunCheckpoint(
                    run_id=context.run_id,
                    stage="ANALYZING",
                    execution_status=ExecutionStatus.ANALYZING,
                    data_quality_status=DataQualityStatus.DEGRADED,
                    delivery_status=DeliveryStatus.DELAYED,
                    written_at=NOW + timedelta(minutes=1),
                    evidence_cutoff_at=None,
                    artifact_hashes=FrozenMap({"research_packet": "a" * 64}),
                    resumable=True,
                ),
            )
        finally:
            checkpoint_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        freeze_future = executor.submit(
            repository.freeze_evidence,
            context.run_id,
            NOW + timedelta(minutes=13),
        )
        try:
            assert cutoff_write_started.wait(2)
            checkpoint_future = executor.submit(append_checkpoint)
            assert checkpoint_started.wait(2)
            assert checkpoint_lock_attempted.wait(2)
            assert not checkpoint_finished.wait(0.2)
        finally:
            continue_freeze.set()
        frozen = freeze_future.result()
        with pytest.raises(ValueError, match="new revision"):
            checkpoint_future.result()

    assert frozen.checkpoints[-1].stage == "EVIDENCE_FROZEN"
    assert frozen.checkpoints[-1].execution_status is ExecutionStatus.COLLECTING


def test_post_cutoff_collection_resume_is_rejected_but_frozen_packet_validation_is_allowed(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    repository.checkpoint(
        context.run_id,
        RunCheckpoint(
            run_id=context.run_id,
            stage="ANALYZING",
            execution_status=ExecutionStatus.ANALYZING,
            data_quality_status=DataQualityStatus.PASS,
            delivery_status=DeliveryStatus.MANUAL,
            written_at=NOW + timedelta(minutes=1),
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({}),
            resumable=True,
        ),
    )
    cutoff = NOW + timedelta(minutes=13)
    repository.freeze_evidence(context.run_id, cutoff)

    with pytest.raises(ValueError, match="new revision"):
        repository.checkpoint(
            context.run_id,
            RunCheckpoint(
                run_id=context.run_id,
                stage="COLLECTING",
                execution_status=ExecutionStatus.COLLECTING,
                data_quality_status=DataQualityStatus.PASS,
                delivery_status=DeliveryStatus.MANUAL,
                written_at=cutoff + timedelta(seconds=1),
                evidence_cutoff_at=cutoff,
                artifact_hashes=FrozenMap({"research_packet": "a" * 64}),
                resumable=True,
            ),
        )

    packet_hash = repository.stage_artifact(context.run_id, "research_packet", b"packet")
    repository.checkpoint(
        context.run_id,
        RunCheckpoint(
            run_id=context.run_id,
            stage="AWAITING_SYNTHESIS",
            execution_status=ExecutionStatus.AWAITING_SYNTHESIS,
            data_quality_status=DataQualityStatus.PASS,
            delivery_status=DeliveryStatus.MANUAL,
            written_at=cutoff + timedelta(seconds=2),
            evidence_cutoff_at=cutoff,
            artifact_hashes=FrozenMap({"research_packet": packet_hash}),
            resumable=False,
        ),
    )
    repository.checkpoint(
        context.run_id,
        RunCheckpoint(
            run_id=context.run_id,
            stage="VALIDATING",
            execution_status=ExecutionStatus.VALIDATING,
            data_quality_status=DataQualityStatus.PASS,
            delivery_status=DeliveryStatus.MANUAL,
            written_at=cutoff + timedelta(seconds=3),
            evidence_cutoff_at=cutoff,
            artifact_hashes=FrozenMap({"research_packet": packet_hash}),
            resumable=False,
        ),
    )
    stored = repository.load(context.run_id)
    assert stored is not None
    assert stored.checkpoints[-1].stage == "VALIDATING"

    with pytest.raises(ValueError, match="new revision"):
        repository.checkpoint(
            context.run_id,
            RunCheckpoint(
                run_id=context.run_id,
                stage="ANALYZING",
                execution_status=ExecutionStatus.ANALYZING,
                data_quality_status=DataQualityStatus.PASS,
                delivery_status=DeliveryStatus.MANUAL,
                written_at=cutoff + timedelta(seconds=3),
                evidence_cutoff_at=cutoff,
                artifact_hashes=FrozenMap({"research_packet": packet_hash}),
                resumable=True,
            ),
        )


def test_post_cutoff_validation_rejects_resumption_and_packet_hash_replacement(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    packet_hash = "a" * 64
    repository.checkpoint(
        context.run_id,
        RunCheckpoint(
            run_id=context.run_id,
            stage="PACKET_FROZEN",
            execution_status=ExecutionStatus.AWAITING_SYNTHESIS,
            data_quality_status=DataQualityStatus.PASS,
            delivery_status=DeliveryStatus.MANUAL,
            written_at=NOW + timedelta(minutes=1),
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"research_packet": packet_hash}),
            resumable=False,
        ),
    )
    cutoff = NOW + timedelta(minutes=13)
    repository.freeze_evidence(context.run_id, cutoff)

    with pytest.raises(ValueError, match="new revision"):
        repository.checkpoint(
            context.run_id,
            RunCheckpoint(
                run_id=context.run_id,
                stage="VALIDATING",
                execution_status=ExecutionStatus.VALIDATING,
                data_quality_status=DataQualityStatus.PASS,
                delivery_status=DeliveryStatus.MANUAL,
                written_at=cutoff + timedelta(seconds=1),
                evidence_cutoff_at=cutoff,
                artifact_hashes=FrozenMap({"research_packet": packet_hash}),
                resumable=True,
            ),
        )

    with pytest.raises(ValueError, match="new revision"):
        repository.checkpoint(
            context.run_id,
            RunCheckpoint(
                run_id=context.run_id,
                stage="VALIDATING",
                execution_status=ExecutionStatus.VALIDATING,
                data_quality_status=DataQualityStatus.PASS,
                delivery_status=DeliveryStatus.MANUAL,
                written_at=cutoff + timedelta(seconds=2),
                evidence_cutoff_at=cutoff,
                artifact_hashes=FrozenMap({"research_packet": "b" * 64}),
                resumable=False,
            ),
        )


def test_post_cutoff_evidence_frozen_checkpoint_cannot_replace_packet_or_resume(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    repository.checkpoint(
        context.run_id,
        RunCheckpoint(
            run_id=context.run_id,
            stage="PACKET_FROZEN",
            execution_status=ExecutionStatus.AWAITING_SYNTHESIS,
            data_quality_status=DataQualityStatus.PASS,
            delivery_status=DeliveryStatus.MANUAL,
            written_at=NOW + timedelta(minutes=1),
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"research_packet": "a" * 64}),
            resumable=False,
        ),
    )
    cutoff = NOW + timedelta(minutes=13)
    repository.freeze_evidence(context.run_id, cutoff)

    with pytest.raises(ValueError, match="new revision"):
        repository.checkpoint(
            context.run_id,
            RunCheckpoint(
                run_id=context.run_id,
                stage="EVIDENCE_FROZEN",
                execution_status=ExecutionStatus.AWAITING_SYNTHESIS,
                data_quality_status=DataQualityStatus.PASS,
                delivery_status=DeliveryStatus.MANUAL,
                written_at=cutoff + timedelta(seconds=1),
                evidence_cutoff_at=cutoff,
                artifact_hashes=FrozenMap({"research_packet": "b" * 64}),
                resumable=True,
            ),
        )


def test_publication_failure_keeps_staging_and_does_not_update_latest(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    repository.inject_failure_before_rename = True

    with pytest.raises(PublicationError):
        repository.publish_atomically(_bundle(context))

    assert repository.get_latest(context.market_date) is None
    assert repository.diagnostic_staging_exists(context.run_id)
    assert (tmp_path / "runs/2026/2026-08-19/premarket-2026-08-19-r1").exists() is False


def test_publication_rejects_same_id_bundle_with_different_complete_context(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    different_context = context.model_copy(update={"delivery_status": DeliveryStatus.DELAYED})

    with pytest.raises(PublicationError, match="RunContext"):
        repository.publish_atomically(_bundle(different_context))

    assert repository.get_latest(context.market_date) is None
    assert repository.diagnostic_staging_exists(context.run_id)


def test_publication_accepts_final_status_snapshot_from_latest_checkpoint(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    checkpoint = RunCheckpoint(
        run_id=context.run_id,
        stage="VALIDATING",
        execution_status=ExecutionStatus.VALIDATING,
        data_quality_status=DataQualityStatus.DEGRADED,
        delivery_status=DeliveryStatus.DELAYED,
        written_at=NOW + timedelta(minutes=11),
        evidence_cutoff_at=context.evidence_cutoff_at,
        artifact_hashes=FrozenMap({"research_packet": "a" * 64}),
        resumable=False,
    )
    repository.checkpoint(context.run_id, checkpoint)
    final_context = context.model_copy(
        update={
            "execution_status": checkpoint.execution_status,
            "data_quality_status": checkpoint.data_quality_status,
            "delivery_status": checkpoint.delivery_status,
        }
    )

    repository.publish_atomically(_bundle(final_context))

    published = repository.load_published_bundle(context.run_id)
    assert published is not None
    assert published.run == final_context
    stored = repository.load(context.run_id)
    assert stored is not None
    assert stored.checkpoints == (checkpoint,)
    assert stored.run == context


def test_publication_holds_run_lock_after_status_check_until_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    checkpoint = RunCheckpoint(
        run_id=context.run_id,
        stage="ANALYZING",
        execution_status=ExecutionStatus.ANALYZING,
        data_quality_status=DataQualityStatus.DEGRADED,
        delivery_status=DeliveryStatus.DELAYED,
        written_at=NOW + timedelta(minutes=1),
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({"research_packet": "a" * 64}),
        resumable=True,
    )
    repository.checkpoint(context.run_id, checkpoint)
    final_context = context.model_copy(
        update={
            "execution_status": checkpoint.execution_status,
            "data_quality_status": checkpoint.data_quality_status,
            "delivery_status": checkpoint.delivery_status,
        }
    )
    validation_started = Event()
    continue_publication = Event()
    checkpoint_started = Event()
    checkpoint_lock_attempted = Event()
    checkpoint_finished = Event()
    original_atomic_write = repository._atomic_write
    original_run_lock = repository._run_lock

    def gated_atomic_write(target: Path, payload: bytes) -> None:
        if target.name == "bundle.json":
            validation_started.set()
            assert continue_publication.wait(3)
        original_atomic_write(target, payload)

    @contextmanager
    def tracked_run_lock(market_date: date):
        if checkpoint_started.is_set():
            checkpoint_lock_attempted.set()
        with original_run_lock(market_date):
            yield

    monkeypatch.setattr(repository, "_atomic_write", gated_atomic_write)
    monkeypatch.setattr(repository, "_run_lock", tracked_run_lock)

    def append_checkpoint() -> None:
        checkpoint_started.set()
        try:
            repository.checkpoint(
                context.run_id,
                RunCheckpoint(
                    run_id=context.run_id,
                    stage="VALIDATING",
                    execution_status=ExecutionStatus.VALIDATING,
                    data_quality_status=DataQualityStatus.FAIL,
                    delivery_status=DeliveryStatus.DELAYED,
                    written_at=NOW + timedelta(minutes=2),
                    evidence_cutoff_at=None,
                    artifact_hashes=FrozenMap({"research_packet": "b" * 64}),
                    resumable=False,
                ),
            )
        finally:
            checkpoint_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        publish_future = executor.submit(repository.publish_atomically, _bundle(final_context))
        try:
            assert validation_started.wait(2)
            checkpoint_future = executor.submit(append_checkpoint)
            assert checkpoint_started.wait(2)
            assert checkpoint_lock_attempted.wait(2)
            assert not checkpoint_finished.wait(0.2)
        finally:
            continue_publication.set()
        publish_future.result()
        with pytest.raises(ValueError, match="run staging does not exist"):
            checkpoint_future.result()

    published = repository.load_published_bundle(context.run_id)
    assert published is not None
    assert published.run == final_context


@pytest.mark.parametrize(
    "execution_status,data_quality_status,delivery_status",
    (
        (ExecutionStatus.VALIDATING, DataQualityStatus.PASS, DeliveryStatus.MANUAL),
        (ExecutionStatus.CREATED, DataQualityStatus.DEGRADED, DeliveryStatus.MANUAL),
        (ExecutionStatus.CREATED, DataQualityStatus.PASS, DeliveryStatus.DELAYED),
    ),
)
def test_publication_rejects_each_status_that_disagrees_with_latest_checkpoint(
    tmp_path: Path,
    execution_status: ExecutionStatus,
    data_quality_status: DataQualityStatus,
    delivery_status: DeliveryStatus,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    repository.checkpoint(
        context.run_id,
        RunCheckpoint(
            run_id=context.run_id,
            stage="VALIDATING",
            execution_status=execution_status,
            data_quality_status=data_quality_status,
            delivery_status=delivery_status,
            written_at=NOW + timedelta(minutes=11),
            evidence_cutoff_at=context.evidence_cutoff_at,
            artifact_hashes=FrozenMap({"research_packet": "a" * 64}),
            resumable=False,
        ),
    )

    with pytest.raises(PublicationError, match="final checkpoint status snapshot"):
        repository.publish_atomically(_bundle(context))

    assert repository.get_latest(context.market_date) is None
    assert repository.diagnostic_staging_exists(context.run_id)


def test_publication_hashes_and_visibility_are_atomic(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    bundle = _bundle(context)
    artifact = repository.publish_atomically(bundle)
    final = tmp_path / "runs/2026/2026-08-19/premarket-2026-08-19-r1"

    assert artifact.run_id == context.run_id
    assert (
        artifact.markdown_sha256 == hashlib.sha256((final / "report.md").read_bytes()).hexdigest()
    )
    assert (
        artifact.bundle_sha256 == hashlib.sha256((final / "bundle.json").read_bytes()).hexdigest()
    )
    assert repository.get_latest(context.market_date) == context.run_id
    assert repository.get_report(context.run_id) == "# Synthetic report\n"
    assert final.is_dir()
    assert not (final / ".staging").exists()


def test_publication_receipt_uses_injected_clock_and_keeps_original_time(
    tmp_path: Path,
) -> None:
    from inspect import signature

    assert "clock" in signature(FileSystemRunRepository).parameters, (
        "publication receipt timestamps must use the trusted injected clock"
    )
    published_at = NOW + timedelta(minutes=20)

    def publication_clock() -> datetime:
        return published_at

    repository = FileSystemRunRepository(tmp_path, clock=publication_clock)
    context = _context()
    repository.create(context)

    receipt = repository.publish_atomically(_bundle(context))

    assert receipt.published_at == published_at
    assert repository.get_published_artifact(context.run_id) == receipt

    def later_clock() -> datetime:
        return published_at + timedelta(minutes=5)

    reloaded = FileSystemRunRepository(tmp_path, create_layout=False, clock=later_clock)
    assert reloaded.get_published_artifact(context.run_id) == receipt


def test_publication_rejects_report_hash_mismatch_without_exposing_output(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    bundle = PublishedRunBundle(
        run=context,
        bundle=FrozenMap({"kind": "minimal-frozen-run"}),
        report_markdown="# wrong hash\n",
        markdown_sha256="f" * 64,
    )

    with pytest.raises(PublicationError, match="SHA-256"):
        repository.publish_atomically(bundle)

    assert repository.get_latest(context.market_date) is None
    assert repository.get_report(context.run_id) is None
    assert repository.diagnostic_staging_exists(context.run_id)


def test_publication_requires_declared_markdown_hash(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    bundle = PublishedRunBundle(
        run=context,
        bundle=FrozenMap({"kind": "minimal-frozen-run"}),
        report_markdown="# missing hash\n",
    )

    with pytest.raises(PublicationError, match="declared markdown SHA-256"):
        repository.publish_atomically(bundle)

    assert repository.get_latest(context.market_date) is None
    assert repository.diagnostic_staging_exists(context.run_id)


def test_index_failure_quarantines_complete_final_as_unindexed_orphan(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    repository.inject_failure_during_index_update = True

    with pytest.raises(PublicationError, match="quarantined"):
        repository.publish_atomically(_bundle(context))

    assert repository.get_latest(context.market_date) is None
    assert repository.get_report(context.run_id) is None
    assert repository.load(context.run_id) is None
    assert repository.load_published_bundle(context.run_id) is None
    assert repository.diagnostic_orphan_exists(context.run_id)


def test_unindexed_final_run_is_not_exposed_by_load_or_bundle_lookup(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    repository.publish_atomically(_bundle(context))

    index = tmp_path / "reports/2026/2026-08-19/index.json"
    index.unlink()

    assert repository.load(context.run_id) is None
    assert repository.load_published_bundle(context.run_id) is None


def test_latest_remains_highest_published_revision_when_publication_order_differs(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    first = _context(1)
    second = _context(2)
    repository.create(first)
    repository.create(second)

    repository.publish_atomically(_bundle(second, "# r2\n"))
    repository.publish_atomically(_bundle(first, "# r1\n"))

    assert repository.get_latest(first.market_date) == second.run_id


def _publish_prior_research(
    repository: FileSystemRunRepository,
    market_date: date,
    revision: int = 1,
    *,
    research: bool = True,
) -> str:
    context = _context(revision).model_copy(
        update={
            "run_id": f"premarket-{market_date.isoformat()}-r{revision}",
            "market_date": market_date,
            "execution_status": ExecutionStatus.PUBLISHED,
            "data_quality_status": DataQualityStatus.PASS if research else DataQualityStatus.FAIL,
        }
    )
    repository.create(context)
    bundle = _bundle(context).model_copy(
        update={
            "bundle": FrozenMap({"research_packet": {"synthetic": True}})
            if research
            else FrozenMap({"brief_origin": "OPERATIONAL", "failure_code": "INTERNAL_ERROR"}),
        }
    )
    repository.publish_atomically(bundle)
    return context.run_id


def test_previous_research_chooses_latest_indexed_revision_strictly_before_date(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    _publish_prior_research(repository, date(2025, 12, 31))
    latest = _publish_prior_research(repository, date(2026, 8, 19), 2)
    _publish_prior_research(repository, date(2026, 8, 19), 1)
    _publish_prior_research(repository, date(2026, 8, 20))
    _publish_prior_research(repository, date(2026, 8, 21))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    assert repository.get_previous_research_run(date(2026, 8, 20)) == latest
    assert repository.get_previous_research_run(date(2025, 12, 31)) is None
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_previous_research_skips_operational_latest_revision_without_older_revision_fallback(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    earlier = _publish_prior_research(repository, date(2026, 8, 18))
    _publish_prior_research(repository, date(2026, 8, 19), 1)
    _publish_prior_research(repository, date(2026, 8, 19), 2, research=False)

    assert repository.get_previous_research_run(date(2026, 8, 20)) == earlier


def test_previous_research_missing_storage_is_read_only(tmp_path: Path) -> None:
    root = tmp_path / "absent"
    repository = FileSystemRunRepository(root, create_layout=False)

    assert repository.get_previous_research_run(date(2026, 8, 20)) is None
    assert not root.exists()


def _publish_operational_with_telemetry(repository: FileSystemRunRepository) -> str:
    from finance_research_agent.application.publication_service import publish_operational_report
    from finance_research_agent.domain.errors import ErrorCode

    context = _context().model_copy(update={
        "evidence_cutoff_at": None, "data_quality_status": DataQualityStatus.FAIL,
    })
    repository.create(context)
    repository.checkpoint(context.run_id, RunCheckpoint(
        run_id=context.run_id, stage="CONFIG_FROZEN",
        execution_status=context.execution_status, data_quality_status=DataQualityStatus.FAIL,
        delivery_status=context.delivery_status, written_at=NOW, evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({}), resumable=True,
    ))
    publish_operational_report(repository, context, ErrorCode.INTERNAL_ERROR, NOW)
    return context.run_id


def test_previous_research_accepts_checkpoint_bound_operational_telemetry(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    earlier = _publish_prior_research(repository, date(2026, 8, 18))
    _publish_operational_with_telemetry(repository)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    assert repository.get_previous_research_run(date(2026, 8, 20)) == earlier
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("corruption", ["missing_run", "wrong_identity", "wrong_version"])
def test_previous_research_rejects_corrupt_legacy_operational_stored_identity(
    tmp_path: Path, corruption: str,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    _publish_prior_research(repository, date(2026, 8, 18))
    run_id = _publish_prior_research(repository, date(2026, 8, 19), research=False)
    path = tmp_path / "runs/2026/2026-08-19" / run_id / "run.json"
    if corruption == "missing_run":
        path.unlink()
    else:
        context = json.loads(path.read_bytes())
        if corruption == "wrong_identity":
            context.update(run_id="premarket-2026-08-19-r2", revision=2)
        else:
            context["skill_version"] = "changed"
        path.write_text(json.dumps(context), encoding="utf-8")

    with pytest.raises(PublicationError):
        repository.get_previous_research_run(date(2026, 8, 20))


@pytest.mark.parametrize("corruption", ["corrupt", "missing", "symlink_inside", "symlink_outside"])
def test_previous_research_verifies_final_operational_telemetry_artifact_bytes(
    tmp_path: Path, corruption: str,
) -> None:
    repository = FileSystemRunRepository(tmp_path / "data")
    _publish_prior_research(repository, date(2026, 8, 18))
    run_id = _publish_operational_with_telemetry(repository)
    final = tmp_path / "data/runs/2026/2026-08-19" / run_id
    artifact = next((final / "artifacts").glob("performance_telemetry_*.bin"))
    if corruption == "corrupt":
        artifact.write_bytes(b"corrupt")
    elif corruption == "missing":
        artifact.unlink()
    else:
        alias = (tmp_path / "data" if corruption == "symlink_inside" else tmp_path) / "alias"
        alias.write_bytes(artifact.read_bytes())
        artifact.unlink()
        artifact.symlink_to(alias)

    with pytest.raises((PublicationError, PathNotAllowedError)):
        repository.get_previous_research_run(date(2026, 8, 20))


@pytest.mark.parametrize("corruption", [
    "missing_value", "missing_digest", "missing_pair", "null_pair", "malformed_value",
    "wrong_digest", "noncanonical_value", "missing_checkpoint", "wrong_checkpoint_hash",
    "wrong_checkpoint_name", "wrong_checkpoint_identity", "wrong_checkpoint_stage",
    "wrong_run_identity", "missing_all_checkpoints",
])
def test_previous_research_rejects_invalid_operational_telemetry_binding(
    tmp_path: Path, corruption: str,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    _publish_prior_research(repository, date(2026, 8, 18))
    run_id = _publish_operational_with_telemetry(repository)
    final = tmp_path / "runs/2026/2026-08-19" / run_id
    bundle_path = final / "bundle.json"
    payload = json.loads(bundle_path.read_bytes())
    contents = payload["bundle"]
    if corruption == "missing_value":
        del contents["performance_telemetry"]
    elif corruption == "missing_digest":
        del contents["performance_telemetry_sha256"]
    elif corruption == "missing_pair":
        del contents["performance_telemetry"]
        del contents["performance_telemetry_sha256"]
    elif corruption == "null_pair":
        contents.update(performance_telemetry=None, performance_telemetry_sha256=None)
    elif corruption == "malformed_value":
        contents["performance_telemetry"] = "private-secret"
    elif corruption == "wrong_digest":
        contents["performance_telemetry_sha256"] = "a" * 64
    elif corruption == "noncanonical_value":
        del contents["performance_telemetry"]["schema_version"]
    elif corruption == "wrong_run_identity":
        path = final / "run.json"
        context = json.loads(path.read_bytes())
        context.update(run_id="premarket-2026-08-19-r2", revision=2)
        path.write_text(json.dumps(context), encoding="utf-8")
    elif corruption == "missing_all_checkpoints":
        for path in (final / "checkpoints").glob("*.json"):
            path.unlink()
        (final / "run.json").write_text(json.dumps(payload["run"]), encoding="utf-8")
    else:
        checkpoint_path = sorted((final / "checkpoints").glob("*.json"))[-1]
        checkpoint = json.loads(checkpoint_path.read_bytes())
        name = next(key for key in checkpoint["artifact_hashes"]
                    if key.startswith("performance_telemetry_"))
        if corruption == "missing_checkpoint":
            checkpoint_path.unlink()
        else:
            if corruption == "wrong_checkpoint_hash":
                checkpoint["artifact_hashes"][name] = "a" * 64
            elif corruption == "wrong_checkpoint_name":
                digest = checkpoint["artifact_hashes"].pop(name)
                checkpoint["artifact_hashes"]["performance_telemetry_" + "a" * 64] = digest
            elif corruption == "wrong_checkpoint_identity":
                checkpoint["run_id"] = "premarket-2026-08-19-r2"
            else:
                checkpoint["stage"] = "CONFIG_FROZEN"
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    bundle_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")),
                           encoding="utf-8")
    index_path = tmp_path / "reports/2026/2026-08-19/index.json"
    index = json.loads(index_path.read_bytes())
    index[run_id]["bundle_sha256"] = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(PublicationError):
        repository.get_previous_research_run(date(2026, 8, 20))


@pytest.mark.parametrize(
    "contents,quality",
    [
        ({"operational_reason": "INTERNAL_ERROR"}, DataQualityStatus.FAIL),
        ({"brief_origin": "OTHER", "failure_code": "INTERNAL_ERROR"}, DataQualityStatus.FAIL),
        ({"brief_origin": "OPERATIONAL", "failure_code": "UNKNOWN"}, DataQualityStatus.FAIL),
        ({"brief_origin": "OPERATIONAL", "failure_code": "INTERNAL_ERROR"}, DataQualityStatus.PASS),
    ],
)
def test_previous_research_rejects_unrecognized_packetless_publication(tmp_path, contents, quality):
    repository = FileSystemRunRepository(tmp_path)
    context = _context().model_copy(
        update={
            "execution_status": ExecutionStatus.PUBLISHED,
            "data_quality_status": quality,
        }
    )
    repository.create(context)
    repository.publish_atomically(
        _bundle(context).model_copy(update={"bundle": FrozenMap(contents)})
    )
    with pytest.raises(PublicationError):
        repository.get_previous_research_run(date(2026, 8, 20))


@pytest.mark.parametrize("filename", ["report.md", "bundle.json"])
def test_previous_research_corrupt_latest_publication_does_not_fall_back(
    tmp_path: Path,
    filename: str,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    _publish_prior_research(repository, date(2026, 8, 18))
    latest = _publish_prior_research(repository, date(2026, 8, 19))
    target = tmp_path / "runs/2026/2026-08-19" / latest / filename
    target.write_bytes(b"corrupt")

    with pytest.raises(PublicationError):
        repository.get_previous_research_run(date(2026, 8, 20))


@pytest.mark.parametrize(
    "payload",
    [
        b"corrupt",
        b"[]",
        b"{}",
        b'{"../escape":{}}',
        b'{"premarket-2026-08-18-r1":{}}',
    ],
)
def test_previous_research_rejects_corrupt_publication_index(
    tmp_path: Path,
    payload: bytes,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    _publish_prior_research(repository, date(2026, 8, 19))
    (tmp_path / "reports/2026/2026-08-19/index.json").write_bytes(payload)

    with pytest.raises((PublicationError, PathNotAllowedError)):
        repository.get_previous_research_run(date(2026, 8, 20))


@pytest.mark.parametrize(
    "relative",
    [
        "reports",
        "reports/2026",
        "reports/2026/2026-08-19",
        "reports/2026/2026-08-19/index.json",
        "runs",
        "runs/2026",
        "runs/2026/2026-08-19",
        "runs/2026/2026-08-19/premarket-2026-08-19-r1",
        "runs/2026/2026-08-19/premarket-2026-08-19-r1/bundle.json",
        "runs/2026/2026-08-19/premarket-2026-08-19-r1/report.md",
    ],
)
def test_previous_research_rejects_symlink_aliases_even_within_root(
    tmp_path: Path,
    relative: str,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    _publish_prior_research(repository, date(2026, 8, 19))
    target = tmp_path / relative
    alias = tmp_path / "alias"
    target.rename(alias)
    target.symlink_to(alias, target_is_directory=alias.is_dir())

    with pytest.raises(PathNotAllowedError):
        repository.get_previous_research_run(date(2026, 8, 20))


def test_previous_research_rejects_indexed_bundle_identity_mismatch(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    latest = _publish_prior_research(repository, date(2026, 8, 19))
    target = tmp_path / "runs/2026/2026-08-19" / latest / "bundle.json"
    payload = json.loads(target.read_bytes())
    payload["run"]["run_id"] = "premarket-2026-08-19-r2"
    payload["run"]["revision"] = 2
    target.write_text(json.dumps(payload), encoding="utf-8")
    index = tmp_path / "reports/2026/2026-08-19/index.json"
    index_payload = json.loads(index.read_bytes())
    index_payload[latest]["bundle_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    index.write_text(json.dumps(index_payload), encoding="utf-8")

    with pytest.raises(PublicationError):
        repository.get_previous_research_run(date(2026, 8, 20))


@pytest.mark.parametrize("change", ["status", "embedded_report", "embedded_hash"])
def test_previous_research_verifies_published_state_and_embedded_report(
    tmp_path: Path,
    change: str,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    latest = _publish_prior_research(repository, date(2026, 8, 19))
    target = tmp_path / "runs/2026/2026-08-19" / latest / "bundle.json"
    payload = json.loads(target.read_bytes())
    if change == "status":
        payload["run"]["execution_status"] = "CREATED"
    elif change == "embedded_report":
        payload["report_markdown"] = "# Other report\n"
    else:
        payload["markdown_sha256"] = "f" * 64
    target.write_text(json.dumps(payload), encoding="utf-8")
    index = tmp_path / "reports/2026/2026-08-19/index.json"
    index_payload = json.loads(index.read_bytes())
    index_payload[latest]["bundle_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    index.write_text(json.dumps(index_payload), encoding="utf-8")

    with pytest.raises(PublicationError):
        repository.get_previous_research_run(date(2026, 8, 20))


def test_previous_research_returns_reduced_packet_for_caller_plan_status_checks(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context().model_copy(update={"execution_status": ExecutionStatus.PUBLISHED})
    repository.create(context)
    bundle = _bundle(context).model_copy(
        update={
            "bundle": FrozenMap(
                {
                    "research_packet": {"synthetic": True},
                    "reduced_reason": "INTERNAL_ERROR",
                }
            )
        }
    )
    repository.publish_atomically(bundle)

    assert repository.get_previous_research_run(date(2026, 8, 20)) == context.run_id


def test_previous_research_ignores_unindexed_final_directory(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    earlier = _publish_prior_research(repository, date(2026, 8, 18))
    _publish_prior_research(repository, date(2026, 8, 19))
    (tmp_path / "reports/2026/2026-08-19/index.json").unlink()

    assert repository.get_previous_research_run(date(2026, 8, 20)) == earlier


def test_previous_research_missing_latest_indexed_bundle_does_not_fall_back(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    _publish_prior_research(repository, date(2026, 8, 19), 1)
    latest = _publish_prior_research(repository, date(2026, 8, 19), 2)
    (tmp_path / "runs/2026/2026-08-19" / latest / "bundle.json").unlink()

    with pytest.raises(PublicationError):
        repository.get_previous_research_run(date(2026, 8, 20))


@pytest.mark.parametrize("relative", ["reports", "reports/2026", "reports/2026/2026-08-19"])
def test_previous_research_rejects_file_in_publication_directory_position(
    tmp_path: Path,
    relative: str,
) -> None:
    repository = FileSystemRunRepository(tmp_path, create_layout=False)
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("invalid directory", encoding="utf-8")

    with pytest.raises(PublicationError):
        repository.get_previous_research_run(date(2026, 8, 20))


def test_previous_research_ignores_unrelated_names_and_noncanonical_dates(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    for relative in (
        "reports/.temporary",
        "reports/2026/.temporary",
        "reports/2026/20260819",
        "reports/2026/2025-08-19",
    ):
        (tmp_path / relative).mkdir(parents=True)

    assert repository.get_previous_research_run(date(2026, 8, 20)) is None


@pytest.mark.parametrize("value", [NOW, "2026-08-20", None])
def test_previous_research_rejects_non_date_inputs(tmp_path: Path, value: object) -> None:
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(ValueError, match="must be a date"):
        repository.get_previous_research_run(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "run_id",
    [
        "/tmp/premarket-2026-08-19-r1",
        "../premarket-2026-08-19-r1",
        "premarket-2026-08-19-r0",
        "premarket-2026-08-19-r01",
        "premarket-2026-08-19-r-1",
        "premarket-2026-08-19-r1\x00outside",
    ],
)
def test_public_run_lookups_reject_unsafe_ids_and_do_not_touch_sentinel(
    tmp_path: Path, run_id: str
) -> None:
    outside = tmp_path.parent / "storage-sentinel.txt"
    outside.write_text("unchanged", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path)

    with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
        repository.load(run_id)

    assert outside.read_text(encoding="utf-8") == "unchanged"


def test_symlinked_storage_parent_cannot_escape_data_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-runs"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    root = tmp_path / "root"
    repository = FileSystemRunRepository(root)
    (root / "runs").rename(root / "runs-real")
    (root / "runs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
        repository.create(_context())

    assert sentinel.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize("read_target", ["run", "checkpoints", "frozen", "report", "bundle"])
def test_read_paths_reject_symlinked_files_and_nested_directories(
    tmp_path: Path, read_target: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("unchanged", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path / "data")
    context = _context()
    repository.create(context)
    staging = tmp_path / "data/runs/2026/2026-08-19/.staging/premarket-2026-08-19-r1"

    if read_target == "run":
        (staging / "run.json").unlink()
        (staging / "run.json").symlink_to(outside / "sentinel.txt")
        with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
            repository.load(context.run_id)
    elif read_target == "checkpoints":
        checkpoints = staging / "checkpoints"
        checkpoints.mkdir()
        (checkpoints / "0001-COLLECTING.json").symlink_to(outside / "sentinel.txt")
        with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
            repository.load(context.run_id)
    elif read_target == "frozen":
        repository.freeze_evidence(context.run_id, NOW + timedelta(minutes=13))
        (staging / "frozen-evidence.json").unlink()
        (staging / "frozen-evidence.json").symlink_to(outside / "sentinel.txt")
        with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
            repository.load(context.run_id)
    else:
        repository.publish_atomically(_bundle(context))
        final = tmp_path / "data/runs/2026/2026-08-19/premarket-2026-08-19-r1"
        if read_target == "report":
            filename = "report.md"
            (final / filename).unlink()
            (final / filename).symlink_to(outside / "sentinel.txt")
        else:
            filename = "bundle.json"
            (final / filename).unlink()
            (final / filename).symlink_to(outside / "sentinel.txt")
        with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
            if read_target == "report":
                repository.get_report(context.run_id)
            else:
                repository.load_published_bundle(context.run_id)

    assert (outside / "sentinel.txt").read_text(encoding="utf-8") == "unchanged"


def test_report_index_symlink_cannot_escape_data_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.json").write_text("{}", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path / "data")
    context = _context()
    repository.create(context)
    repository.publish_atomically(_bundle(context))
    index = tmp_path / "data/reports/2026/2026-08-19/index.json"
    index.unlink()
    index.symlink_to(outside / "index.json")

    with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
        repository.get_report(context.run_id)


def test_publication_index_read_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_index = outside / "index.json"
    outside_index.write_text("{}", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path / "data")
    context = _context()
    repository.create(context)
    index = tmp_path / "data/reports/2026/2026-08-19/index.json"
    index.parent.mkdir(parents=True)
    index.symlink_to(outside_index)

    with pytest.raises(PathNotAllowedError, match="PATH_NOT_ALLOWED"):
        repository.publish_atomically(_bundle(context))

    assert repository.get_latest(context.market_date) is None
    assert repository.diagnostic_orphan_exists(context.run_id)
    assert outside_index.read_text(encoding="utf-8") == "{}"


def test_storage_layout_has_only_the_allowlisted_top_level_directories(tmp_path: Path) -> None:
    FileSystemRunRepository(tmp_path)
    assert {path.name for path in tmp_path.iterdir()} == {
        "config",
        "runs",
        "reports",
        "cache",
        "diagnostics",
        "logs",
    }


def test_minimal_frozen_run_fixture_is_a_strict_bundle() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "artifacts" / "minimal-frozen-run.json"
    bundle = PublishedRunBundle.model_validate_json(fixture.read_bytes())

    assert bundle.run.run_id == "premarket-2026-08-19-r1"
    assert bundle.bundle["kind"] == "minimal-frozen-run"
