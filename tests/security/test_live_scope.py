import shlex
import tomllib
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from finance_research_agent.adapters.alpaca import AlpacaMarketDataProvider
from finance_research_agent.settings import Settings
from tests.support.live_smoke import (
    build_live_smoke_source_policy,
    create_live_smoke_provider,
    premarket_smoke_window,
)

ROOT = Path(__file__).resolve().parents[2]
NEW_YORK = ZoneInfo("America/New_York")


class WeekdayCalendar:
    def is_trading_day(self, market_date: date) -> bool:
        return market_date.weekday() < 5

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        opened = datetime.combine(market_date, time(9, 30), NEW_YORK).astimezone(UTC)
        closed = datetime.combine(market_date, time(16), NEW_YORK).astimezone(UTC)
        return opened, closed


def test_default_pytest_options_exclude_live_marker() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = shlex.split(pyproject["tool"]["pytest"]["ini_options"]["addopts"])

    marker_position = addopts.index("-m")
    assert addopts[marker_position + 1] == "not live"


def test_ci_keeps_the_unqualified_pytest_command() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert 'python -m pytest -m "not live"' in workflow
    assert "tests/live" not in workflow


def test_live_smoke_window_uses_calendar_completed_sessions_across_weekend() -> None:
    now = datetime(2026, 3, 9, 8, 30, tzinfo=NEW_YORK).astimezone(UTC)

    window = premarket_smoke_window(now, WeekdayCalendar())

    assert window == (date(2026, 3, 9), (date(2026, 3, 5), date(2026, 3, 6)))


@pytest.mark.parametrize(
    "now",
    (
        datetime(2026, 3, 9, 3, 59, tzinfo=NEW_YORK).astimezone(UTC),
        datetime(2026, 3, 9, 9, 30, tzinfo=NEW_YORK).astimezone(UTC),
        datetime(2026, 3, 7, 8, 30, tzinfo=NEW_YORK).astimezone(UTC),
    ),
)
def test_live_smoke_window_rejects_out_of_window_or_non_session_times(now) -> None:
    assert premarket_smoke_window(now, WeekdayCalendar()) is None


def test_live_smoke_window_rejects_a_naive_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        premarket_smoke_window(datetime(2026, 3, 9, 8, 30), WeekdayCalendar())


def test_live_smoke_window_rejects_a_naive_calendar_open() -> None:
    class NaiveOpenCalendar(WeekdayCalendar):
        def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
            return datetime(2026, 3, 9, 13, 30), datetime(2026, 3, 9, 20)

    now = datetime(2026, 3, 9, 8, 30, tzinfo=NEW_YORK).astimezone(UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        premarket_smoke_window(now, NaiveOpenCalendar())


def test_live_smoke_validates_source_policy_before_constructing_network_client() -> None:
    client_calls = []

    def invalid_policy():
        raise ValueError("invalid source policy")

    def client_factory(policy):
        client_calls.append(policy)
        raise AssertionError("HTTP client must not be constructed")

    with pytest.raises(ValueError, match="invalid source policy"):
        create_live_smoke_provider(
            settings=None,
            now_utc=datetime(2026, 3, 9, 12, 30, tzinfo=UTC),
            policy_factory=invalid_policy,
            client_factory=client_factory,
        )

    assert client_calls == []


def test_live_smoke_provider_can_be_constructed_without_network_access(tmp_path) -> None:
    provider = create_live_smoke_provider(
        settings=Settings(data_dir=tmp_path),
        now_utc=datetime(2026, 3, 9, 12, 30, tzinfo=UTC),
    )

    assert isinstance(provider, AlpacaMarketDataProvider)


def test_live_smoke_policy_is_validated_and_uses_decimal_timing() -> None:
    policy = build_live_smoke_source_policy()

    assert policy.version == "1"
    assert type(policy.request_deadline_seconds) is Decimal
    assert policy.allowed_adapters == ("alpaca",)
