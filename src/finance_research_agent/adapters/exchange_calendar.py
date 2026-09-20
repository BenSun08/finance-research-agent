"""XNYS exchange-calendar adapter."""

from datetime import UTC, date, datetime

from finance_research_agent.domain.errors import ErrorCode


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("provider timestamp must be timezone-aware")
    return value.astimezone(UTC)


class ExchangeCalendarAdapter:
    """Expose XNYS sessions through the provider-neutral calendar protocol."""

    def __init__(self, calendar_name: str = "XNYS") -> None:
        try:
            import exchange_calendars as xcals  # type: ignore[import-untyped]

            self._calendar = xcals.get_calendar(calendar_name)
        except Exception as error:
            raise RuntimeError(
                f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: market calendar unavailable"
            ) from error

    def is_trading_day(self, market_date: date) -> bool:
        try:
            return bool(self._calendar.is_session(market_date.isoformat()))
        except Exception as error:
            raise RuntimeError(
                f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: market calendar unavailable"
            ) from error

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        try:
            session = self._calendar.schedule.loc[market_date.isoformat()]
            return (
                _as_utc(session["open"].to_pydatetime()),
                _as_utc(session["close"].to_pydatetime()),
            )
        except Exception as error:
            raise RuntimeError(
                f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: market calendar unavailable"
            ) from error
