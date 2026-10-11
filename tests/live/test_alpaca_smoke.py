from datetime import UTC, datetime

import pytest

from finance_research_agent.adapters.exchange_calendar import ExchangeCalendarAdapter
from finance_research_agent.settings import Settings
from tests.support.live_smoke import create_live_smoke_provider, premarket_smoke_window

pytestmark = pytest.mark.live


def test_alpaca_market_data_live_smoke() -> None:
    settings = Settings()
    if settings.alpaca_api_key is None or settings.alpaca_api_secret is None:
        pytest.skip("ALPACA_API_KEY and ALPACA_API_SECRET are not configured")
    now = datetime.now(UTC)
    calendar = ExchangeCalendarAdapter()
    window = premarket_smoke_window(now, calendar)
    if window is None:
        pytest.skip("Alpaca live smoke requires an XNYS trading day during 04:00-09:30 ET")
    _, completed_sessions = window
    provider = create_live_smoke_provider(settings, now)
    assert provider.readiness().available is True

    identity = provider.fetch_instruments(["SPY"])["SPY"]
    assert identity.symbol == "SPY"
    bars = provider.fetch_daily_bars(
        ["SPY"],
        start=completed_sessions[0],
        end=completed_sessions[-1],
        expected_sessions=completed_sessions,
        completed_through_session=completed_sessions[-1],
        evidence_cutoff_at=now,
        instrument_identities={"SPY": identity},
    )["SPY"]
    assert bars
    observation = provider.fetch_premarket_observations(
        ["SPY"],
        now,
        instrument_identities={"SPY": identity},
    )["SPY"]
    assert observation.provider == "alpaca"
    assert observation.feed == "iex"
    assert observation.coverage.value == "single_exchange"
    assert observation.session.value == "PRE_MARKET"
    assert observation.observed_at <= observation.retrieved_at <= now
    assert settings.alpaca_api_key.get_secret_value() not in repr(observation)
    assert settings.alpaca_api_secret.get_secret_value() not in repr(observation)
