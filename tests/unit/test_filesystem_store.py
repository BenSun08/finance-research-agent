import hashlib
from concurrent.futures import ThreadPoolExecutor
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


def _bundle(context: RunContext, report: str = "# Synthetic report\n") -> PublishedRunBundle:
    return PublishedRunBundle(
        run=context,
        bundle=FrozenMap({"kind": "minimal-frozen-run", "run_id": context.run_id}),
        report_markdown=report,
        markdown_sha256=hashlib.sha256(report.encode()).hexdigest(),
    )


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
    market_date = date(2026, 8, 19)

    def allocate(repository: FileSystemRunRepository) -> str:
        return repository.allocate_revision(market_date, InvocationType.MANUAL, NOW).run_id

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
            written_at=NOW,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"config": "c" * 64}),
            resumable=True,
        ),
        RunCheckpoint(
            run_id=context.run_id,
            stage="ANALYZING",
            execution_status=ExecutionStatus.ANALYZING,
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
            written_at=NOW,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"config": "c" * 64}),
            resumable=True,
        ),
    )
    stored = repository.freeze_evidence(context.run_id, NOW + timedelta(minutes=13))

    assert stored.evidence_cutoff_at == NOW + timedelta(minutes=13)
    assert stored.checkpoints[-1].stage == "EVIDENCE_FROZEN"
    with pytest.raises(ValueError, match="new revision"):
        repository.freeze_evidence(context.run_id, NOW + timedelta(minutes=14))


def test_post_cutoff_collection_resume_is_rejected_but_frozen_packet_validation_is_allowed(
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
                stage="COLLECTING",
                execution_status=ExecutionStatus.COLLECTING,
                written_at=cutoff + timedelta(seconds=1),
                evidence_cutoff_at=cutoff,
                artifact_hashes=FrozenMap({"research_packet": "a" * 64}),
                resumable=True,
            ),
        )

    repository.checkpoint(
        context.run_id,
        RunCheckpoint(
            run_id=context.run_id,
            stage="VALIDATING",
            execution_status=ExecutionStatus.VALIDATING,
            written_at=cutoff + timedelta(seconds=2),
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


def test_publication_hashes_and_visibility_are_atomic(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = _context()
    repository.create(context)
    bundle = _bundle(context)
    artifact = repository.publish_atomically(bundle)
    final = tmp_path / "runs/2026/2026-08-19/premarket-2026-08-19-r1"

    assert artifact.run_id == context.run_id
    assert artifact.markdown_sha256 == hashlib.sha256(
        (final / "report.md").read_bytes()
    ).hexdigest()
    assert artifact.bundle_sha256 == hashlib.sha256(
        (final / "bundle.json").read_bytes()
    ).hexdigest()
    assert repository.get_latest(context.market_date) == context.run_id
    assert repository.get_report(context.run_id) == "# Synthetic report\n"
    assert final.is_dir()
    assert not (final / ".staging").exists()


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
    assert {
        path.name for path in tmp_path.iterdir()
    } == {"config", "runs", "reports", "cache", "diagnostics", "logs"}


def test_minimal_frozen_run_fixture_is_a_strict_bundle() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "artifacts" / "minimal-frozen-run.json"
    bundle = PublishedRunBundle.model_validate_json(fixture.read_bytes())

    assert bundle.run.run_id == "premarket-2026-08-19-r1"
    assert bundle.bundle["kind"] == "minimal-frozen-run"
