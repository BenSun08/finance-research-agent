"""Trusted run deadlines cap actual HTTP retries and Alpaca pagination."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import cast

import httpcore
import httpx
import pytest

from finance_research_agent.adapters.alpaca import AlpacaMarketDataProvider
from finance_research_agent.adapters.http_client import (
    AllowedRequest,
    RequestDeadlineExceeded,
    RequestRejected,
    SafeHttpClient,
)
from finance_research_agent.domain.enums import SourceRole
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import ProviderFailure
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.settings import Settings

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


@pytest.fixture
def policy() -> SourcePolicy:
    return SourcePolicy(
        version="1",
        quality_source_roles=(SourceRole.MARKET_DATA, SourceRole.MARKET_CALENDAR),
        allowed_adapters=("alpaca",),
        allowed_https_domains=("data.alpaca.markets",),
        allowed_hosts_by_adapter=FrozenMap({"alpaca": ("data.alpaca.markets",)}),
        freshness_by_data_type=FrozenMap({"market_data": 60}),
        cache_retention_seconds=60,
        request_deadline_seconds=Decimal("10"),
        retry_attempts=2,
        retry_backoff_seconds=Decimal("0.1"),
        retry_jitter_seconds=Decimal("0.1"),
        per_run_request_budgets=FrozenMap({"market_data": 20}),
        maximum_response_bytes=1_000_000,
        allowed_content_types=("application/json",),
        excerpt_limits=FrozenMap({"application/json": 4096}),
    )


def request_data() -> AllowedRequest:
    return AllowedRequest.for_adapter(
        "alpaca", "/v2/stocks/bars", host="data.alpaca.markets",
        accepted_content_types=("application/json",),
    )


@pytest.mark.parametrize(
    "deadline",
    (NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=1))), "private-secret"),
)
def test_run_deadline_requires_utc_without_echoing_input(policy: SourcePolicy, deadline) -> None:
    with pytest.raises(RequestRejected, match="UTC") as error:
        SafeHttpClient(policy, clock=lambda: NOW, run_deadline=cast(datetime, deadline))
    assert "private-secret" not in str(error.value)


@pytest.mark.parametrize("offset", (0, -1))
def test_run_deadline_rejects_expired_constructor(policy: SourcePolicy, offset: int) -> None:
    with pytest.raises(RequestDeadlineExceeded):
        SafeHttpClient(policy, clock=lambda: NOW, run_deadline=NOW + timedelta(seconds=offset))


@pytest.mark.parametrize(("call_seconds", "expected_timeout"), ((2, 2), (4, 4), (8, 4)))
def test_effective_http_timeout_uses_earliest_deadline(
    policy: SourcePolicy, call_seconds: int, expected_timeout: int
) -> None:
    timeouts: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={})

    client = SafeHttpClient(
        policy, transport=httpx.MockTransport(respond),
        resolver=lambda host, port: ("93.184.216.34",), clock=lambda: NOW,
        run_deadline=NOW + timedelta(seconds=4),
    )
    assert client.request(request_data(), NOW + timedelta(seconds=call_seconds)).status_code == 200
    assert timeouts == [expected_timeout]


def test_retry_after_cannot_extend_run_cap(policy: SourcePolicy) -> None:
    calls: list[httpx.Request] = []
    sleeps: list[Decimal] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "2"}, json={})

    client = SafeHttpClient(
        policy, transport=httpx.MockTransport(respond),
        resolver=lambda host, port: ("93.184.216.34",), clock=lambda: NOW,
        sleeper=sleeps.append, run_deadline=NOW + timedelta(seconds=1),
    )
    with pytest.raises(RequestDeadlineExceeded):
        client.request(request_data(), NOW + timedelta(seconds=60))
    assert len(calls) == 1
    assert sleeps == []


def test_response_completing_after_run_deadline_is_rejected(policy: SourcePolicy) -> None:
    now = [NOW]

    def respond(request: httpx.Request) -> httpx.Response:
        now[0] += timedelta(seconds=2)
        return httpx.Response(200, json={"private": "secret"})

    client = SafeHttpClient(
        policy, transport=httpx.MockTransport(respond),
        resolver=lambda host, port: ("93.184.216.34",), clock=lambda: now[0],
        run_deadline=NOW + timedelta(seconds=1),
    )
    with pytest.raises(RequestDeadlineExceeded) as error:
        client.request(request_data(), NOW + timedelta(seconds=60))
    assert "secret" not in str(error.value)


def test_transport_timeout_at_run_cap_does_not_retry_or_echo_exception(
    policy: SourcePolicy,
) -> None:
    now = [NOW]
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        now[0] += timedelta(seconds=2)
        raise httpx.ReadTimeout("private-secret", request=request)

    client = SafeHttpClient(
        policy, transport=httpx.MockTransport(respond),
        resolver=lambda host, port: ("93.184.216.34",), clock=lambda: now[0],
        run_deadline=NOW + timedelta(seconds=1),
    )
    with pytest.raises(RequestDeadlineExceeded) as error:
        client.request(request_data(), NOW + timedelta(seconds=60))
    assert len(calls) == 1
    assert "private-secret" not in str(error.value)
    assert error.value.__suppress_context__ is True


def test_later_request_cannot_restart_expired_run_budget(policy: SourcePolicy) -> None:
    now = [NOW]
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    client = SafeHttpClient(
        policy, transport=httpx.MockTransport(respond),
        resolver=lambda host, port: ("93.184.216.34",), clock=lambda: now[0],
        run_deadline=NOW + timedelta(seconds=1),
    )
    client.request(request_data(), NOW + timedelta(seconds=60))
    now[0] += timedelta(seconds=1)
    with pytest.raises(RequestDeadlineExceeded):
        client.request(request_data(), now[0] + timedelta(seconds=60))
    assert len(calls) == 1


def test_slow_stream_stops_reading_at_run_cap(policy: SourcePolicy) -> None:
    now = [NOW]
    chunks: list[bytes] = []

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            for chunk in (b"{", b"}", b"private-secret"):
                chunks.append(chunk)
                now[0] += timedelta(seconds=1)
                yield chunk

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=SlowStream()
        )

    client = SafeHttpClient(
        policy, transport=httpx.MockTransport(respond),
        resolver=lambda host, port: ("93.184.216.34",), clock=lambda: now[0],
        run_deadline=NOW + timedelta(seconds=2),
    )
    with pytest.raises(RequestDeadlineExceeded):
        client.request(request_data(), NOW + timedelta(seconds=60))
    assert chunks == [b"{", b"}"]


def test_alpaca_pagination_shares_run_cap_and_returns_redacted_deadline(
    policy: SourcePolicy,
) -> None:
    now = [NOW]
    timeouts: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        now[0] += timedelta(seconds=2)
        return httpx.Response(200, json={"bars": {}, "next_page_token": "private-page-token"})

    client = SafeHttpClient(
        policy, transport=httpx.MockTransport(respond),
        resolver=lambda host, port: ("93.184.216.34",), clock=lambda: now[0],
        run_deadline=NOW + timedelta(seconds=3),
    )
    provider = AlpacaMarketDataProvider(
        Settings(data_dir=Path("data"), alpaca_api_key="secret-key", alpaca_api_secret="secret"),
        client, clock=lambda: now[0],
    )
    result = provider.fetch_daily_bars(
        ("AAPL",), date(2026, 10, 7), date(2026, 10, 7),
        expected_sessions=(date(2026, 10, 7),), completed_through_session=date(2026, 10, 7),
    )["AAPL"]
    assert isinstance(result, ProviderFailure)
    assert result.error_code is ErrorCode.DEADLINE_EXCEEDED
    assert result.retryable is False
    assert timeouts == [3, 1]
    assert "secret" not in result.model_dump_json()
    assert "private-page-token" not in result.model_dump_json()


def test_httpcore_body_read_uses_budget_remaining_after_headers(
    policy: SourcePolicy, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real HTTPX/httpcore stack with a timeout-respecting socket."""
    now = [NOW]
    read_timeouts: list[float | None] = []
    closed: list[bool] = []

    class Socket(httpcore.NetworkStream):
        def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
            read_timeouts.append(timeout)
            duration = 0.8
            if timeout is not None and timeout < duration:
                now[0] += timedelta(seconds=timeout)
                raise httpcore.ReadTimeout("private-secret")
            now[0] += timedelta(seconds=duration)
            if len(read_timeouts) == 1:
                return (
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: 2\r\n\r\n"
                )
            return b"{}"

        def write(self, buffer: bytes, timeout: float | None = None) -> None:
            pass

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            return self

        def close(self) -> None:
            closed.append(True)

    class Backend(httpcore.NetworkBackend):
        def connect_tcp(self, *args, **kwargs):
            return Socket()

    monkeypatch.setattr(httpcore, "SyncBackend", Backend)
    client = SafeHttpClient(
        policy, resolver=lambda host, port: ("93.184.216.34",), clock=lambda: now[0],
        run_deadline=NOW + timedelta(seconds=1),
    )
    with pytest.raises(RequestDeadlineExceeded) as error:
        client.request(request_data(), NOW + timedelta(seconds=60))
    assert read_timeouts == pytest.approx([1.0, 0.2])
    assert now[0] == NOW + timedelta(seconds=1)
    assert "private-secret" not in str(error.value)
    assert closed


def test_expired_body_iterator_is_not_advanced(policy: SourcePolicy) -> None:
    now = [NOW]
    advanced: list[bool] = []

    def body() -> Iterator[bytes]:
        advanced.append(True)
        yield b"{}"

    client = SafeHttpClient(policy, clock=lambda: now[0], run_deadline=NOW + timedelta(seconds=1))
    now[0] += timedelta(seconds=1)
    with pytest.raises(RequestDeadlineExceeded):
        next(client._read_before_deadline(body(), now[0]))
    assert advanced == []
