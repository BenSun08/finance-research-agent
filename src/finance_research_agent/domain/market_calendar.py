"""Deterministic market-window and execution-state contracts."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

from finance_research_agent.domain.enums import DeliveryStatus, ExecutionStatus, InvocationType
from finance_research_agent.domain.errors import ErrorCode

NEW_YORK = ZoneInfo("America/New_York")
_RUN_START = time(8, 45)
_MISSED_WINDOW = time(9, 30)


class TradingCalendar(Protocol):
    """Provider-neutral trading-session calendar boundary."""

    def is_trading_day(self, market_date: date) -> bool: ...

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]: ...


@dataclass(frozen=True)
class RunWindowDecision:
    market_date: date
    should_run: bool
    delivery_status: DeliveryStatus | None
    allow_normal_plan: bool
    force_review_required: bool
    publish_missed_report: bool
    missed_record_only: bool
    reason_code: str


def _calendar_unavailable(error: Exception) -> RuntimeError:
    return RuntimeError(f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: market calendar unavailable")


def _require_utc(now_utc: datetime) -> None:
    if (
        now_utc.tzinfo is None
        or now_utc.utcoffset() != datetime.min.replace(tzinfo=UTC).utcoffset()
    ):
        raise ValueError("now_utc must be timezone-aware UTC")


def resolve_run_window(
    now_utc: datetime,
    calendar: TradingCalendar,
    requested_market_date: date | None,
    invocation: InvocationType,
) -> RunWindowDecision:
    """Classify a scheduled or manual premarket invocation."""
    _require_utc(now_utc)
    local_now = now_utc.astimezone(NEW_YORK)
    market_date = requested_market_date or local_now.date()

    try:
        if not calendar.is_trading_day(market_date):
            return RunWindowDecision(
                market_date, False, None, False, False, False, False, "NON_TRADING_DAY"
            )
        session_open, regular_close = calendar.session_open_close(market_date)
    except Exception as error:
        raise _calendar_unavailable(error) from error

    if (
        session_open.tzinfo is None
        or session_open.utcoffset() is None
        or regular_close.tzinfo is None
        or regular_close.utcoffset() is None
    ):
        raise RuntimeError(
            f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: session times are not timezone-aware"
        )
    if session_open.utcoffset() != UTC.utcoffset(
        session_open
    ) or regular_close.utcoffset() != UTC.utcoffset(regular_close):
        raise RuntimeError(f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: session times are not UTC")

    local_time = local_now.timetz().replace(tzinfo=None)
    if local_time < _RUN_START and invocation is InvocationType.SCHEDULED:
        return RunWindowDecision(market_date, False, None, False, False, False, False, "TOO_EARLY")
    if now_utc >= regular_close:
        return RunWindowDecision(
            market_date,
            False,
            DeliveryStatus.MISSED_WINDOW,
            False,
            False,
            False,
            True,
            "MISSED_WINDOW",
        )
    if local_time >= _MISSED_WINDOW:
        return RunWindowDecision(
            market_date,
            True,
            DeliveryStatus.MISSED_WINDOW,
            False,
            False,
            True,
            False,
            "MISSED_WINDOW",
        )
    if invocation is InvocationType.MANUAL:
        return RunWindowDecision(
            market_date, True, DeliveryStatus.MANUAL, True, False, False, False, "MANUAL"
        )
    if local_time < time(9):
        return RunWindowDecision(
            market_date, True, DeliveryStatus.ON_TIME, True, False, False, False, "ON_TIME"
        )
    if local_time < time(9, 25):
        return RunWindowDecision(
            market_date, True, DeliveryStatus.DELAYED, False, False, False, False, "DELAYED"
        )
    return RunWindowDecision(
        market_date, True, DeliveryStatus.DELAYED, True, True, False, False, "DELAYED"
    )


def format_run_id(market_date: date, revision: int) -> str:
    if revision <= 0:
        raise ValueError("revision must be positive")
    return f"premarket-{market_date.isoformat()}-r{revision}"


LEGAL_EXECUTION_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.CREATED: frozenset(
        {
            ExecutionStatus.COLLECTING,
            ExecutionStatus.PUBLISHED,
            ExecutionStatus.SKIPPED,
            ExecutionStatus.FAILED,
        }
    ),
    ExecutionStatus.COLLECTING: frozenset(
        {ExecutionStatus.NORMALIZING, ExecutionStatus.PUBLISHED, ExecutionStatus.FAILED}
    ),
    ExecutionStatus.NORMALIZING: frozenset(
        {ExecutionStatus.ANALYZING, ExecutionStatus.PUBLISHED, ExecutionStatus.FAILED}
    ),
    ExecutionStatus.ANALYZING: frozenset(
        {ExecutionStatus.AWAITING_SYNTHESIS, ExecutionStatus.PUBLISHED, ExecutionStatus.FAILED}
    ),
    ExecutionStatus.AWAITING_SYNTHESIS: frozenset(
        {ExecutionStatus.VALIDATING, ExecutionStatus.PUBLISHED, ExecutionStatus.FAILED}
    ),
    ExecutionStatus.VALIDATING: frozenset(
        {ExecutionStatus.AWAITING_SYNTHESIS, ExecutionStatus.PUBLISHED, ExecutionStatus.FAILED}
    ),
    ExecutionStatus.PUBLISHED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.SKIPPED: frozenset(),
}


def transition_execution(current: ExecutionStatus, target: ExecutionStatus) -> ExecutionStatus:
    if target not in LEGAL_EXECUTION_TRANSITIONS[current]:
        raise ValueError(f"illegal execution transition: {current} -> {target}")
    return target
