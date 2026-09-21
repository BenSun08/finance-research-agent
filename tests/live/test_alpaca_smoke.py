from datetime import UTC, date, datetime

import pytest

from finance_research_agent.adapters.alpaca import AlpacaMarketDataProvider
from finance_research_agent.adapters.http_client import SafeHttpClient
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.settings import Settings

pytestmark = pytest.mark.live


def test_alpaca_market_data_live_smoke() -> None:
    settings = Settings()
    if settings.alpaca_api_key is None or settings.alpaca_api_secret is None:
        pytest.skip("ALPACA_API_KEY and ALPACA_API_SECRET are not configured")
    policy = SourcePolicy(
        version="live-smoke",
        allowed_adapters=("alpaca",),
        allowed_https_domains=("api.alpaca.markets", "data.alpaca.markets"),
        allowed_hosts_by_adapter=FrozenMap(
            {"alpaca": ("api.alpaca.markets", "data.alpaca.markets")}
        ),
        freshness_by_data_type=FrozenMap({"market_data": 300}),
        cache_retention_seconds=300,
        request_deadline_seconds="20",
        retry_attempts=2,
        retry_backoff_seconds="0.5",
        retry_jitter_seconds="0.1",
        per_run_request_budgets=FrozenMap({"market_data": 10}),
        maximum_response_bytes=1_000_000,
        allowed_content_types=("application/json",),
        excerpt_limits=FrozenMap({"application/json": 4096}),
    )
    now = datetime.now(UTC)
    client = SafeHttpClient(policy)
    provider = AlpacaMarketDataProvider(settings, client, clock=lambda: now)
    assert provider.readiness().available is True

    identity = provider.fetch_instruments(["SPY"])["SPY"]
    assert identity.symbol == "SPY"
    completed_date = date(2026, 8, 18)
    expected_sessions = (completed_date, date(2026, 8, 19))
    bars = provider.fetch_daily_bars(
        ["SPY"],
        start=expected_sessions[0],
        end=expected_sessions[-1],
        expected_sessions=expected_sessions,
        completed_through_session=expected_sessions[-1],
    )["SPY"]
    assert bars
    observation = provider.fetch_premarket_observations(["SPY"], now)["SPY"]
    assert observation.provider == "alpaca"
    assert observation.feed == "iex"
    assert observation.coverage.value == "single_exchange"
    assert observation.session.value == "PRE_MARKET"
    assert observation.observed_at <= observation.retrieved_at <= now
    assert settings.alpaca_api_key.get_secret_value() not in repr(observation)
    assert settings.alpaca_api_secret.get_secret_value() not in repr(observation)
