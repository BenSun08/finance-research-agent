from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import exchange_calendars
import pytest

from finance_research_agent.adapters.exchange_calendar import ExchangeCalendarAdapter
from finance_research_agent.domain.errors import ErrorCode

PUBLIC_ERROR = f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: market calendar unavailable"


def test_constructor_failure_is_redacted_and_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_get_calendar(name: str) -> object:
        raise RuntimeError("provider secret: calendar credentials")

    monkeypatch.setattr(exchange_calendars, "get_calendar", fail_get_calendar)

    with pytest.raises(RuntimeError) as caught:
        ExchangeCalendarAdapter()

    assert str(caught.value) == PUBLIC_ERROR
    assert "provider secret" not in str(caught.value)


def test_is_trading_day_provider_failure_is_redacted_and_stable() -> None:
    class BrokenCalendar:
        def is_session(self, value: str) -> bool:
            raise RuntimeError("provider secret: session lookup")

    adapter = object.__new__(ExchangeCalendarAdapter)
    adapter._calendar = BrokenCalendar()

    with pytest.raises(RuntimeError) as caught:
        adapter.is_trading_day(date(2026, 8, 19))

    assert str(caught.value) == PUBLIC_ERROR
    assert "provider secret" not in str(caught.value)


def test_session_open_close_provider_failure_is_redacted_and_stable() -> None:
    class BrokenSchedule:
        @property
        def loc(self) -> object:
            raise RuntimeError("provider secret: schedule lookup")

    adapter = object.__new__(ExchangeCalendarAdapter)
    adapter._calendar = SimpleNamespace(schedule=BrokenSchedule())

    with pytest.raises(RuntimeError) as caught:
        adapter.session_open_close(date(2026, 8, 19))

    assert str(caught.value) == PUBLIC_ERROR
    assert "provider secret" not in str(caught.value)


def test_session_values_are_converted_to_timezone_aware_utc() -> None:
    class ProviderTimestamp:
        def __init__(self, value: datetime) -> None:
            self.value = value

        def to_pydatetime(self) -> datetime:
            return self.value

    class Schedule:
        loc = {
            "2026-03-09": {
                    "open": ProviderTimestamp(
                        datetime(2026, 3, 9, 9, 30, tzinfo=ZoneInfo("America/New_York"))
                    ),
                    "close": ProviderTimestamp(
                        datetime(2026, 3, 9, 16, 0, tzinfo=ZoneInfo("America/New_York"))
                    ),
            }
        }

    adapter = object.__new__(ExchangeCalendarAdapter)
    adapter._calendar = SimpleNamespace(schedule=Schedule())

    opened, closed = adapter.session_open_close(date(2026, 3, 9))

    assert opened == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)
    assert closed == datetime(2026, 3, 9, 20, 0, tzinfo=UTC)
    assert opened.tzinfo is UTC
    assert closed.tzinfo is UTC


def test_session_values_reject_naive_provider_timestamps() -> None:
    class ProviderTimestamp:
        def __init__(self, value: datetime) -> None:
            self.value = value

        def to_pydatetime(self) -> datetime:
            return self.value

    class Schedule:
        loc = {
            "2026-03-09": {
                "open": ProviderTimestamp(datetime(2026, 3, 9, 9, 30)),
                "close": ProviderTimestamp(
                    datetime(2026, 3, 9, 16, 0, tzinfo=ZoneInfo("America/New_York"))
                ),
            }
        }

    adapter = object.__new__(ExchangeCalendarAdapter)
    adapter._calendar = SimpleNamespace(schedule=Schedule())

    with pytest.raises(RuntimeError) as caught:
        adapter.session_open_close(date(2026, 3, 9))

    assert str(caught.value) == PUBLIC_ERROR
