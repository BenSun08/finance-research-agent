"""Small private helpers shared by bounded event adapters."""

from __future__ import annotations

import hashlib
import urllib.parse
from datetime import datetime, timedelta

from finance_research_agent.adapters.http_client import (
    AllowedRequest,
    RequestRejected,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import SourceHealth
from finance_research_agent.domain.policies import SourcePolicy


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("clock must return timezone-aware UTC")
    return value


def request_for_url(
    adapter: str,
    url: str,
    policy: SourcePolicy,
    *,
    accepted_content_types: tuple[str, ...],
) -> AllowedRequest:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise RequestRejected("source URL must be HTTPS on the default port")
    path = parsed.path or "/"
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if any(len(values) != 1 for values in query.values()):
        raise RequestRejected("source URL query is not bounded")
    return AllowedRequest.for_adapter(
        adapter,
        path,
        host=parsed.hostname,
        query={key: values[0] for key, values in query.items()},
        accepted_content_types=accepted_content_types,
        response_byte_limit=policy.maximum_response_bytes,
    )


def deadline(now: datetime, policy: SourcePolicy) -> datetime:
    return now + timedelta(seconds=float(policy.request_deadline_seconds))


def unavailable(provider: str, required: bool, error_code: ErrorCode, message: str) -> SourceHealth:
    return SourceHealth(
        provider=provider,
        available=False,
        required=required,
        error_code=error_code,
        message=message[:256],
    )


def available(provider: str, required: bool, *, empty_valid: bool = False) -> SourceHealth:
    return SourceHealth(
        provider=provider,
        available=True,
        required=required,
        empty_valid=empty_valid,
    )


def source_policy_hosts(policy: SourcePolicy, adapter: str) -> tuple[str, ...]:
    hosts = policy.allowed_hosts_by_adapter
    if hosts is None:
        return ()
    return hosts[adapter]
