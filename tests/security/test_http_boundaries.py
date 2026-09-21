from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import format_datetime
from urllib.parse import quote

import httpcore
import httpx
import pytest

from finance_research_agent.adapters.http_client import (
    AllowedRequest,
    RequestRejected,
    SafeHttpClient,
    _PinnedNetworkBackend,
)
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap


@pytest.fixture
def source_policy() -> SourcePolicy:
    return SourcePolicy(
        version="1",
        allowed_adapters=("company_ir", "sec"),
        allowed_https_domains=("example.test",),
        allowed_hosts_by_adapter=FrozenMap(
            {"company_ir": ("example.test",), "sec": ("example.test",)}
        ),
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


class _RecordingStream(httpcore.NetworkStream):
    def __init__(self) -> None:
        self.tls_server_name: str | None = None

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return b""

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        return None

    def close(self) -> None:
        return None

    def start_tls(
        self,
        ssl_context: object,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        self.tls_server_name = server_hostname
        return self


class _RecordingBackend(httpcore.NetworkBackend):
    def __init__(self) -> None:
        self.connected_host: str | None = None
        self.stream = _RecordingStream()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object | None = None,
    ) -> httpcore.NetworkStream:
        self.connected_host = host
        return self.stream


def test_validated_dns_address_is_pinned_without_changing_tls_hostname() -> None:
    delegate = _RecordingBackend()
    backend = _PinnedNetworkBackend(("93.184.216.34",), delegate=delegate)
    connection = httpcore.HTTPConnection(
        httpcore.Origin(b"https", b"example.test", 443),
        network_backend=backend,
    )

    connection._connect(httpcore.Request("GET", "https://example.test/release"))

    assert delegate.connected_host == "93.184.216.34"
    assert delegate.stream.tls_server_name == "example.test"


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


def test_default_retry_jitter_uses_the_policy_bound(source_policy: SourcePolicy) -> None:
    responses = iter(
        (
            httpx.Response(503, headers={"content-type": "application/json"}),
            httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}"),
        )
    )
    delays: list[Decimal] = []
    client = SafeHttpClient(
        source_policy,
        transport=httpx.MockTransport(lambda request: next(responses)),
        resolver=lambda host, port: ("93.184.216.34",),
        sleeper=delays.append,
        clock=lambda: datetime(2026, 9, 21, 12, tzinfo=UTC),
    )

    client.request(
        AllowedRequest.for_adapter(
            "sec", "/submissions/CIK.json", accepted_content_types=("application/json",)
        ),
        deadline=datetime(2026, 9, 21, 13, tzinfo=UTC),
    )

    assert len(delays) == 1
    assert Decimal("0.5") < delays[0] <= Decimal("0.6")


def test_retry_after_http_date_is_used_and_capped_by_the_run_deadline(
    source_policy: SourcePolicy,
) -> None:
    now = datetime(2026, 9, 21, 12, tzinfo=UTC)
    responses = iter(
        (
            httpx.Response(
                429,
                headers={
                    "retry-after": format_datetime(now.replace(second=2), usegmt=True),
                    "content-type": "application/json",
                },
            ),
            httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}"),
        )
    )
    delays: list[Decimal] = []
    client = SafeHttpClient(
        source_policy,
        transport=httpx.MockTransport(lambda request: next(responses)),
        resolver=lambda host, port: ("93.184.216.34",),
        sleeper=delays.append,
        clock=lambda: now,
    )

    result = client.request(
        AllowedRequest.for_adapter(
            "sec", "/submissions/CIK.json", accepted_content_types=("application/json",)
        ),
        deadline=datetime(2026, 9, 21, 12, 5, tzinfo=UTC),
    )

    assert result.status_code == 200
    assert delays == [Decimal("2")]


def test_retry_after_http_date_cannot_outlive_the_run_deadline(
    source_policy: SourcePolicy,
) -> None:
    now = datetime(2026, 9, 21, 12, tzinfo=UTC)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            429,
            headers={
                "retry-after": format_datetime(now.replace(minute=2), usegmt=True),
                "content-type": "application/json",
            },
        )
    )
    client = SafeHttpClient(
        source_policy,
        transport=transport,
        resolver=lambda host, port: ("93.184.216.34",),
        clock=lambda: now,
    )

    with pytest.raises(RequestRejected, match="deadline"):
        client.request(
            AllowedRequest.for_adapter("sec", "/submissions/CIK.json"),
            deadline=datetime(2026, 9, 21, 12, 0, 10, tzinfo=UTC),
        )


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


def test_content_type_matching_is_case_insensitive(source_policy: SourcePolicy) -> None:
    client = SafeHttpClient(
        source_policy,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "Application/JSON; charset=utf-8"},
                content=b"{}",
            )
        ),
        resolver=lambda host, port: ("93.184.216.34",),
    )

    result = client.request(
        AllowedRequest.for_adapter(
            "sec", "/submissions/CIK.json", accepted_content_types=("APPLICATION/JSON",)
        ),
        deadline=datetime(2026, 9, 21, 13, tzinfo=UTC),
    )

    assert result.content_type == "application/json"


def test_redirect_query_is_bounded_and_control_safe(source_policy: SourcePolicy) -> None:
    policy = source_policy.model_copy(update={"allow_redirects": True})
    oversized_query = "&".join(f"key{index}=value" for index in range(33))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302,
            headers={"location": f"https://example.test/next?{oversized_query}"},
        )
    )
    client = SafeHttpClient(
        policy,
        transport=transport,
        resolver=lambda host, port: ("93.184.216.34",),
    )

    with pytest.raises(RequestRejected, match="query"):
        client.request(
            AllowedRequest.for_adapter("sec", "/submissions/CIK.json"),
            deadline=datetime(2026, 9, 21, 13, tzinfo=UTC),
        )


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

    with pytest.raises(ValueError, match="traversal"):
        AllowedRequest.for_adapter(
            "sec",
            "/%2e%2e/private",
            accepted_content_types=("application/json",),
            response_byte_limit=100,
        )


def test_path_cannot_smuggle_query_data_outside_bounded_query_mapping() -> None:
    with pytest.raises(ValueError, match="path"):
        AllowedRequest.for_adapter(
            "sec",
            "/submissions?unbounded=outside",
            accepted_content_types=("application/json",),
        )


def test_path_rejects_ninth_level_encoded_query_delimiter() -> None:
    nested_delimiter = "?"
    for _ in range(9):
        nested_delimiter = quote(nested_delimiter, safe="")

    with pytest.raises(ValueError, match="path"):
        AllowedRequest.for_adapter(
            "sec",
            f"/submissions/{nested_delimiter}",
            accepted_content_types=("application/json",),
        )


def test_adapter_hosts_and_non_default_ports_are_policy_bound(source_policy: SourcePolicy) -> None:
    values = source_policy.model_dump(mode="python")
    values.update(
        {
            "allowed_https_domains": ("example.test", "sec.test", "ir.test"),
            "allowed_hosts_by_adapter": FrozenMap(
                {"company_ir": ("ir.test",), "sec": ("sec.test",)}
            ),
            "allowed_ports_by_host": FrozenMap({"sec.test": (8443,)}),
        }
    )
    policy = SourcePolicy.model_validate(values)
    client = SafeHttpClient(policy, resolver=lambda host, port: ("93.184.216.34",))

    allowed = AllowedRequest.for_adapter(
        "sec",
        "/submissions/CIK.json",
        host="sec.test",
        port=8443,
        accepted_content_types=("application/json",),
    )
    assert client.validate(allowed) == "https://sec.test:8443/submissions/CIK.json"

    with pytest.raises(RequestRejected, match="adapter host"):
        client.validate(
            AllowedRequest.for_adapter(
                "company_ir",
                "/release",
                host="sec.test",
                accepted_content_types=("application/json",),
            )
        )

    with pytest.raises(RequestRejected, match="port"):
        client.validate(
            AllowedRequest.for_adapter(
                "sec",
                "/submissions/CIK.json",
                host="sec.test",
                port=9443,
                accepted_content_types=("application/json",),
            )
        )

    with pytest.raises(ValueError, match="path"):
        AllowedRequest.for_adapter(
            "sec",
            "/submissions%3Funbounded=outside",
            accepted_content_types=("application/json",),
        )


def test_client_rejects_requests_without_adapter_host_policy(
    source_policy: SourcePolicy,
) -> None:
    policy_without_mapping = source_policy.model_copy(update={"allowed_hosts_by_adapter": None})
    client = SafeHttpClient(policy_without_mapping, resolver=lambda host, port: ("93.184.216.34",))

    with pytest.raises(RequestRejected, match="adapter host policy"):
        client.validate(AllowedRequest.for_adapter("sec", "/submissions/CIK.json"))


def _nested_percent_encoding(value: str, rounds: int = 9) -> str:
    for _ in range(rounds):
        value = quote(value, safe="")
    return value


@pytest.mark.parametrize(
    "redirect_path",
    [
        "/" + "a" * 2048,
        "/safe/" + _nested_percent_encoding("?"),
        "/safe/%0A",
    ],
)
def test_redirect_path_uses_the_same_bounded_safe_validator(
    source_policy: SourcePolicy,
    redirect_path: str,
) -> None:
    policy = source_policy.model_copy(update={"allow_redirects": True})
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302,
            headers={"location": f"https://example.test{redirect_path}"},
        )
    )
    client = SafeHttpClient(
        policy,
        transport=transport,
        resolver=lambda host, port: ("93.184.216.34",),
    )

    with pytest.raises(RequestRejected, match="path"):
        client.request(
            AllowedRequest.for_adapter("sec", "/submissions/CIK.json"),
            deadline=datetime(2026, 9, 21, 13, tzinfo=UTC),
        )


def test_query_rejects_control_and_format_characters(source_policy: SourcePolicy) -> None:
    with pytest.raises(ValueError, match="query"):
        request = AllowedRequest.for_adapter(
            "sec",
            "/submissions/CIK.json",
            query={"q\n": "value"},
            accepted_content_types=("application/json",),
        )
        SafeHttpClient(source_policy).validate(request)
