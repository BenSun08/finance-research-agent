import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import httpx
import pytest

from finance_research_agent.adapters.alpaca import AlpacaMarketDataProvider
from finance_research_agent.adapters.http_client import (
    RequestDeadlineExceeded,
    RequestRejected,
    RequestTransportUnavailable,
    SafeHttpClient,
)
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    InstrumentIdentity,
    MarketSnapshot,
    PriceObservation,
    SourceObservation,
)
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.market_data.historical import BarAdjustment, MarketDataFeed
from finance_research_agent.settings import Settings

FIXTURES = Path("tests/fixtures/alpaca")
AS_OF = datetime(2026, 8, 19, 12, 58, tzinfo=UTC)
EXPECTED_SESSIONS = (date(2026, 8, 18), date(2026, 8, 19))
DAILY_RETRIEVED_AT = datetime(2026, 8, 20, tzinfo=UTC)
DAILY_EVIDENCE_CUTOFF = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _fixture_identity(symbol: str) -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id=f"fixture-{symbol.lower()}",
        symbol=symbol,
        name=symbol,
        instrument_type="UNKNOWN",
        primary_exchange=None,
        listing_country=None,
        currency="USD",
        is_active=None,
        is_leveraged=None,
        is_inverse=None,
        is_otc=None,
    )


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
        expected_host = "api.alpaca.markets" if path == "/v2/assets" else "data.alpaca.markets"
        assert request.url.host == expected_host
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
        allowed_https_domains=("api.alpaca.markets", "data.alpaca.markets"),
        allowed_hosts_by_adapter=FrozenMap(
            {"alpaca": ("api.alpaca.markets", "data.alpaca.markets")}
        ),
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
    return AlpacaMarketDataProvider(
        settings,
        client,
        clock=lambda: clock,
        identity_cache={symbol: _fixture_identity(symbol) for symbol in ("AAPL", "MSFT")},
    )


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


def test_quote_shaped_latest_observation_validates_both_iex_venues() -> None:
    provider = _provider(
        "premarket-iex.json",
        payload={
            "quotes": {
                "AAPL": {
                    "ap": 192.34,
                    "bp": 192.33,
                    "t": "2026-08-19T12:58:00Z",
                    "ax": "V",
                    "bx": "V",
                }
            }
        },
    )

    result = provider.fetch_premarket_observations(["AAPL"], AS_OF)

    observation = result["AAPL"]
    assert observation.value == Decimal("192.34")
    assert observation.feed == "iex"
    assert observation.coverage.value == "single_exchange"
    assert observation.session.value == "PRE_MARKET"


@pytest.mark.parametrize(
    "venue_fields",
    ({}, {"ax": "V", "bx": "NASDAQ"}),
)
def test_quote_shaped_latest_observation_rejects_missing_or_non_iex_venue(
    venue_fields: dict[str, str],
) -> None:
    provider = _provider(
        "premarket-iex.json",
        payload={
            "quotes": {
                "AAPL": {
                    "ap": 192.34,
                    "bp": 192.33,
                    "t": "2026-08-19T12:58:00Z",
                    **venue_fields,
                }
            }
        },
    )

    result = provider.fetch_premarket_observations(["AAPL"], AS_OF)

    assert result["AAPL"].error_code == "PROVIDER_SCHEMA_DRIFT"
    assert result["AAPL"].retryable is False


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
    assert identity.instrument_id == "aapl-id"
    assert identity.instrument_type == "UNKNOWN"
    assert identity.primary_exchange == "NASDAQ"
    assert identity.currency == "USD"
    assert identity.is_active is True


def test_identity_and_current_observation_compose_into_market_snapshot() -> None:
    identity_result = _provider("instruments.json", path="/v2/assets").fetch_instruments(["AAPL"])
    identity = identity_result["AAPL"]
    assert isinstance(identity, InstrumentIdentity)

    observation_result = _provider("premarket-iex.json").fetch_premarket_observations(
        ["AAPL"],
        AS_OF,
        instrument_identities={"AAPL": identity},
    )
    observation = observation_result["AAPL"]
    assert observation.instrument_id == identity.instrument_id
    assert isinstance(observation, PriceObservation)

    source = SourceObservation(
        observation_id=observation.evidence_id,
        provider=observation.provider,
        source_url=None,
        source_hash_sha256="a" * 64,
        observed_at=observation.observed_at,
        retrieved_at=observation.retrieved_at,
        content_type="application/vnd.finance-research-agent.price+json",
        excerpt="",
        persistence_allowed=True,
        quality_flags=observation.quality_flags,
    )
    snapshot = MarketSnapshot(
        instrument=identity,
        latest_price=observation,
        completed_daily_bars=(),
        current_session_bars=(),
        source_observations=(source,),
        quality_flags=observation.quality_flags,
    )

    assert snapshot.latest_price is not None
    assert snapshot.latest_price.instrument_id == identity.instrument_id


def test_current_observation_fails_closed_without_identity_resolution() -> None:
    provider = _provider("premarket-iex.json")

    result = provider.fetch_premarket_observations(
        ["AAPL"],
        AS_OF,
        instrument_identities={},
    )

    assert result["AAPL"].symbol == "AAPL"
    assert result["AAPL"].error_code == "INVALID_REQUEST"
    assert result["AAPL"].retryable is False


def test_daily_bars_preserve_provenance_and_provider_identity() -> None:
    provider = _provider(
        "daily-bars.json",
        path="/v2/stocks/bars",
        clock=DAILY_RETRIEVED_AT,
    )

    result = provider.fetch_daily_bars(
        ["AAPL"],
        date(2026, 8, 18),
        date(2026, 8, 19),
        expected_sessions=EXPECTED_SESSIONS,
        completed_through_session=EXPECTED_SESSIONS[-1],
        evidence_cutoff_at=DAILY_EVIDENCE_CUTOFF,
        instrument_identities={"AAPL": _fixture_identity("AAPL")},
    )

    bars = result["AAPL"]
    assert isinstance(bars, tuple)
    assert all(isinstance(bar, CompletedDailyBar) for bar in bars)
    assert [bar.instrument_id for bar in bars] == ["fixture-aapl", "fixture-aapl"]
    assert bars[0].provider == "alpaca"
    assert bars[0].feed == "iex"
    assert bars[0].coverage.value == "single_exchange"
    assert bars[0].adjustment == "all"
    assert bars[0].source_timestamp == datetime(2026, 8, 18, 20, tzinfo=UTC)
    assert bars[0].retrieved_at == DAILY_RETRIEVED_AT
    assert bars[0].evidence_cutoff_at == DAILY_EVIDENCE_CUTOFF
    assert bars[0].evidence_id
    assert bars[0].quality_flags


def test_daily_bars_fail_closed_without_explicit_evidence_cutoff() -> None:
    provider = _provider(
        "daily-bars.json",
        path="/v2/stocks/bars",
        clock=DAILY_RETRIEVED_AT,
    )

    result = provider.fetch_daily_bars(
        ["AAPL"],
        date(2026, 8, 18),
        date(2026, 8, 19),
        expected_sessions=EXPECTED_SESSIONS,
        completed_through_session=EXPECTED_SESSIONS[-1],
        instrument_identities={"AAPL": _fixture_identity("AAPL")},
    )

    assert result["AAPL"].error_code == "INVALID_REQUEST"
    assert result["AAPL"].retryable is False


def test_daily_bars_fail_closed_without_identity_resolution() -> None:
    provider = _provider(
        "daily-bars.json",
        path="/v2/stocks/bars",
        clock=DAILY_RETRIEVED_AT,
    )

    result = provider.fetch_daily_bars(
        ["AAPL"],
        date(2026, 8, 18),
        date(2026, 8, 19),
        expected_sessions=EXPECTED_SESSIONS,
        completed_through_session=EXPECTED_SESSIONS[-1],
        evidence_cutoff_at=DAILY_EVIDENCE_CUTOFF,
        instrument_identities={},
    )

    assert result["AAPL"].error_code == "INVALID_REQUEST"
    assert result["AAPL"].retryable is False


def test_premarket_fails_when_retrieval_is_after_evidence_cutoff() -> None:
    provider = _provider(
        "premarket-iex.json",
        clock=AS_OF + timedelta(minutes=1),
    )

    result = provider.fetch_premarket_observations(["AAPL"], AS_OF)

    assert result["AAPL"].error_code == "EVIDENCE_CUTOFF_VIOLATION"
    assert result["AAPL"].retryable is False


def test_empty_provider_asset_id_is_typed_schema_failure() -> None:
    provider = _provider(
        "instruments.json",
        path="/v2/assets",
        payload=[
            {
                "id": "",
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "exchange": "NASDAQ",
                "class": "us_equity",
                "status": "active",
                "tradable": True,
                "fractionable": True,
            }
        ],
    )

    result = provider.fetch_instruments(["AAPL"])

    assert result["AAPL"].error_code == "PROVIDER_SCHEMA_DRIFT"
    assert result["AAPL"].retryable is False
    assert result["AAPL"].scope == "symbol"


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
        expected_sessions=EXPECTED_SESSIONS,
        completed_through_session=EXPECTED_SESSIONS[-1],
        evidence_cutoff_at=DAILY_EVIDENCE_CUTOFF,
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
        expected_sessions=EXPECTED_SESSIONS,
        completed_through_session=EXPECTED_SESSIONS[-1],
        evidence_cutoff_at=DAILY_EVIDENCE_CUTOFF,
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
        expected_sessions=EXPECTED_SESSIONS,
        completed_through_session=EXPECTED_SESSIONS[-1],
        evidence_cutoff_at=DAILY_EVIDENCE_CUTOFF,
    )
    assert result["MSFT"].symbol == "MSFT"
    assert result["MSFT"].error_code == "PROVIDER_NO_DATA"
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


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("x", "NASDAQ"),
        ("t", "2026-08-19T14:00:00Z"),
        ("t", "2026-08-19T13:00:00Z"),
    ),
)
def test_premarket_rejects_wrong_venue_outside_window_and_future_trade(
    field: str, value: str
) -> None:
    payload = {
        "trades": {
            "AAPL": {
                "p": 192.34,
                "s": 10,
                "t": "2026-08-19T12:58:00Z",
                "x": "V",
            }
        }
    }
    payload["trades"]["AAPL"][field] = value
    provider = _provider("premarket-iex.json", payload=payload)

    result = provider.fetch_premarket_observations(["AAPL"], AS_OF)

    assert result["AAPL"].error_code == "PROVIDER_SCHEMA_DRIFT"
    assert result["AAPL"].retryable is False


def test_instrument_catalog_selects_requested_assets_and_reports_absence_per_symbol() -> None:
    provider = _provider(
        "instruments.json",
        path="/v2/assets",
        payload=[
            {
                "id": "aapl-id",
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "exchange": "NASDAQ",
                "class": "us_equity",
                "status": "active",
                "tradable": True,
                "fractionable": True,
            },
            {
                "id": "msft-id",
                "symbol": "MSFT",
                "name": "Microsoft Corporation",
                "exchange": "NASDAQ",
                "class": "us_equity",
                "status": "active",
                "tradable": True,
                "fractionable": True,
            },
        ],
    )

    result = provider.fetch_instruments(["AAPL", "SPY"])

    assert result["AAPL"].instrument_id == "aapl-id"
    assert result["SPY"].error_code == "UNSUPPORTED_INSTRUMENT"
    assert result["SPY"].scope == "symbol"


def test_instrument_classification_requires_explicit_provider_evidence() -> None:
    provider = _provider(
        "instruments-classifications.json",
        path="/v2/assets",
    )

    result = provider.fetch_instruments(["AAPL", "SPY", "QQQ"])

    assert result["AAPL"].instrument_id == "stock-id"
    assert result["AAPL"].instrument_type == "COMMON_STOCK"
    assert result["SPY"].instrument_id == "etf-id"
    assert result["SPY"].instrument_type == "ETF"
    assert result["QQQ"].instrument_id == "unknown-id"
    assert result["QQQ"].instrument_type == "UNKNOWN"


def test_unknown_payload_shape_is_rejected_without_guessing() -> None:
    provider = _provider(
        "premarket-iex.json",
        payload={
            "trades": {
                "AAPL": {
                    "p": 192.34,
                    "s": 10,
                    "t": "2026-08-19T12:58:00Z",
                    "x": "V",
                    "unexpected": "schema drift",
                }
            }
        },
    )

    result = provider.fetch_premarket_observations(["AAPL"], AS_OF)

    assert result["AAPL"].error_code == "PROVIDER_SCHEMA_DRIFT"


def test_daily_bars_require_caller_supplied_calendar_sessions() -> None:
    provider = _provider(
        "daily-bars.json",
        path="/v2/stocks/bars",
        clock=datetime(2026, 8, 20, tzinfo=UTC),
    )

    result = provider.fetch_daily_bars(
        ["AAPL"],
        date(2026, 8, 18),
        date(2026, 8, 19),
    )

    assert result["AAPL"].error_code == "INVALID_REQUEST"
    assert result["AAPL"].retryable is False


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    (
        (
            {"bars": {}, "next_page_token": None},
            "PROVIDER_NO_DATA",
        ),
        (
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-08-19T20:00:00Z",
                            "o": 191.0,
                            "h": 194.0,
                            "l": 190.0,
                            "c": 193.0,
                            "v": 1100,
                        }
                    ]
                },
                "next_page_token": None,
            },
            "PROVIDER_MISSING_SESSION",
        ),
        (
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-08-18T20:00:00Z",
                            "o": 190.0,
                            "h": 193.0,
                            "l": 189.0,
                            "c": 192.0,
                            "v": 1000,
                        }
                    ]
                },
                "next_page_token": None,
            },
            "PROVIDER_STALE",
        ),
        (
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-08-18T20:00:00Z",
                            "o": 190.0,
                            "h": 193.0,
                            "l": 189.0,
                            "c": 192.0,
                            "v": 1000,
                        },
                        {
                            "t": "2026-08-18T20:00:00Z",
                            "o": 190.0,
                            "h": 193.0,
                            "l": 189.0,
                            "c": 191.0,
                            "v": 1000,
                        },
                    ]
                },
                "next_page_token": None,
            },
            "PROVIDER_DUPLICATE_CONFLICT",
        ),
        (
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-08-18T20:00:00Z",
                            "o": 190.0,
                            "h": 193.0,
                            "l": 189.0,
                            "c": 192.0,
                            "v": -1,
                        },
                        {
                            "t": "2026-08-19T20:00:00Z",
                            "o": 191.0,
                            "h": 194.0,
                            "l": 190.0,
                            "c": 193.0,
                            "v": 1100,
                        },
                    ]
                },
                "next_page_token": None,
            },
            "PROVIDER_MALFORMED_BAR",
        ),
        (
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-08-20T20:00:00Z",
                            "o": 190.0,
                            "h": 193.0,
                            "l": 189.0,
                            "c": 192.0,
                            "v": 1000,
                        }
                    ]
                },
                "next_page_token": None,
            },
            "PROVIDER_FUTURE_OR_INCOMPLETE_BAR",
        ),
    ),
)
def test_historical_normalizer_reason_maps_to_distinct_provider_failure(
    payload: object, expected_code: str
) -> None:
    provider = _provider(
        "daily-bars.json",
        path="/v2/stocks/bars",
        payload=payload,
        clock=datetime(2026, 8, 20, tzinfo=UTC),
    )

    result = provider.fetch_daily_bars(
        ["AAPL"],
        date(2026, 8, 18),
        date(2026, 8, 19),
        expected_sessions=EXPECTED_SESSIONS,
        completed_through_session=EXPECTED_SESSIONS[-1],
        evidence_cutoff_at=DAILY_EVIDENCE_CUTOFF,
    )

    assert result["AAPL"].error_code == expected_code
    assert result["AAPL"].retryable is False
    assert result["AAPL"].message.startswith("historical data unavailable:")


@pytest.mark.parametrize(
    ("exception", "retryable"),
    (
        (RequestRejected("policy"), False),
        (RequestDeadlineExceeded("deadline"), False),
        (RequestTransportUnavailable("transport"), True),
    ),
)
def test_request_rejection_retryability_uses_typed_boundary_signal(
    exception: RequestRejected, retryable: bool
) -> None:
    class RejectingClient:
        def request(self, *args: object, **kwargs: object) -> object:
            raise exception

    settings = Settings(
        data_dir=Path("data"),
        alpaca_api_key="fixture-key",
        alpaca_api_secret="fixture-secret",
    )
    provider = AlpacaMarketDataProvider(settings, cast(SafeHttpClient, RejectingClient()))

    result = provider.fetch_premarket_observations(["AAPL"], AS_OF)

    assert result["AAPL"].error_code == "PROVIDER_UNAVAILABLE"
    assert result["AAPL"].retryable is retryable


def test_configured_requests_send_only_market_data_credentials() -> None:
    provider = _provider("premarket-iex.json", require_auth=True)
    result = provider.fetch_premarket_observations(["AAPL"], AS_OF)
    assert result["AAPL"].provider == "alpaca"
