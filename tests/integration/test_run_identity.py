from datetime import UTC, date, datetime
from pathlib import Path

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.domain.enums import InvocationType


def test_automatic_invocation_is_idempotent_and_manual_rerun_increments(
    tmp_path: Path,
) -> None:
    repository = FileSystemRunRepository(tmp_path)
    market_date = date(2026, 8, 19)
    first = repository.allocate_revision(
        market_date,
        InvocationType.SCHEDULED,
        datetime(2026, 8, 19, 12, 45, tzinfo=UTC),
    )
    duplicate = repository.allocate_revision(
        market_date,
        InvocationType.SCHEDULED,
        datetime(2026, 8, 19, 12, 46, tzinfo=UTC),
    )
    manual = repository.allocate_revision(
        market_date,
        InvocationType.MANUAL,
        datetime(2026, 8, 19, 12, 47, tzinfo=UTC),
    )

    assert first.run_id == duplicate.run_id == "premarket-2026-08-19-r1"
    assert manual.run_id == "premarket-2026-08-19-r2"
    assert (tmp_path / "runs/2026/2026-08-19/.staging/premarket-2026-08-19-r1").is_dir()
    assert (tmp_path / "runs/2026/2026-08-19/.staging/premarket-2026-08-19-r2").is_dir()

