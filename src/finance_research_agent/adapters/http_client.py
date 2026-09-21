"""A bounded, provider-independent HTTP boundary for external evidence."""

from __future__ import annotations

import hashlib
import ipaddress
import random
import re
import socket
import unicodedata
import urllib.parse
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Literal, cast

import httpcore
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap

_DEFAULT_HOST = "example.test"
_MAX_PATH_LENGTH = 2048
_MAX_QUERY_ENTRIES = 32
_MAX_QUERY_VALUE_LENGTH = 512
_MAX_RETRY_DELAY = Decimal("60")
_CREDENTIAL_VALUE = re.compile(r"(?i)(\b(?:ALPACA_API_KEY|ALPACA_API_SECRET)\s*=\s*)([^\s,;]+)")
_AUTHORIZATION_VALUE = re.compile(r"(?i)(\bAuthorization\s*:\s*(?:Bearer|Basic)\s+)([^\s,;]+)")
_HIDDEN_STYLE = re.compile(r"(?i)(?:display|visibility)\s*:\s*none|hidden")
_IGNORED_HTML_TAGS = frozenset(
    {"embed", "frame", "iframe", "noscript", "object", "script", "style", "svg", "template"}
)
Clock = Callable[[], datetime]
SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)


class RequestRejected(ValueError):
    """Raised when a request or response violates the source policy."""


class RequestDeadlineExceeded(RequestRejected):
    """Raised when a request cannot finish before its run deadline."""


class AllowedRequest(BaseModel):
    """Closed GET-only request data accepted by the HTTP boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    adapter: str = Field(min_length=1, max_length=64)
    method: Literal["GET"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=443, ge=1, le=65535)
    path: str = Field(min_length=1, max_length=_MAX_PATH_LENGTH)
    query: FrozenMap[str, str] = FrozenMap({})
    accepted_content_types: tuple[str, ...] = Field(min_length=1, max_length=32)
    response_byte_limit: int = Field(default=1_000_000, gt=0, le=10_000_000)

    @field_validator("path")
    @classmethod
    def path_is_safe(cls, value: str) -> str:
        return _validate_path(value)

    @field_validator("query")
    @classmethod
    def bounded_query(cls, value: FrozenMap[str, str]) -> FrozenMap[str, str]:
        if len(value) > _MAX_QUERY_ENTRIES or any(
            not 1 <= len(key) <= 128 or not 1 <= len(item) <= _MAX_QUERY_VALUE_LENGTH
            for key, item in value.items()
        ):
            raise ValueError("query must be bounded")
        if any(
            unicodedata.category(character) in {"Cc", "Cf"}
            for key, item in value.items()
            for character in f"{key}{item}"
        ):
            raise ValueError("query contains a control or format character")
        return value

    @field_validator("accepted_content_types")
    @classmethod
    def unique_content_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len({item.casefold() for item in value}) or any(
            not item.strip() or len(item) > 128 or ";" in item for item in value
        ):
            raise ValueError("accepted content types must be unique media types")
        return value

    @classmethod
    def for_adapter(
        cls,
        adapter: str,
        path: str,
        *,
        host: str = _DEFAULT_HOST,
        port: int = 443,
        query: Mapping[str, str] | None = None,
        accepted_content_types: tuple[str, ...] = ("application/json", "text/html"),
        response_byte_limit: int = 1_000_000,
    ) -> AllowedRequest:
        return cls(
            adapter=adapter,
            method="GET",
            host=host,
            port=port,
            path=path,
            query=FrozenMap({} if query is None else query),
            accepted_content_types=accepted_content_types,
            response_byte_limit=response_byte_limit,
        )


@dataclass(frozen=True, slots=True)
class SafeResponse:
    """Bounded response bytes and metadata returned by the HTTP boundary."""

    status_code: int
    content: bytes
    content_type: str
    url: str
    attempts: int


class _PinnedNetworkBackend(httpcore.NetworkBackend):
    """Connect to validated addresses while retaining the request hostname."""

    def __init__(
        self,
        addresses: tuple[str, ...],
        *,
        delegate: httpcore.NetworkBackend | None = None,
    ) -> None:
        if not addresses:
            raise ValueError("at least one validated address is required")
        self._addresses = addresses
        self._delegate = delegate or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.NetworkStream:
        return self._delegate.connect_tcp(
            self._addresses[0], port, timeout, local_address, socket_options
        )

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.NetworkStream:
        return self._delegate.connect_unix_socket(path, timeout, socket_options)

    def sleep(self, seconds: float) -> None:
        self._delegate.sleep(seconds)


class _PinnedResponseStream(httpx.SyncByteStream):
    def __init__(self, stream: Iterable[bytes]) -> None:
        self._stream = stream

    def __iter__(self) -> Iterator[bytes]:
        yield from self._stream

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if callable(close):
            close()


class _PinnedHTTPTransport(httpx.BaseTransport):
    def __init__(self, addresses: tuple[str, ...]) -> None:
        self._pool = httpcore.ConnectionPool(
            network_backend=_PinnedNetworkBackend(addresses),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        core_response = self._pool.handle_request(core_request)
        return httpx.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=_PinnedResponseStream(cast(Iterable[bytes], core_response.stream)),
            extensions=core_response.extensions,
            request=request,
        )

    def close(self) -> None:
        self._pool.close()


@dataclass(frozen=True, slots=True)
class SanitizedText:
    """Bounded visible text that remains explicitly untrusted external data."""

    text: str
    source_hash_sha256: str
    untrusted: bool = True


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._suppressed: list[str] = []
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        attributes = {name.lower(): (value or "") for name, value in attrs}
        hidden = (
            normalized_tag in _IGNORED_HTML_TAGS
            or "hidden" in attributes
            or attributes.get("aria-hidden", "").lower() == "true"
            or bool(_HIDDEN_STYLE.search(attributes.get("style", "")))
        )
        if hidden:
            self._suppressed.append(normalized_tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self._suppressed:
            index = len(self._suppressed) - 1 - self._suppressed[::-1].index(normalized_tag)
            del self._suppressed[index:]

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return " ".join(self._parts)


def sanitize_external_text(raw: bytes, content_type: str, max_chars: int) -> SanitizedText:
    """Extract bounded visible text without treating external prose as instructions."""

    if type(raw) is not bytes or max_chars < 1:
        raise ValueError("raw must be bytes and max_chars must be positive")
    source_hash = hashlib.sha256(raw).hexdigest()
    byte_limit = min(len(raw), min(4_000_000, max(4096, max_chars * 16)))
    bounded = raw[:byte_limit].decode("utf-8", errors="replace")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "text/html" or media_type.endswith("+html"):
        parser = _VisibleTextParser()
        parser.feed(bounded)
        parser.close()
        text = parser.text
    else:
        text = bounded
    normalized = unicodedata.normalize("NFKC", text)
    visible = "".join(
        character for character in normalized if unicodedata.category(character) not in {"Cc", "Cf"}
    )
    return SanitizedText(
        text=" ".join(visible.split())[:max_chars],
        source_hash_sha256=source_hash,
    )


def retry_delay(
    attempt: int,
    base_seconds: Decimal,
    cap_seconds: Decimal,
    jitter_seconds: Decimal,
) -> Decimal:
    """Return the capped exponential delay prescribed by the source policy."""

    if attempt < 1:
        raise ValueError("attempt must be positive")
    exponential = base_seconds * (Decimal(2) ** Decimal(attempt - 1))
    return min(exponential, cap_seconds) + jitter_seconds


def _redact_match(match: re.Match[str]) -> str:
    return f"{match.group(1)}[REDACTED]"


def redact(value: str, secrets: Sequence[str]) -> str:
    """Redact supplied secrets and common credential-shaped values."""

    cleaned = value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = _AUTHORIZATION_VALUE.sub(_redact_match, cleaned)
    return _CREDENTIAL_VALUE.sub(_redact_match, cleaned)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _resolve_default(host: str, port: int) -> tuple[str, ...]:
    return tuple(
        {str(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    )


def _unsafe_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise RequestRejected("host resolved to an invalid address") from error
    return not address.is_global or any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


class SafeHttpClient:
    """Validate and fetch one allowlisted GET request with bounded retries."""

    def __init__(
        self,
        source_policy: SourcePolicy,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Callable[[str, int], tuple[str, ...]] = _resolve_default,
        sleeper: Callable[[Decimal], None] | None = None,
        jitter: Callable[[int], Decimal] | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._policy = source_policy
        self._transport = transport
        self._resolver = resolver
        self._sleeper = sleeper or (lambda seconds: None)
        self._jitter: Callable[[int], Decimal]
        if jitter is None:
            source = random.SystemRandom()
            self._jitter = lambda attempt: Decimal(
                str(source.uniform(0.0, float(source_policy.retry_jitter_seconds)))
            )
        else:
            self._jitter = jitter
        self._clock = clock

    def validate(self, request: AllowedRequest) -> str:
        """Validate a request and return its safe absolute URL."""

        url, _addresses = self._validate_request(request)
        return url

    def _validate_request(self, request: AllowedRequest) -> tuple[str, tuple[str, ...]]:
        """Validate a request and retain the addresses for the actual connection."""

        if not isinstance(request, AllowedRequest):
            raise RequestRejected("request is not an allowed request")
        if request.adapter not in self._policy.allowed_adapters:
            raise RequestRejected("adapter is not allowed")
        if request.response_byte_limit > self._policy.maximum_response_bytes:
            raise RequestRejected("response byte limit exceeds policy")
        allowed_content_types = {item.casefold() for item in self._policy.allowed_content_types}
        if any(
            item.casefold() not in allowed_content_types for item in request.accepted_content_types
        ):
            raise RequestRejected("content type is not allowed")
        if "@" in request.host or any(character.isspace() for character in request.host):
            raise RequestRejected("host contains user info or whitespace")

        host = request.host.strip("[]").lower()
        if host == "localhost":
            raise RequestRejected("host is not allowlisted")
        allowed_hosts_by_adapter = self._policy.allowed_hosts_by_adapter
        if allowed_hosts_by_adapter is None or request.adapter not in allowed_hosts_by_adapter:
            raise RequestRejected("adapter host policy is required")
        allowed_hosts = allowed_hosts_by_adapter[request.adapter]
        host_error = "adapter host is not allowlisted"
        if not _host_matches_policy(host, allowed_hosts):
            raise RequestRejected(host_error)
        if not _port_allowed(host, request.port, self._policy.allowed_ports_by_host):
            raise RequestRejected("port is not allowed")
        addresses: tuple[str, ...]
        try:
            parsed_host = ipaddress.ip_address(host)
        except ValueError:
            parsed_host = None
        if parsed_host is not None:
            if _unsafe_address(host):
                raise RequestRejected("host resolves to a private or local address")
            addresses = (host,)
        else:
            try:
                addresses = self._resolver(host, request.port)
            except OSError as error:
                raise RequestRejected("host could not be resolved") from error
            if not addresses or any(_unsafe_address(address) for address in addresses):
                raise RequestRejected("host resolves to a private or local address")

        _validate_path(request.path)
        netloc = host if request.port == 443 else f"{host}:{request.port}"
        raw_url = urllib.parse.urlunsplit(
            ("https", netloc, request.path, urllib.parse.urlencode(dict(request.query)), "")
        )
        parsed = urllib.parse.urlsplit(raw_url)
        parsed_port = 443 if parsed.port is None else parsed.port
        if parsed.scheme != "https" or parsed_port != request.port or parsed.fragment:
            raise RequestRejected("request must use HTTPS and an allowed port")
        return raw_url, addresses

    def request(
        self,
        request: AllowedRequest,
        deadline: datetime,
        *,
        provider_credentials: tuple[str, str] | None = None,
    ) -> SafeResponse:
        if provider_credentials is not None and any(
            type(value) is not str or not value for value in provider_credentials
        ):
            raise RequestRejected("provider credentials must be non-empty strings")
        url, addresses = self._validate_request(request)
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise RequestRejected("deadline must be timezone-aware")
        if deadline <= self._clock():
            raise RequestDeadlineExceeded("request deadline exceeded")

        max_attempts = max(1, self._policy.retry_attempts + 1)
        attempt = 1
        redirects = 0
        transport = self._transport or _PinnedHTTPTransport(addresses)
        headers = (
            {
                "APCA-API-KEY-ID": provider_credentials[0],
                "APCA-API-SECRET-KEY": provider_credentials[1],
            }
            if provider_credentials is not None
            else None
        )
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            while attempt <= max_attempts:
                remaining = (deadline - self._clock()).total_seconds()
                if remaining <= 0:
                    raise RequestDeadlineExceeded("request deadline exceeded")
                try:
                    with client.stream(
                        request.method,
                        url,
                        headers=headers,
                        timeout=min(float(self._policy.request_deadline_seconds), remaining),
                    ) as response:
                        if 300 <= response.status_code < 400:
                            redirects += 1
                            if redirects > 5:
                                raise RequestRejected("redirect limit exceeded")
                            url = self._validate_redirect(url, response.headers.get("location"))
                            continue
                        if self._retryable_status(response.status_code) and attempt < max_attempts:
                            delay = self._retry_after(response.headers.get("retry-after"), attempt)
                            self._sleep_before_retry(delay, deadline)
                            attempt += 1
                            continue
                        content_type = response.headers.get("content-type", "")
                        content_type = content_type.split(";", 1)[0].strip().casefold()
                        if content_type not in {
                            item.casefold() for item in request.accepted_content_types
                        }:
                            raise RequestRejected("response content type is not accepted")
                        content = _read_bounded(response.iter_bytes(), request.response_byte_limit)
                        return SafeResponse(
                            status_code=response.status_code,
                            content=content,
                            content_type=content_type,
                            url=str(response.url),
                            attempts=attempt,
                        )
                except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as error:
                    if attempt >= max_attempts:
                        raise RequestRejected("request transport failed") from error
                    self._sleep_before_retry(
                        retry_delay(
                            attempt,
                            self._policy.retry_backoff_seconds,
                            _MAX_RETRY_DELAY,
                            self._bounded_jitter(attempt),
                        ),
                        deadline,
                    )
                    attempt += 1
        raise RequestRejected("request retries exhausted")

    def _retryable_status(self, status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code <= 599

    def _retry_after(self, value: str | None, attempt: int) -> Decimal:
        if value is not None:
            try:
                delay = max(Decimal("0"), Decimal(value))
            except ArithmeticError:
                try:
                    retry_at = parsedate_to_datetime(value)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    delay = max(
                        Decimal("0"),
                        Decimal(str((retry_at - self._clock()).total_seconds())),
                    )
                except (TypeError, ValueError, OverflowError):
                    delay = Decimal("0")
            return min(delay, _MAX_RETRY_DELAY)
        return retry_delay(
            attempt,
            self._policy.retry_backoff_seconds,
            _MAX_RETRY_DELAY,
            self._bounded_jitter(attempt),
        )

    def _bounded_jitter(self, attempt: int) -> Decimal:
        return min(
            max(Decimal("0"), self._jitter(attempt)),
            self._policy.retry_jitter_seconds,
        )

    def _sleep_before_retry(self, delay: Decimal, deadline: datetime) -> None:
        if self._clock() + _seconds(delay) >= deadline:
            raise RequestDeadlineExceeded("retry would exceed request deadline")
        self._sleeper(delay)

    def _validate_redirect(self, original_url: str, location: str | None) -> str:
        if not self._policy.allow_redirects or not location:
            raise RequestRejected("redirect is not allowed")
        target = urllib.parse.urlsplit(urllib.parse.urljoin(original_url, location))
        original = urllib.parse.urlsplit(original_url)
        target_port = 443 if target.port is None else target.port
        if (
            target.scheme != "https"
            or target.hostname is None
            or target.hostname != original.hostname
            or not _port_allowed(
                target.hostname,
                target_port,
                self._policy.allowed_ports_by_host,
            )
            or target.username is not None
            or target.password is not None
            or target.fragment
        ):
            raise RequestRejected("redirect crosses an unsafe host")
        _validate_path(target.path)
        query = _validate_query_string(target.query)
        return urllib.parse.urlunsplit((target.scheme, target.netloc, target.path, query, ""))


def _host_matches_policy(host: str, domains: Sequence[str]) -> bool:
    return any(host == domain.lower() or host.endswith(f".{domain.lower()}") for domain in domains)


def _port_allowed(
    host: str,
    port: int,
    allowed_ports_by_host: FrozenMap[str, tuple[int, ...]],
) -> bool:
    return port == 443 or any(
        host == allowed_host.lower() or host.endswith(f".{allowed_host.lower()}")
        for allowed_host, ports in allowed_ports_by_host.items()
        if port in ports
    )


def _contains_traversal(path: str) -> bool:
    candidate = _fully_percent_decode(path).replace("\\", "/")
    return any(part in {".", ".."} for part in candidate.split("/"))


def _contains_path_delimiter(path: str) -> bool:
    candidate = _fully_percent_decode(path)
    return "?" in candidate or "#" in candidate


def _validate_path(path: str) -> str:
    if len(path) > _MAX_PATH_LENGTH:
        raise RequestRejected("path exceeds maximum length")
    if path != path.strip() or not path.startswith("/"):
        raise RequestRejected("path must be an absolute request path")
    decoded = _fully_percent_decode(path)
    if len(decoded) > _MAX_PATH_LENGTH or not decoded.startswith("/"):
        raise RequestRejected("path must be an absolute request path")
    if (
        any(unicodedata.category(character) in {"Cc", "Cf"} for character in f"{path}{decoded}")
        or "\\" in path
        or "\\" in decoded
    ):
        raise RequestRejected("path contains an invalid control or format character")
    if _contains_path_delimiter(path):
        raise RequestRejected("path contains an ambiguous query or fragment delimiter")
    if _contains_traversal(path):
        raise RequestRejected("path contains traversal")
    return path


def _fully_percent_decode(value: str) -> str:
    candidate = value
    while True:
        decoded = urllib.parse.unquote(candidate)
        if decoded == candidate:
            return candidate
        candidate = decoded


def _validate_query_string(value: str) -> str:
    if len(value) > _MAX_QUERY_ENTRIES * (_MAX_QUERY_VALUE_LENGTH * 2 + 2):
        raise RequestRejected("redirect query is too large")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise RequestRejected("redirect query contains a control or format character")
    try:
        pairs = urllib.parse.parse_qsl(
            value,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=_MAX_QUERY_ENTRIES,
        )
    except ValueError as error:
        raise RequestRejected("redirect query is invalid or too large") from error
    keys = tuple(key for key, _value in pairs)
    if len(keys) != len(set(keys)) or any(
        not 1 <= len(key) <= 128
        or not 1 <= len(item) <= _MAX_QUERY_VALUE_LENGTH
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in f"{key}{item}")
        for key, item in pairs
    ):
        raise RequestRejected("redirect query is not bounded")
    return urllib.parse.urlencode(pairs)


def _read_bounded(chunks: Iterable[bytes], limit: int) -> bytes:
    collected = bytearray()
    for chunk in chunks:
        collected.extend(chunk)
        if len(collected) > limit:
            raise RequestRejected("response exceeds byte limit")
    return bytes(collected)


def _seconds(value: Decimal) -> timedelta:
    return timedelta(seconds=float(value))
