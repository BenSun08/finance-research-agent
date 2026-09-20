from datetime import date

import pytest

from finance_research_agent.domain.enums import ExecutionStatus
from finance_research_agent.domain.market_calendar import format_run_id, transition_execution


@pytest.mark.parametrize("revision", [True, 1.5, -1])
def test_run_id_rejects_non_positive_integer_revisions(revision: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        format_run_id(date(2026, 8, 19), revision)  # type: ignore[arg-type]


def test_run_id_is_date_and_positive_integer_revision_stable() -> None:
    assert format_run_id(date(2026, 8, 19), 1) == "premarket-2026-08-19-r1"
    assert format_run_id(date(2026, 8, 19), 12) == "premarket-2026-08-19-r12"
    with pytest.raises(ValueError, match="positive integer"):
        format_run_id(date(2026, 8, 19), 0)


def test_execution_state_rejects_illegal_transition() -> None:
    assert (
        transition_execution(
            ExecutionStatus.CREATED,
            ExecutionStatus.COLLECTING,
        )
        is ExecutionStatus.COLLECTING
    )
    with pytest.raises(ValueError, match="illegal execution transition"):
        transition_execution(
            ExecutionStatus.PUBLISHED,
            ExecutionStatus.COLLECTING,
        )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ExecutionStatus.CREATED, ExecutionStatus.PUBLISHED),
        (ExecutionStatus.CREATED, ExecutionStatus.SKIPPED),
        (ExecutionStatus.COLLECTING, ExecutionStatus.NORMALIZING),
        (ExecutionStatus.NORMALIZING, ExecutionStatus.ANALYZING),
        (ExecutionStatus.ANALYZING, ExecutionStatus.AWAITING_SYNTHESIS),
        (ExecutionStatus.AWAITING_SYNTHESIS, ExecutionStatus.VALIDATING),
        (ExecutionStatus.VALIDATING, ExecutionStatus.AWAITING_SYNTHESIS),
        (ExecutionStatus.VALIDATING, ExecutionStatus.FAILED),
    ],
)
def test_each_declared_legal_execution_transition_is_accepted(
    current: ExecutionStatus,
    target: ExecutionStatus,
) -> None:
    assert transition_execution(current, target) is target
