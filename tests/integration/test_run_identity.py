import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository, PublicationError
from finance_research_agent.domain.enums import DeliveryStatus, InvocationType
from finance_research_agent.domain.models import (
    ComponentVersions,
    ConfigurationSnapshot,
    PublishedRunBundle,
    RunContextSeed,
)
from finance_research_agent.domain.types import FrozenMap

MARKET_DATE = date(2026, 8, 19)
NOW = datetime(2026, 8, 19, 12, 45, tzinfo=UTC)


def _seed(
    *,
    invoked_at: datetime = NOW,
    delivery_status: DeliveryStatus = DeliveryStatus.MANUAL,
    version: str = "1",
) -> RunContextSeed:
    configuration = ConfigurationSnapshot(
        content_hash_sha256=version * 64,
        file_hashes=FrozenMap({"watchlist.yaml": "b" * 64}),
        watchlist_version=version,
        regime_policy_version=version,
        setup_policy_version=version,
        risk_policy_version=version,
        source_policy_version=version,
    )
    return RunContextSeed(
        market_date=MARKET_DATE,
        invoked_at=invoked_at,
        delivery_status=delivery_status,
        configuration_snapshot=configuration,
        component_versions=ComponentVersions(
            core_version="0.5.0.dev0",
            mcp_contract_version="0.1",
            plugin_version="0.1",
            skill_version="0.1",
            prompt_version="0.1",
            report_template_version="0.1",
            schema_versions=FrozenMap({"run-context": "0.1"}),
            watchlist_version=version,
            regime_policy_version=version,
            setup_policy_version=version,
            risk_policy_version=version,
            source_policy_version=version,
        ),
    )


def _run_ids(repository: FileSystemRunRepository, market_date: date) -> tuple[str, ...]:
    return repository._existing_revision_ids(market_date)


def test_automatic_invocation_is_idempotent_and_manual_rerun_increments(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    first = repository.allocate_revision(
        _seed(delivery_status=DeliveryStatus.ON_TIME),
        InvocationType.SCHEDULED,
    )
    duplicate = repository.allocate_revision(
        _seed(
            invoked_at=NOW + timedelta(minutes=1),
            delivery_status=DeliveryStatus.DELAYED,
            version="2",
        ),
        InvocationType.SCHEDULED,
    )
    manual = repository.allocate_revision(
        _seed(invoked_at=NOW + timedelta(minutes=2)),
        InvocationType.MANUAL,
    )

    assert first.run_id == duplicate.run_id == "premarket-2026-08-19-r1"
    assert first.delivery_status is DeliveryStatus.ON_TIME
    assert duplicate == first
    assert manual.run_id == "premarket-2026-08-19-r2"
    assert (tmp_path / "runs/2026/2026-08-19/.staging/premarket-2026-08-19-r1").is_dir()
    assert (tmp_path / "runs/2026/2026-08-19/.staging/premarket-2026-08-19-r2").is_dir()


def test_new_revision_copies_every_seed_field(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    seed = _seed(delivery_status=DeliveryStatus.DELAYED)

    context = repository.allocate_revision(seed, InvocationType.MANUAL)

    assert context.run_id == "premarket-2026-08-19-r1"
    assert context.revision == 1
    assert context.market_date == seed.market_date
    assert context.invoked_at == seed.invoked_at
    assert context.delivery_status is seed.delivery_status
    assert context.configuration_snapshot == seed.configuration_snapshot
    assert context.core_version == seed.component_versions.core_version
    assert context.mcp_contract_version == seed.component_versions.mcp_contract_version
    assert context.plugin_version == seed.component_versions.plugin_version
    assert context.skill_version == seed.component_versions.skill_version
    assert context.prompt_version == seed.component_versions.prompt_version
    assert context.report_template_version == seed.component_versions.report_template_version
    assert context.schema_versions == seed.component_versions.schema_versions
    assert context.evidence_cutoff_at == seed.invoked_at


def test_requested_unpublished_manual_revision_is_reused(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    seed = _seed()
    original = repository.allocate_revision(seed, InvocationType.MANUAL)
    repository.stage_artifact(original.run_id, "checkpoint", b"original")

    resumed = repository.allocate_revision(
        _seed(invoked_at=NOW + timedelta(minutes=1), version="2"),
        InvocationType.MANUAL,
        requested_revision=1,
    )

    assert resumed == original
    assert repository.read_staged_artifact(original.run_id, "checkpoint") == b"original"


def test_requested_manual_revision_creates_only_next_revision(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    first = repository.allocate_revision(_seed(), InvocationType.MANUAL)

    second = repository.allocate_revision(
        _seed(invoked_at=NOW + timedelta(minutes=1), version="2"),
        InvocationType.MANUAL,
        requested_revision=2,
    )

    assert second.run_id == "premarket-2026-08-19-r2"
    assert second.configuration_snapshot.content_hash_sha256 == "2" * 64
    assert first.run_id in _run_ids(repository, MARKET_DATE)
    assert second.run_id in _run_ids(repository, MARKET_DATE)


@pytest.mark.parametrize("requested_revision", [0, -1, True, 1.0, "1"])
def test_invalid_requested_revision_fails_without_staging_changes(
    tmp_path: Path, requested_revision: object
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    before = _run_ids(repository, MARKET_DATE)

    with pytest.raises(ValueError):
        repository.allocate_revision(
            _seed(),
            InvocationType.MANUAL,
            requested_revision=requested_revision,  # type: ignore[arg-type]
        )

    assert _run_ids(repository, MARKET_DATE) == before


def test_allocator_revalidates_seed_before_staging_changes(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    seed = _seed()
    invalid_versions = seed.component_versions.model_copy(
        update={"watchlist_version": "999"}
    )
    invalid_seed = seed.model_copy(update={"component_versions": invalid_versions})
    before = _run_ids(repository, MARKET_DATE)

    with pytest.raises(ValueError):
        repository.allocate_revision(invalid_seed, InvocationType.MANUAL)

    assert _run_ids(repository, MARKET_DATE) == before


def test_allocator_rejects_unvalidated_seed_without_staging_changes(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    before = _run_ids(repository, MARKET_DATE)

    with pytest.raises(ValueError):
        repository.allocate_revision(None, InvocationType.MANUAL)  # type: ignore[arg-type]

    assert _run_ids(repository, MARKET_DATE) == before


def test_allocator_rejects_undeclared_invocation_without_staging_changes(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    before = _run_ids(repository, MARKET_DATE)

    with pytest.raises(ValueError):
        repository.allocate_revision(_seed(), "MANUAL")  # type: ignore[arg-type]

    assert _run_ids(repository, MARKET_DATE) == before


@pytest.mark.parametrize(
    ("invocation", "requested_revision"),
    [(InvocationType.MANUAL, 1), (InvocationType.SCHEDULED, None)],
)
def test_existing_revision_without_context_is_not_overwritten(
    tmp_path: Path,
    invocation: InvocationType,
    requested_revision: int | None,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    run_id = "premarket-2026-08-19-r1"
    staging, _, _ = repository._paths(run_id)
    staging.mkdir(parents=True)
    before = _run_ids(repository, MARKET_DATE)

    with pytest.raises(PublicationError):
        repository.allocate_revision(
            _seed(), invocation, requested_revision=requested_revision
        )

    assert _run_ids(repository, MARKET_DATE) == before
    assert not (staging / "run.json").exists()


def test_scheduled_invocation_rejects_explicit_revision_without_changes(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    before = _run_ids(repository, MARKET_DATE)

    with pytest.raises(ValueError):
        repository.allocate_revision(
            _seed(delivery_status=DeliveryStatus.ON_TIME),
            InvocationType.SCHEDULED,
            requested_revision=1,
        )

    assert _run_ids(repository, MARKET_DATE) == before


def test_requested_manual_revision_rejects_gap_without_staging_changes(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    before = _run_ids(repository, MARKET_DATE)

    with pytest.raises(ValueError):
        repository.allocate_revision(_seed(), InvocationType.MANUAL, requested_revision=2)

    assert _run_ids(repository, MARKET_DATE) == before


def test_requested_manual_revision_rejects_published_run_without_changes(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path)
    context = repository.allocate_revision(_seed(), InvocationType.MANUAL)
    report = "# Synthetic report\n"
    repository.publish_atomically(
        PublishedRunBundle(
            run=context,
            report_markdown=report,
            markdown_sha256=hashlib.sha256(report.encode()).hexdigest(),
        )
    )
    before = _run_ids(repository, MARKET_DATE)
    existing = repository.load(context.run_id)
    assert existing is not None and existing.published

    with pytest.raises(ValueError):
        repository.allocate_revision(
            _seed(version="2"), InvocationType.MANUAL, requested_revision=1
        )

    assert _run_ids(repository, MARKET_DATE) == before
    assert repository.load(context.run_id) == existing
