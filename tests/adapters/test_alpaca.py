import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from finance_research_agent.adapters.alpaca import AlpacaMarketDataProvider
from finance_research_agent.adapters.http_client import SafeHttpClient
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.market_data.historical import BarAdjustment, MarketDataFeed
from finance_research_agent.settings import Settings

FIXTURES = Path("tests/fixtures/alpaca")
AS_OF = datetime(2026, 8, 19, 12, 58, tzinfo=UTC)


def _provider(
    payload_name: str,
    *,
    path: str = "/v2/stocks/trades/latest",
    status_code: int = 200,
    payload: object | None = None,
    clock: datetime = AS_OF,
    require_auth: bool = False,
) -> AlpacaMarketDataProvider:
    response_payload = (
        json.loads((FIXTURES / payload_name).read_text(encoding="utf-8"))
        if payload is None
        else payload
    )

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "data.alpaca.markets"
        assert request.url.path == path
        if require_auth:
            assert request.headers["APCA-API-KEY-ID"] == "fixture-key"
            assert request.headers["APCA-API-SECRET-KEY"] == "fixture-secret"
        return httpx.Response(
            status_code,
            headers={"content-type": "application/json"},
            json=response_payload,
        )

    policy = SourcePolicy(
        version="1",
        allowed_adapters=("alpaca",),
        allowed_https_domains=("data.alpaca.markets",),
        allowed_hosts_by_adapter=FrozenMap({"alpaca": ("data.alpaca.markets",)}),
        freshness_by_data_type=FrozenMap({"market_data": 60}),
        cache_retention_seconds=60,
        request_deadline_seconds=Decimal("10"),
        retry_attempts=0,
        retry_backoff_seconds=Decimal("0.1"),
        retry_jitter_seconds=Decimal("0.1"),
        per_run_request_budgets=FrozenMap({"market_data": 20}),
        maximum_response_bytes=1_000_000,
        allowed_content_types=("application/json",),
        excerpt_limits=FrozenMap({"application/json": 4096}),
    )
    client = SafeHttpClient(
        policy,
        transport=httpx.MockTransport(respond),
        resolver=lambda host, port: ("93.184.216.34",),
        clock=lambda: clock,
    )
    settings = Settings(
        data_dir=Path("data"),
        alpaca_api_key="fixture-key",
        alpaca_api_secret="fixture-secret",
    )
    return AlpacaMarketDataProvider(settings, client, clock=lambda: clock)


@pytest.fixture
def alpaca_provider() -> AlpacaMarketDataProvider:
    return _provider("premarket-iex.json")


@pytest.fixture
def alpaca_schema_drift_provider() -> AlpacaMarketDataProvider:
    return _provider("schema-drift.json")


def test_premarket_quote_preserves_iex_provenance(
    alpaca_provider: AlpacaMarketDataProvider,
) -> None:
    result = alpaca_provider.fetch_premarket_observations(["AAPL"], AS_OF)
    quote = result["AAPL"]
    assert quote.value == Decimal("192.34")
    assert quote.provider == "alpaca"
    assert quote.feed == "iex"
    assert quote.coverage.value == "single_exchange"
    assert quote.session.value == "PRE_MARKET"
    assert quote.observed_at == AS_OF
    assert quote.evidence_id


def test_schema_drift_returns_failure_without_guessing(
    alpaca_schema_drift_provider: AlpacaMarketDataProvider,
) -> None:
    result = alpaca_schema_drift_provider.fetch_premarket_observations(["AAPL"], AS_OF)
    assert result["AAPL"].error_code == "PROVIDER_SCHEMA_DRIFT"
    assert result["AAPL"].retryable is False


def test_readiness_exposes_configuration_without_secret_content() -> None:
    provider = _provider("premarket-iex.json")
    readiness = provider.readiness()
    assert readiness.provider == "alpaca"
    assert readiness.configured is True
    assert readiness.available is True
    assert "fixture-key" not in repr(readiness)


def test_instruments_are_normalized_to_provider_neutral_identity() -> None:
    provider = _provider("instruments.json", path="/v2/assets")
    result = provider.fetch_instruments(["AAPL"])
    identity = result["AAPL"]
    assert identity.symbol == "AAPL"
    assert identity.instrument_id == "AAPL"
    assert identity.instrument_type == "COMMON_STOCK"
    assert identity.primary_exchange == "NASDAQ"
    assert identity.currency == "USD"
    assert identity.is_active is True


def test_daily_bars_use_the_existing_completed_history_normalizer() -> None:
    provider = _provider(
        "daily-bars.json",
        path="/v2/stocks/bars",
        clock=datetime(2026, 8, 20, tzinfo=UTC),
    )
    result = provider.fetch_daily_bars(
        ["AAPL"],
        datetime(2026, 8, 18, tzinfo=UTC).date(),
        datetime(2026, 8, 19, tzinfo=UTC).date(),
    )
    bars = result["AAPL"]
    assert [bar.session_date.isoformat() for bar in bars] == ["2026-08-18", "2026-08-19"]
    assert bars[0].close == Decimal("192.0")
    assert bars[1].close == Decimal("193.0")


def test_daily_bar_request_preserves_explicit_feed_and_adjustment() -> None:
    provider = _provider(
        "daily-bars.json",
        path="/v2/stocks/bars",
        clock=datetime(2026, 8, 20, tzinfo=UTC),
    )
    result = provider.fetch_daily_bars(
        ["AAPL"],
        datetime(2026, 8, 18, tzinfo=UTC).date(),
        datetime(2026, 8, 19, tzinfo=UTC).date(),
        feed=MarketDataFeed.SIP,
        adjustment=BarAdjustment.SPLIT,
    )
    assert result["AAPL"][0].close == Decimal("192.0")


def test_daily_bar_symbol_absence_is_scoped_without_fabricating_a_bar() -> None:
    provider = _provider(
        "daily-bars.json",
        path="/v2/stocks/bars",
        clock=datetime(2026, 8, 20, tzinfo=UTC),
    )
    result = provider.fetch_daily_bars(
        ["AAPL", "MSFT"],
        datetime(2026, 8, 18, tzinfo=UTC).date(),
        datetime(2026, 8, 19, tzinfo=UTC).date(),
    )
    assert result["MSFT"].symbol == "MSFT"
    assert result["MSFT"].error_code == "PROVIDER_UNAVAILABLE"
    assert result["MSFT"].scope == "symbol"


def test_exhausted_rate_limit_is_a_global_typed_failure() -> None:
    provider = _provider("rate-limit.json", status_code=429)
    result = provider.fetch_premarket_observations(["AAPL", "MSFT"], AS_OF)
    assert result["AAPL"].symbol is None
    assert result["MSFT"].error_code == "PROVIDER_UNAVAILABLE"
    assert result["MSFT"].retryable is False
    assert result["AAPL"].scope == "global"


def test_invalid_symbol_is_rejected_without_uppercase_normalization() -> None:
    provider = _provider("premarket-iex.json")
    result = provider.fetch_premarket_observations(["aapl"], AS_OF)
    assert result["aapl"].error_code == "INVALID_REQUEST"
    assert result["aapl"].symbol is None


def test_invalid_premarket_timestamp_is_not_rewritten_into_success() -> None:
    provider = _provider(
        "premarket-iex.json",
        payload={
            "trades": {
                "AAPL": {
                    "p": 192.34,
                    "s": 10,
                    "t": "2026-08-19T12:58:00+08:00",
                    "x": "V",
                }
            }
        },
    )
    result = provider.fetch_premarket_observations(["AAPL"], AS_OF)
    assert result["AAPL"].error_code == "PROVIDER_SCHEMA_DRIFT"


def test_configured_requests_send_only_market_data_credentials() -> None:
    provider = _provider("premarket-iex.json", require_auth=True)
    result = provider.fetch_premarket_observations(["AAPL"], AS_OF)
    assert result["AAPL"].provider == "alpaca"
