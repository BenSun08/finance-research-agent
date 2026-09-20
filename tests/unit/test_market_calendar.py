from datetime import UTC, date, datetime, time

import pytest

from finance_research_agent.domain.enums import DeliveryStatus, InvocationType
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.market_calendar import (
    resolve_run_window,
)


class FakeCalendar:
    def __init__(self, valid_dates: set[date]) -> None:
        self.valid_dates = valid_dates

    def is_trading_day(self, market_date: date) -> bool:
        return market_date in self.valid_dates

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(market_date, time(9, 30), tzinfo=UTC),
            datetime.combine(market_date, time(16), tzinfo=UTC),
        )


def test_0845_new_york_after_dst_is_on_time() -> None:
    decision = resolve_run_window(
        now_utc=datetime(2026, 3, 9, 12, 45, tzinfo=UTC),
        calendar=FakeCalendar({date(2026, 3, 9)}),
        requested_market_date=None,
        invocation=InvocationType.SCHEDULED,
    )

    assert decision.market_date == date(2026, 3, 9)
    assert decision.delivery_status is DeliveryStatus.ON_TIME
    assert decision.allow_normal_plan is True
    assert decision.force_review_required is False


def test_holiday_is_skipped_by_calendar_not_weekday() -> None:
    decision = resolve_run_window(
        now_utc=datetime(2026, 7, 3, 12, 45, tzinfo=UTC),
        calendar=FakeCalendar(set()),
        requested_market_date=None,
        invocation=InvocationType.SCHEDULED,
    )

    assert decision.should_run is False
    assert decision.reason_code == "NON_TRADING_DAY"


@pytest.mark.parametrize(
    ("now_utc", "delivery_status", "allow_normal_plan", "force_review_required"),
    [
        (datetime(2026, 8, 19, 12, 44, 59, 999999, tzinfo=UTC), None, False, False),
        (datetime(2026, 8, 19, 13, 0, tzinfo=UTC), DeliveryStatus.DELAYED, False, False),
        (
            datetime(2026, 8, 19, 13, 24, 59, 999999, tzinfo=UTC),
            DeliveryStatus.DELAYED,
            False,
            False,
        ),
        (datetime(2026, 8, 19, 13, 25, tzinfo=UTC), DeliveryStatus.DELAYED, True, True),
        (datetime(2026, 8, 19, 13, 29, 59, 999999, tzinfo=UTC), DeliveryStatus.DELAYED, True, True),
    ],
)
def test_scheduled_boundaries_are_explicit(
    now_utc: datetime,
    delivery_status: DeliveryStatus | None,
    allow_normal_plan: bool,
    force_review_required: bool,
) -> None:
    decision = resolve_run_window(
        now_utc,
        FakeCalendar({date(2026, 8, 19)}),
        None,
        InvocationType.SCHEDULED,
    )

    assert decision.delivery_status is delivery_status
    assert decision.allow_normal_plan is allow_normal_plan
    assert decision.force_review_required is force_review_required


def test_missed_window_before_close_publishes_missed_report() -> None:
    decision = resolve_run_window(
        datetime(2026, 8, 19, 13, 30, tzinfo=UTC),
        FakeCalendar({date(2026, 8, 19)}),
        None,
        InvocationType.SCHEDULED,
    )

    assert decision.delivery_status is DeliveryStatus.MISSED_WINDOW
    assert decision.should_run is True
    assert decision.publish_missed_report is True
    assert decision.missed_record_only is False
    assert decision.allow_normal_plan is False


def test_after_close_is_record_only() -> None:
    decision = resolve_run_window(
        datetime(2026, 8, 19, 20, 0, tzinfo=UTC),
        FakeCalendar({date(2026, 8, 19)}),
        None,
        InvocationType.SCHEDULED,
    )

    assert decision.delivery_status is DeliveryStatus.MISSED_WINDOW
    assert decision.should_run is False
    assert decision.publish_missed_report is False
    assert decision.missed_record_only is True


def test_manual_invocation_before_missed_window_uses_manual_delivery() -> None:
    decision = resolve_run_window(
        datetime(2026, 8, 19, 13, 10, tzinfo=UTC),
        FakeCalendar({date(2026, 8, 19)}),
        None,
        InvocationType.MANUAL,
    )

    assert decision.delivery_status is DeliveryStatus.MANUAL
    assert decision.should_run is True


def test_calendar_failure_uses_stable_error_code() -> None:
    class BrokenCalendar(FakeCalendar):
        def is_trading_day(self, market_date: date) -> bool:
            raise RuntimeError("provider-specific failure")

    with pytest.raises(RuntimeError, match=ErrorCode.MARKET_CALENDAR_UNAVAILABLE):
        resolve_run_window(
            datetime(2026, 8, 19, 12, 45, tzinfo=UTC),
            BrokenCalendar({date(2026, 8, 19)}),
            None,
            InvocationType.SCHEDULED,
        )
