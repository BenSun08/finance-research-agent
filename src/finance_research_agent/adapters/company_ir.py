"""Fail-closed verification of configured company investor-relations sources."""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from html.parser import HTMLParser

from finance_research_agent.adapters._event_common import (
    EvidenceCutoffViolation,
    available,
    deadline,
    digest,
    failure,
    request_for_url,
    unavailable,
)
from finance_research_agent.adapters.http_client import (
    RequestDeadlineExceeded,
    RequestRejected,
    RequestTransportUnavailable,
    SafeHttpClient,
    sanitize_external_text,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    EventCollection,
    EventRecord,
    EvidenceItem,
    SourceObservation,
)
from finance_research_agent.domain.policies import SourcePolicy, WatchlistItem
from finance_research_agent.domain.types import FrozenMap

Clock = Callable[[], datetime]
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})")


class _IrParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.event_time: str | None = None
        self.published_time: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value or "" for name, value in attrs}
        if tag.casefold() == "time" and values.get("datetime"):
            self.event_time = values["datetime"]
        if tag.casefold() == "meta" and values.get("name", "").casefold() in {
            "published",
            "published_time",
        }:
            self.published_time = values.get("content") or None

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("company IR timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


class CompanyIrAdapter:
    """Read only URLs already present in the validated watchlist configuration."""

    provider_id = "company_ir"

    def __init__(
        self,
        http_client: SafeHttpClient,
        source_policy: SourcePolicy,
        *,
        company_names: Mapping[str, str] | None = None,
        watchlist_items: Sequence[WatchlistItem] = (),
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._http_client = http_client
        self._policy = source_policy
        self._company_names = dict(company_names or {})
        self._watchlist_items = tuple(watchlist_items)
        self._clock = clock

    def collect_for_item(
        self,
        item: WatchlistItem,
        *,
        start: datetime,
        end: datetime,
        cutoff_at: datetime,
    ) -> EventCollection:
        now = self._clock()
        if now > cutoff_at:
            message = "company IR retrieval is after the evidence cutoff"
            return EventCollection(
                provider=self.provider_id,
                source_health=(
                    unavailable(
                        self.provider_id,
                        True,
                        ErrorCode.EVIDENCE_CUTOFF_VIOLATION,
                        message,
                    ),
                ),
                failures=(
                    failure(
                        self.provider_id,
                        ErrorCode.EVIDENCE_CUTOFF_VIOLATION,
                        retryable=False,
                        message=message,
                    ),
                ),
            )
        events: list[EventRecord] = []
        evidence_values: list[EvidenceItem] = []
        observations: list[SourceObservation] = []
        for source_index, source_url in enumerate(item.official_sources):
            parsed = urllib.parse.urlsplit(source_url)
            allowed_hosts = self._policy.allowed_hosts_by_adapter
            company_hosts = () if allowed_hosts is None else allowed_hosts.get("company_ir", ())
            if (
                parsed.hostname not in company_hosts
                or parsed.hostname not in self._policy.allowed_https_domains
            ):
                message = "configured company IR source is outside the approved domain"
                return EventCollection(
                    provider=self.provider_id,
                    events=tuple(events),
                    evidence=tuple(evidence_values),
                    source_observations=tuple(observations),
                    source_health=(
                        unavailable(
                            self.provider_id,
                            True,
                            ErrorCode.CONFIGURATION_INVALID,
                            message,
                        ),
                    ),
                    failures=(
                        failure(
                            self.provider_id,
                            ErrorCode.CONFIGURATION_INVALID,
                            retryable=False,
                            message=message,
                        ),
                    ),
                )
            try:
                request = request_for_url(
                    "company_ir",
                    source_url,
                    self._policy,
                    accepted_content_types=("text/html",),
                )
                response = self._http_client.request(
                    request,
                    deadline=deadline(now, self._policy),
                )
                if not 200 <= response.status_code < 300:
                    raise RequestRejected("company IR response was not successful")
                parser = _IrParser()
                parser.feed(response.content.decode("utf-8", errors="replace"))
                text = sanitize_external_text(
                    response.content,
                    response.content_type,
                    min(500, self._policy.excerpt_limits.get("text/html", 500)),
                ).text
                identity = self._company_names.get(item.symbol, item.symbol)
                if parser.event_time is None:
                    matches = _ISO.findall(text)
                    parser.event_time = matches[0] if matches else None
                if parser.event_time is None:
                    evidence_id = f"company-ir-evidence-{item.symbol}-{source_index}"
                    observation = SourceObservation(
                        observation_id=f"company-ir-observation-{item.symbol}-{source_index}",
                        provider=self.provider_id,
                        source_url=source_url,
                        source_hash_sha256=digest(response.content),
                        observed_at=now,
                        retrieved_at=now,
                        content_type=response.content_type,
                        excerpt=text,
                        persistence_allowed=self._policy.licensed_content_persistence != "NONE",
                        quality_flags=("UNVERIFIED_CONTENT",),
                    )
                    evidence_values.append(
                        EvidenceItem(
                            evidence_id=evidence_id,
                            source=observation,
                            authority_tier=2,
                            instrument_id=item.symbol,
                            event_time=None,
                            published_time=None,
                            structured_fields=FrozenMap(
                                {
                                    "symbol": item.symbol,
                                    "company_name": identity,
                                    "unverified": True,
                                }
                            ),
                            citation_label=f"Official {item.symbol} investor relations",
                        )
                    )
                    observations.append(observation)
                    continue
                event_time = _timestamp(parser.event_time)
                published_time = (
                    _timestamp(parser.published_time) if parser.published_time else None
                )
                if published_time is not None and (
                    published_time > cutoff_at or published_time > now
                ):
                    raise EvidenceCutoffViolation("company IR content is after the evidence cutoff")
                if not start <= event_time < end:
                    continue
                verified = identity.casefold() in text.casefold()
                evidence_id = f"company-ir-evidence-{item.symbol}-{source_index}"
                observation = SourceObservation(
                    observation_id=f"company-ir-observation-{item.symbol}-{source_index}",
                    provider=self.provider_id,
                    source_url=source_url,
                    source_hash_sha256=digest(response.content),
                    observed_at=now,
                    retrieved_at=now,
                    content_type=response.content_type,
                    excerpt=text,
                    persistence_allowed=self._policy.licensed_content_persistence != "NONE",
                    quality_flags=() if verified else ("UNVERIFIED_CONTENT",),
                )
                evidence = EvidenceItem(
                    evidence_id=evidence_id,
                    source=observation,
                    authority_tier=1 if verified else 2,
                    instrument_id=item.symbol,
                    event_time=event_time,
                    published_time=published_time,
                    structured_fields=FrozenMap({"symbol": item.symbol, "company_name": identity}),
                    citation_label=f"Official {item.symbol} investor relations",
                )
                evidence_values.append(evidence)
                observations.append(observation)
                events.append(
                    EventRecord(
                        event_id=f"event-{evidence_id}",
                        event_type="COMPANY_RELEASE",
                        subject_symbol=item.symbol,
                        event_time=event_time,
                        verified=verified,
                        materiality="HIGH" if verified else "UNKNOWN",
                        supporting_evidence_ids=(evidence_id,),
                        conflict_evidence_ids=(),
                    )
                )
            except RequestTransportUnavailable:
                code, retryable = ErrorCode.PROVIDER_UNAVAILABLE, True
                message = "configured company IR transport is unavailable"
                return EventCollection(
                    provider=self.provider_id,
                    events=tuple(events),
                    evidence=tuple(evidence_values),
                    source_observations=tuple(observations),
                    source_health=(unavailable(self.provider_id, True, code, message),),
                    failures=(
                        failure(self.provider_id, code, retryable=retryable, message=message),
                    ),
                )
            except RequestDeadlineExceeded:
                code, retryable = ErrorCode.DEADLINE_EXCEEDED, True
                message = "configured company IR request deadline was exceeded"
                return EventCollection(
                    provider=self.provider_id,
                    events=tuple(events),
                    evidence=tuple(evidence_values),
                    source_observations=tuple(observations),
                    source_health=(unavailable(self.provider_id, True, code, message),),
                    failures=(
                        failure(self.provider_id, code, retryable=retryable, message=message),
                    ),
                )
            except EvidenceCutoffViolation:
                code, retryable = ErrorCode.EVIDENCE_CUTOFF_VIOLATION, False
                message = "configured company IR content crossed the evidence cutoff"
                return EventCollection(
                    provider=self.provider_id,
                    events=tuple(events),
                    evidence=tuple(evidence_values),
                    source_observations=tuple(observations),
                    source_health=(unavailable(self.provider_id, True, code, message),),
                    failures=(
                        failure(self.provider_id, code, retryable=retryable, message=message),
                    ),
                )
            except RequestRejected:
                code, retryable = ErrorCode.INVALID_RESPONSE, False
                message = "configured company IR request or response violated policy"
                return EventCollection(
                    provider=self.provider_id,
                    events=tuple(events),
                    evidence=tuple(evidence_values),
                    source_observations=tuple(observations),
                    source_health=(unavailable(self.provider_id, True, code, message),),
                    failures=(
                        failure(self.provider_id, code, retryable=retryable, message=message),
                    ),
                )
            except (ValueError, TypeError):
                code, retryable = ErrorCode.INVALID_RESPONSE, False
                message = "configured company IR content was invalid or post-cutoff"
                return EventCollection(
                    provider=self.provider_id,
                    events=tuple(events),
                    evidence=tuple(evidence_values),
                    source_observations=tuple(observations),
                    source_health=(
                        unavailable(
                            self.provider_id,
                            True,
                            code,
                            message,
                        ),
                    ),
                    failures=(
                        failure(self.provider_id, code, retryable=retryable, message=message),
                    ),
                )
        return EventCollection(
            provider=self.provider_id,
            events=tuple(events),
            evidence=tuple(evidence_values),
            source_observations=tuple(observations),
            source_health=(
                available(self.provider_id, True, empty_valid=not events and not evidence_values),
            ),
        )

    def collect_events(
        self,
        symbols: Sequence[str] | WatchlistItem,
        start: datetime,
        end: datetime,
        cutoff_at: datetime,
    ) -> EventCollection:
        if isinstance(symbols, WatchlistItem):
            return self.collect_for_item(symbols, start=start, end=end, cutoff_at=cutoff_at)
        requested = set(symbols)
        items = tuple(item for item in self._watchlist_items if item.symbol in requested)
        if not items:
            message = "company IR watchlist items are not configured"
            return EventCollection(
                provider=self.provider_id,
                source_health=(
                    unavailable(
                        self.provider_id,
                        True,
                        ErrorCode.CONFIGURATION_INVALID,
                        message,
                    ),
                ),
                failures=(
                    failure(
                        self.provider_id,
                        ErrorCode.CONFIGURATION_INVALID,
                        retryable=False,
                        message=message,
                    ),
                ),
            )
        collections = tuple(
            self.collect_for_item(item, start=start, end=end, cutoff_at=cutoff_at)
            for item in items
        )
        return EventCollection(
            provider=self.provider_id,
            events=tuple(event for collection in collections for event in collection.events),
            evidence=tuple(item for collection in collections for item in collection.evidence),
            source_observations=tuple(
                observation
                for collection in collections
                for observation in collection.source_observations
            ),
            source_health=tuple(
                health for collection in collections for health in collection.source_health
            ),
            failures=tuple(
                failure_item
                for collection in collections
                for failure_item in collection.failures
            ),
        )
