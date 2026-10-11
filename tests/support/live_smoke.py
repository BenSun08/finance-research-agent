"""Bounded, testable inputs and dependency construction for opt-in live smoke."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from finance_research_agent.adapters.alpaca import AlpacaMarketDataProvider
from finance_research_agent.adapters.http_client import SafeHttpClient
from finance_research_agent.application.market_collection import resolve_completed_session_window
from finance_research_agent.application.ports import TradingCalendar
from finance_research_agent.domain.enums import SourceRole
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.settings import Settings

_NEW_YORK = ZoneInfo("America/New_York")
_PREMARKET_START = time(4)


def premarket_smoke_window(
    now_utc: datetime,
    calendar: TradingCalendar,
) -> tuple[date, tuple[date, ...]] | None:
    """Return today and its last two completed sessions only during XNYS premarket."""
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("live smoke time must be timezone-aware")
    instant = now_utc.astimezone(UTC)
    local_now = instant.astimezone(_NEW_YORK)
    market_date = local_now.date()
    if not calendar.is_trading_day(market_date) or local_now.time().replace(tzinfo=None) < (
        _PREMARKET_START
    ):
        return None

    session_open, _ = calendar.session_open_close(market_date)
    if session_open.tzinfo is None or session_open.utcoffset() is None:
        raise ValueError("calendar session open must be timezone-aware")
    if instant >= session_open.astimezone(UTC):
        return None

    sessions = resolve_completed_session_window(calendar, before=market_date, session_count=2)
    return market_date, sessions


def build_live_smoke_source_policy() -> SourcePolicy:
    """Build a validated, read-only Alpaca market-data policy for the smoke."""
    return SourcePolicy(
        version="1",
        quality_source_roles=(SourceRole.MARKET_DATA, SourceRole.MARKET_CALENDAR),
        allowed_adapters=("alpaca",),
        allowed_https_domains=("api.alpaca.markets", "data.alpaca.markets"),
        allowed_hosts_by_adapter=FrozenMap(
            {"alpaca": ("api.alpaca.markets", "data.alpaca.markets")}
        ),
        freshness_by_data_type=FrozenMap({"market_data": 300}),
        cache_retention_seconds=300,
        request_deadline_seconds=Decimal("20"),
        retry_attempts=2,
        retry_backoff_seconds=Decimal("0.5"),
        retry_jitter_seconds=Decimal("0.1"),
        per_run_request_budgets=FrozenMap({"market_data": 10}),
        maximum_response_bytes=1_000_000,
        allowed_content_types=("application/json",),
        excerpt_limits=FrozenMap({"application/json": 4096}),
    )


def create_live_smoke_provider(
    settings: Settings,
    now_utc: datetime,
    *,
    policy_factory: Callable[[], SourcePolicy] = build_live_smoke_source_policy,
    client_factory: Callable[[SourcePolicy], SafeHttpClient] | None = None,
) -> AlpacaMarketDataProvider:
    """Validate policy before constructing the HTTP client or provider."""
    policy = policy_factory()
    http_client = (SafeHttpClient if client_factory is None else client_factory)(policy)
    return AlpacaMarketDataProvider(settings, http_client, clock=lambda: now_utc)
