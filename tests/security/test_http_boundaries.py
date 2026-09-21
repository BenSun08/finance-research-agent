from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from finance_research_agent.adapters.http_client import (
    AllowedRequest,
    RequestRejected,
    SafeHttpClient,
)
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap


@pytest.fixture
def source_policy() -> SourcePolicy:
    return SourcePolicy(
        version="1",
        allowed_adapters=("company_ir", "sec"),
        allowed_https_domains=("example.test",),
        freshness_by_data_type=FrozenMap({"official_events": 86400}),
        cache_retention_seconds=86400,
        request_deadline_seconds=Decimal("10"),
        retry_attempts=2,
        retry_backoff_seconds=Decimal("0.5"),
        retry_jitter_seconds=Decimal("0.1"),
        per_run_request_budgets=FrozenMap({"official_sources": 20}),
        maximum_response_bytes=1_000_000,
        allowed_content_types=("application/json", "text/html"),
        excerpt_limits=FrozenMap({"text/html": 4096}),
    )


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "169.254.169.254",
        "localhost",
        "[::1]",
        "10.0.0.7",
    ],
)
def test_private_and_local_targets_are_rejected(host: str, source_policy: SourcePolicy) -> None:
    client = SafeHttpClient(source_policy)
    request = AllowedRequest(
        adapter="company_ir",
        method="GET",
        host=host,
        path="/release",
        query={},
        accepted_content_types=("text/html",),
    )
    with pytest.raises(RequestRejected, match="host"):
        client.validate(request)


def test_cross_domain_redirect_is_rejected(
    source_policy: SourcePolicy, redirect_transport: object
) -> None:
    client = SafeHttpClient(
        source_policy,
        transport=redirect_transport,
        resolver=lambda host, port: ("93.184.216.34",),
    )
    request = AllowedRequest.for_adapter("sec", "/submissions/CIK.json")
    with pytest.raises(RequestRejected, match="redirect"):
        client.request(request, deadline=datetime(2026, 9, 21, 13, tzinfo=UTC))


def test_same_host_redirect_is_allowed_only_when_policy_enables_it(
    source_policy: SourcePolicy,
) -> None:
    policy = source_policy.model_copy(update={"allow_redirects": True})
    responses = iter(
        (
            httpx.Response(302, headers={"location": "https://example.test/next"}),
            httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}"),
        )
    )
    client = SafeHttpClient(
        policy,
        transport=httpx.MockTransport(lambda request: next(responses)),
        resolver=lambda host, port: ("93.184.216.34",),
        clock=lambda: datetime(2026, 9, 21, 12, tzinfo=UTC),
    )

    result = client.request(
        AllowedRequest.for_adapter("sec", "/submissions/CIK.json"),
        deadline=datetime(2026, 9, 21, 13, tzinfo=UTC),
    )

    assert result.status_code == 200


@pytest.fixture
def redirect_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://other.test/next"})

    return httpx.MockTransport(handler)


def test_retryable_responses_are_retried_with_injected_delay(source_policy: SourcePolicy) -> None:
    responses = iter(
        (
            httpx.Response(503, headers={"content-type": "application/json"}),
            httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}"),
        )
    )
    delays: list[Decimal] = []
    transport = httpx.MockTransport(lambda request: next(responses))
    client = SafeHttpClient(
        source_policy,
        transport=transport,
        resolver=lambda host, port: ("93.184.216.34",),
        sleeper=delays.append,
        jitter=lambda attempt: Decimal("0.1"),
        clock=lambda: datetime(2026, 9, 21, 12, tzinfo=UTC),
    )

    result = client.request(
        AllowedRequest.for_adapter(
            "sec", "/submissions/CIK.json", accepted_content_types=("application/json",)
        ),
        deadline=datetime(2026, 9, 21, 13, tzinfo=UTC),
    )

    assert result.status_code == 200
    assert result.attempts == 2
    assert delays == [Decimal("0.6")]


def test_non_retryable_authentication_response_is_returned_once(
    source_policy: SourcePolicy,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, headers={"content-type": "application/json"}, content=b"{}")

    client = SafeHttpClient(
        source_policy,
        transport=httpx.MockTransport(handler),
        resolver=lambda host, port: ("93.184.216.34",),
        clock=lambda: datetime(2026, 9, 21, 12, tzinfo=UTC),
    )

    result = client.request(
        AllowedRequest.for_adapter("sec", "/submissions/CIK.json"),
        deadline=datetime(2026, 9, 21, 13, tzinfo=UTC),
    )

    assert result.status_code == 401
    assert result.attempts == 1
    assert calls == 1


def test_safe_response_is_immutable(source_policy: SourcePolicy) -> None:
    client = SafeHttpClient(
        source_policy,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b"{}",
            )
        ),
        resolver=lambda host, port: ("93.184.216.34",),
    )

    result = client.request(
        AllowedRequest.for_adapter("sec", "/submissions/CIK.json"),
        deadline=datetime(2026, 9, 21, 13, tzinfo=UTC),
    )

    with pytest.raises(FrozenInstanceError):
        result.status_code = 500  # type: ignore[misc]


def test_response_byte_limit_is_enforced_before_returning_content(
    source_policy: SourcePolicy,
) -> None:
    client = SafeHttpClient(
        source_policy,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b"012345",
            )
        ),
        resolver=lambda host, port: ("93.184.216.34",),
    )

    with pytest.raises(RequestRejected, match="byte limit"):
        client.request(
            AllowedRequest.for_adapter("sec", "/submissions/CIK.json", response_byte_limit=4),
            deadline=datetime(2026, 9, 21, 13, tzinfo=UTC),
        )


def test_request_does_not_accept_headers_or_encoded_traversal() -> None:
    with pytest.raises(ValueError):
        AllowedRequest.model_validate(
            {
                "adapter": "sec",
                "method": "GET",
                "host": "example.test",
                "path": "/submissions",
                "query": {},
                "headers": {"Authorization": "secret"},
                "accepted_content_types": ("application/json",),
            }
        )

    request = AllowedRequest.for_adapter(
        "sec",
        "/%2e%2e/private",
        accepted_content_types=("application/json",),
        response_byte_limit=100,
    )
    with pytest.raises(RequestRejected, match="traversal"):
        SafeHttpClient(
            SourcePolicy(
                version="1",
                allowed_adapters=("sec",),
                allowed_https_domains=("example.test",),
                freshness_by_data_type=FrozenMap({"official_events": 1}),
                cache_retention_seconds=1,
                request_deadline_seconds=Decimal("1"),
                retry_attempts=0,
                retry_backoff_seconds=Decimal("0.1"),
                retry_jitter_seconds=Decimal("0.1"),
                per_run_request_budgets=FrozenMap({"official_events": 1}),
                maximum_response_bytes=100,
                allowed_content_types=("application/json",),
                excerpt_limits=FrozenMap({"application/json": 10}),
            ),
            resolver=lambda host, port: ("93.184.216.34",),
        ).validate(request)


def test_query_rejects_control_and_format_characters(source_policy: SourcePolicy) -> None:
    with pytest.raises(ValueError, match="query"):
        request = AllowedRequest.for_adapter(
            "sec",
            "/submissions/CIK.json",
            query={"q\n": "value"},
            accepted_content_types=("application/json",),
        )
        SafeHttpClient(source_policy).validate(request)
