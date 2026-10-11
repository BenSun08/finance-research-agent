from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository, PublicationError
from finance_research_agent.domain.enums import ExecutionStatus
from finance_research_agent.domain.models import PublishedRunBundle
from finance_research_agent.domain.types import FrozenMap, canonical_bytes


def test_list_runs_returns_every_revision_in_range_in_stable_order(
    tmp_path: Path, valid_packet
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    contexts = (
        (date(2026, 9, 15), 2),
        (date(2026, 9, 14), 1),
        (date(2026, 9, 15), 1),
        (date(2026, 9, 16), 1),
    )
    for market_date, revision in contexts:
        run_id = f"premarket-{market_date.isoformat()}-r{revision}"
        repository.create(
            valid_packet.run.model_copy(
                update={
                    "run_id": run_id,
                    "market_date": market_date,
                    "revision": revision,
                }
            )
        )

    runs = repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))

    assert tuple(run.run_id for run in runs) == (
        "premarket-2026-09-15-r1",
        "premarket-2026-09-15-r2",
    )
    assert all(not run.published for run in runs)


def test_list_runs_includes_published_runs_and_uses_inclusive_bounds(
    tmp_path: Path, valid_packet
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    market_date = date(2026, 9, 15)
    run_id = f"premarket-{market_date.isoformat()}-r1"
    run = valid_packet.run.model_copy(
        update={
            "run_id": run_id,
            "market_date": market_date,
            "execution_status": ExecutionStatus.PUBLISHED,
        }
    )
    repository.create(run)
    report = "# Shadow listing fixture\n"
    repository.publish_atomically(
        PublishedRunBundle(
            run=run,
            bundle=FrozenMap({"fixture": "published"}),
            report_markdown=report,
            markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
        )
    )

    runs = repository.list_runs(market_date, market_date)

    assert len(runs) == 1
    assert runs[0].run_id == run_id
    assert runs[0].published is True


def test_list_runs_fails_closed_when_an_indexed_publication_is_missing(
    tmp_path: Path, valid_packet
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    market_date = date(2026, 9, 15)
    run_id = f"premarket-{market_date.isoformat()}-r1"
    run = valid_packet.run.model_copy(
        update={
            "run_id": run_id,
            "market_date": market_date,
            "execution_status": ExecutionStatus.PUBLISHED,
        }
    )
    repository.create(run)
    report = "# Missing indexed run fixture\n"
    repository.publish_atomically(
        PublishedRunBundle(
            run=run,
            bundle=FrozenMap({"fixture": "published"}),
            report_markdown=report,
            markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
        )
    )
    shutil.rmtree(tmp_path / "runs" / "2026" / market_date.isoformat() / run_id)

    with pytest.raises(PublicationError, match="indexed publication is missing or corrupt"):
        repository.list_runs(market_date, market_date)


@pytest.mark.parametrize("published_at", [None, "invalid timestamp"])
def test_list_runs_rejects_an_indexed_publication_with_an_invalid_receipt_time(
    tmp_path: Path, valid_packet, published_at: str | None
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    market_date = date(2026, 9, 15)
    run_id = f"premarket-{market_date.isoformat()}-r1"
    run = valid_packet.run.model_copy(
        update={
            "run_id": run_id,
            "market_date": market_date,
            "execution_status": ExecutionStatus.PUBLISHED,
        }
    )
    repository.create(run)
    report = "# Invalid publication receipt fixture\n"
    repository.publish_atomically(
        PublishedRunBundle(
            run=run,
            bundle=FrozenMap({"fixture": "published"}),
            report_markdown=report,
            markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
        )
    )
    index_path = tmp_path / "reports" / "2026" / market_date.isoformat() / "index.json"
    index = json.loads(index_path.read_bytes())
    if published_at is None:
        del index[run_id]["published_at"]
    else:
        index[run_id]["published_at"] = published_at
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(PublicationError, match="indexed publication is missing or corrupt"):
        repository.list_runs(market_date, market_date)


@pytest.mark.parametrize(
    ("index_contents", "message"),
    [
        ("{", "publication index is invalid"),
        ("[]", "publication index is invalid"),
        ('{"not-a-run-id":{}}', "publication index is invalid"),
        (
            '{"premarket-2026-09-14-r1":{}}',
            "publication index identity is invalid",
        ),
    ],
)
def test_list_runs_fails_closed_on_invalid_publication_indexes(
    tmp_path: Path, index_contents: str, message: str
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    index_path = tmp_path / "reports" / "2026" / "2026-09-15" / "index.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(index_contents, encoding="utf-8")

    with pytest.raises(PublicationError, match=message):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_skips_unrecognized_publication_tree_entries(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    (reports / "notes").mkdir(parents=True)
    (reports / "2025" / "2025-09-15").mkdir(parents=True)
    (reports / "2026" / "not-a-date").mkdir(parents=True)
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    assert repository.list_runs(date(2026, 9, 15), date(2026, 9, 15)) == ()


def test_list_runs_rejects_a_non_directory_publication_date_entry(tmp_path: Path) -> None:
    report_year = tmp_path / "reports" / "2026"
    report_year.mkdir(parents=True)
    (report_year / "2026-09-15").write_text("not a directory", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(PublicationError, match="publication date directory is invalid"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_rejects_a_non_directory_publication_root(tmp_path: Path) -> None:
    (tmp_path / "reports").write_text("not a directory", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(PublicationError, match="publication reports directory is invalid"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_rejects_a_non_directory_publication_year(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026").write_text("not a directory", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(PublicationError, match="publication year directory is invalid"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


@pytest.mark.parametrize(
    ("start", "end", "expected", "message"),
    [
        (
            date(2026, 9, 16),
            date(2026, 9, 15),
            ValueError,
            "start date must not follow end date",
        ),
        (
            datetime(2026, 9, 15, tzinfo=UTC),
            date(2026, 9, 15),
            TypeError,
            "market date bounds must be dates",
        ),
        (
            date(2026, 9, 15),
            datetime(2026, 9, 15, tzinfo=UTC),
            TypeError,
            "market date bounds must be dates",
        ),
    ],
)
def test_list_runs_rejects_a_reversed_or_non_date_range(
    tmp_path: Path,
    start: date | datetime,
    end: date | datetime,
    expected: type[Exception],
    message: str,
) -> None:
    repository = FileSystemRunRepository(tmp_path)

    with pytest.raises(expected, match=message):
        repository.list_runs(start, end)


def test_list_runs_returns_empty_when_the_run_root_is_missing(tmp_path: Path) -> None:
    repository = FileSystemRunRepository(tmp_path / "missing", create_layout=False)

    assert repository.list_runs(date(2026, 9, 15), date(2026, 9, 15)) == ()


def test_list_runs_skips_unrecognized_and_out_of_range_tree_entries(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    (runs / "notes").mkdir(parents=True)
    (runs / "2025").mkdir()
    (runs / "2026" / "not-a-date").mkdir(parents=True)
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    assert repository.list_runs(date(2026, 9, 15), date(2026, 9, 15)) == ()


def test_list_runs_rejects_a_non_directory_run_root(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.write_text("not a directory", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(PublicationError, match="run evidence directory"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_rejects_a_non_directory_year_entry(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "2026").write_text("not a directory", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(PublicationError, match="run year directory"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_rejects_a_non_directory_market_date_entry(tmp_path: Path) -> None:
    year = tmp_path / "runs" / "2026"
    year.mkdir(parents=True)
    (year / "2026-09-15").write_text("not a directory", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(PublicationError, match="run date directory"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_rejects_a_non_directory_staging_entry(tmp_path: Path) -> None:
    day = tmp_path / "runs" / "2026" / "2026-09-15"
    day.mkdir(parents=True)
    (day / ".staging").write_text("not a directory", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(PublicationError, match="run state directory"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_rejects_run_ids_in_the_wrong_market_date_directory(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "2026" / "2026-09-15" / "premarket-2026-09-14-r1"
    run.mkdir(parents=True)
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(PublicationError, match="date does not match"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_rejects_a_matching_run_id_that_is_not_a_directory(tmp_path: Path) -> None:
    day = tmp_path / "runs" / "2026" / "2026-09-15"
    day.mkdir(parents=True)
    (day / "premarket-2026-09-15-r1").write_text("not a directory", encoding="utf-8")
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(PublicationError, match="run directory is invalid"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_fails_closed_on_symlinked_date_directory(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    year = runs / "2026"
    year.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (year / "2026-09-15").symlink_to(external, target_is_directory=True)
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(ValueError, match="unsafe run identifier"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_fails_closed_on_symlinked_run_directory(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    day = runs / "2026" / "2026-09-15"
    day.mkdir(parents=True)
    external = tmp_path / "external-run"
    external.mkdir()
    (day / "premarket-2026-09-15-r1").symlink_to(external, target_is_directory=True)
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(ValueError, match="unsafe run identifier"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_fails_closed_on_symlinked_staging_directory(tmp_path: Path) -> None:
    day = tmp_path / "runs" / "2026" / "2026-09-15"
    day.mkdir(parents=True)
    external = tmp_path / "external-staging"
    external.mkdir()
    (day / ".staging").symlink_to(external, target_is_directory=True)
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(ValueError, match="unsafe run identifier"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_fails_closed_on_a_matching_incomplete_run_directory(tmp_path: Path) -> None:
    run_id = "premarket-2026-09-15-r1"
    path = tmp_path / "runs" / "2026" / "2026-09-15" / run_id
    path.mkdir(parents=True)
    repository = FileSystemRunRepository(tmp_path, create_layout=False)

    with pytest.raises(RuntimeError, match="incomplete or corrupt"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_rejects_context_identity_that_differs_from_its_directory(
    tmp_path: Path, valid_packet
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    run_id = "premarket-2026-09-15-r1"
    run_directory = tmp_path / "runs" / "2026" / "2026-09-15" / ".staging" / run_id
    run_directory.mkdir(parents=True)
    mismatched_context = valid_packet.run.model_copy(
        update={"run_id": "premarket-2026-09-14-r1", "market_date": date(2026, 9, 14)}
    )
    (run_directory / "run.json").write_bytes(canonical_bytes(mismatched_context))

    with pytest.raises(PublicationError, match="stored context identity"):
        repository.list_runs(date(2026, 9, 15), date(2026, 9, 15))


def test_list_runs_counts_publication_quarantined_as_an_unpublished_attempt(
    tmp_path: Path, valid_packet
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    market_date = date(2026, 9, 15)
    run_id = f"premarket-{market_date.isoformat()}-r1"
    run = valid_packet.run.model_copy(
        update={
            "run_id": run_id,
            "market_date": market_date,
            "execution_status": ExecutionStatus.PUBLISHED,
        }
    )
    repository.create(run)
    report = "# Interrupted publication fixture\n"
    bundle = PublishedRunBundle(
        run=run,
        bundle=FrozenMap({"fixture": "interrupted"}),
        report_markdown=report,
        markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
    )
    repository.inject_failure_during_index_update = True
    with pytest.raises(PublicationError, match="orphan quarantined"):
        repository.publish_atomically(bundle)

    runs = repository.list_runs(market_date, market_date)

    assert len(runs) == 1
    assert runs[0].run_id == run_id
    assert runs[0].published is False
    assert repository.get_published_artifact(run_id) is None


def test_list_runs_counts_an_indexed_quarantined_publication_as_unpublished(
    tmp_path: Path, valid_packet
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    market_date = date(2026, 9, 15)
    run_id = f"premarket-{market_date.isoformat()}-r1"
    run = valid_packet.run.model_copy(
        update={
            "run_id": run_id,
            "market_date": market_date,
            "execution_status": ExecutionStatus.PUBLISHED,
        }
    )
    repository.create(run)
    report = "# Indexed interrupted publication fixture\n"
    bundle = PublishedRunBundle(
        run=run,
        bundle=FrozenMap({"fixture": "indexed interrupted"}),
        report_markdown=report,
        markdown_sha256=sha256(report.encode("utf-8")).hexdigest(),
    )
    repository.inject_failure_after_index_write = True
    with pytest.raises(PublicationError, match="orphan quarantined"):
        repository.publish_atomically(bundle)

    index_path = tmp_path / "reports" / "2026" / market_date.isoformat() / "index.json"
    assert run_id in json.loads(index_path.read_bytes())

    runs = repository.list_runs(market_date, market_date)

    assert len(runs) == 1
    assert runs[0].run_id == run_id
    assert runs[0].published is False
    assert repository.get_published_artifact(run_id) is None


def test_list_runs_fails_closed_when_a_revision_exists_in_both_states(
    tmp_path: Path, valid_packet
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    market_date = date(2026, 9, 15)
    run_id = f"premarket-{market_date.isoformat()}-r1"
    run = valid_packet.run.model_copy(
        update={"run_id": run_id, "market_date": market_date, "revision": 1}
    )
    repository.create(run)
    staging = tmp_path / "runs" / "2026" / market_date.isoformat() / ".staging" / run_id
    final = tmp_path / "runs" / "2026" / market_date.isoformat() / run_id
    final.mkdir()
    assert staging.is_dir()

    with pytest.raises(RuntimeError, match="multiple storage states"):
        repository.list_runs(market_date, market_date)
