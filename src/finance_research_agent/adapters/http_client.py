"""A bounded, provider-independent HTTP boundary for external evidence."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import unicodedata
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap

_DEFAULT_HOST = "example.test"
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
    path: str = Field(min_length=1, max_length=2048)
    query: FrozenMap[str, str] = FrozenMap({})
    accepted_content_types: tuple[str, ...] = Field(min_length=1, max_length=32)
    response_byte_limit: int = Field(default=1_000_000, gt=0, le=10_000_000)

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
        if len(value) != len(set(value)) or any(
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
        query: Mapping[str, str] | None = None,
        accepted_content_types: tuple[str, ...] = ("application/json", "text/html"),
        response_byte_limit: int = 1_000_000,
    ) -> AllowedRequest:
        return cls(
            adapter=adapter,
            method="GET",
            host=host,
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
        self._jitter = jitter or (lambda attempt: Decimal("0"))
        self._clock = clock

    def validate(self, request: AllowedRequest) -> str:
        """Validate a request and return its safe absolute URL."""

        if not isinstance(request, AllowedRequest):
            raise RequestRejected("request is not an allowed request")
        if request.adapter not in self._policy.allowed_adapters:
            raise RequestRejected("adapter is not allowed")
        if request.response_byte_limit > self._policy.maximum_response_bytes:
            raise RequestRejected("response byte limit exceeds policy")
        if any(
            item not in self._policy.allowed_content_types
            for item in request.accepted_content_types
        ):
            raise RequestRejected("content type is not allowed")
        if "@" in request.host or any(character.isspace() for character in request.host):
            raise RequestRejected("host contains user info or whitespace")

        host = request.host.strip("[]").lower()
        if host == "localhost" or not _host_matches_policy(
            host, self._policy.allowed_https_domains
        ):
            raise RequestRejected("host is not allowlisted")
        try:
            parsed_host = ipaddress.ip_address(host)
        except ValueError:
            parsed_host = None
        if parsed_host is not None:
            if _unsafe_address(host):
                raise RequestRejected("host resolves to a private or local address")
        else:
            try:
                addresses = self._resolver(host, 443)
            except OSError as error:
                raise RequestRejected("host could not be resolved") from error
            if not addresses or any(_unsafe_address(address) for address in addresses):
                raise RequestRejected("host resolves to a private or local address")

        if request.path != request.path.strip() or not request.path.startswith("/"):
            raise RequestRejected("path must be an absolute request path")
        if any(character in request.path for character in "#\\\x00\r\n"):
            raise RequestRejected("path contains an invalid character")
        if _contains_traversal(request.path):
            raise RequestRejected("path contains traversal")
        raw_url = urllib.parse.urlunsplit(
            ("https", host, request.path, urllib.parse.urlencode(dict(request.query)), "")
        )
        parsed = urllib.parse.urlsplit(raw_url)
        if parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.fragment:
            raise RequestRejected("request must use HTTPS and the default port")
        return raw_url

    def request(self, request: AllowedRequest, deadline: datetime) -> SafeResponse:
        url = self.validate(request)
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise RequestRejected("deadline must be timezone-aware")
        if deadline <= self._clock():
            raise RequestDeadlineExceeded("request deadline exceeded")

        max_attempts = max(1, self._policy.retry_attempts + 1)
        attempt = 1
        redirects = 0
        with httpx.Client(transport=self._transport, follow_redirects=False) as client:
            while attempt <= max_attempts:
                remaining = (deadline - self._clock()).total_seconds()
                if remaining <= 0:
                    raise RequestDeadlineExceeded("request deadline exceeded")
                try:
                    with client.stream(
                        request.method,
                        url,
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
                        content_type = (
                            response.headers.get("content-type", "").split(";", 1)[0].strip()
                        )
                        if content_type not in request.accepted_content_types:
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
                            self._jitter(attempt),
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
                return max(Decimal("0"), Decimal(value))
            except ArithmeticError:
                pass
        return retry_delay(
            attempt,
            self._policy.retry_backoff_seconds,
            _MAX_RETRY_DELAY,
            self._jitter(attempt),
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
        if (
            target.scheme != "https"
            or target.hostname != original.hostname
            or target.port not in (None, 443)
            or target.username is not None
            or target.password is not None
            or target.fragment
            or not target.path.startswith("/")
            or _contains_traversal(target.path)
        ):
            raise RequestRejected("redirect crosses an unsafe host")
        return urllib.parse.urlunsplit(
            (target.scheme, target.netloc, target.path, target.query, "")
        )


def _host_matches_policy(host: str, domains: Sequence[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _contains_traversal(path: str) -> bool:
    candidate = path.replace("\\", "/")
    for _ in range(8):
        decoded = urllib.parse.unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return any(part in {".", ".."} for part in candidate.split("/"))


def _read_bounded(chunks: Iterable[bytes], limit: int) -> bytes:
    collected = bytearray()
    for chunk in chunks:
        collected.extend(chunk)
        if len(collected) > limit:
            raise RequestRejected("response exceeds byte limit")
    return bytes(collected)


def _seconds(value: Decimal) -> timedelta:
    return timedelta(seconds=float(value))
