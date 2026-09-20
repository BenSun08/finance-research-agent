"""XNYS exchange-calendar adapter."""

from datetime import UTC, date, datetime

from finance_research_agent.domain.errors import ErrorCode


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
                session["open"].to_pydatetime().astimezone(UTC),
                session["close"].to_pydatetime().astimezone(UTC),
            )
        except Exception as error:
            raise RuntimeError(
                f"{ErrorCode.MARKET_CALENDAR_UNAVAILABLE}: market calendar unavailable"
            ) from error
