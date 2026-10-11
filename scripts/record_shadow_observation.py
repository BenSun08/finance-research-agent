"""Record one strict shadow-day observation in the configured private store."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from finance_research_agent.adapters.exchange_calendar import ExchangeCalendarAdapter
from finance_research_agent.adapters.feedback import FileSystemFeedbackRepository
from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.adapters.shadow_evidence import FileSystemShadowObservationRepository
from finance_research_agent.application.shadow_evidence_service import (
    RecordShadowDayObservation,
    record_shadow_day_observation,
)
from scripts._private_shadow_evidence import (
    private_evidence_roots,
    read_strict_request,
    write_receipt,
)


class _SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.parse_args(argv)
    try:
        request = read_strict_request(RecordShadowDayObservation)
        data_root, record_root = private_evidence_roots("shadow-observations")
        repository = FileSystemShadowObservationRepository(
            record_root,
            run_repository=FileSystemRunRepository(data_root, create_layout=False),
            feedback_repository=FileSystemFeedbackRepository(data_root / "feedback"),
        )
        receipt = record_shadow_day_observation(
            request,
            repository,
            ExchangeCalendarAdapter(),
            _SystemClock(),
        )
    except Exception:
        print(
            "Shadow observation rejected: invalid request or private storage unavailable",
            file=sys.stderr,
        )
        return 1
    write_receipt(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
