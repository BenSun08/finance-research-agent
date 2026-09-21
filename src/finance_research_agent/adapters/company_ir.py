"""Fail-closed verification of configured company investor-relations sources."""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from html.parser import HTMLParser

from finance_research_agent.adapters._event_common import (
    available,
    deadline,
    digest,
    request_for_url,
    unavailable,
)
from finance_research_agent.adapters.http_client import (
    RequestRejected,
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

    def __init__(
        self,
        http_client: SafeHttpClient,
        source_policy: SourcePolicy,
        *,
        company_names: Mapping[str, str] | None = None,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._http_client = http_client
        self._policy = source_policy
        self._company_names = dict(company_names or {})
        self._clock = clock

    def collect_events(
        self,
        item: WatchlistItem,
        *,
        start: datetime,
        end: datetime,
        cutoff_at: datetime,
    ) -> EventCollection:
        now = self._clock()
        if now > cutoff_at:
            return EventCollection(
                provider="company_ir",
                source_health=(
                    unavailable(
                        "company_ir",
                        True,
                        ErrorCode.EVIDENCE_CUTOFF_VIOLATION,
                        "company IR retrieval is after the evidence cutoff",
                    ),
                ),
            )
        events: list[EventRecord] = []
        evidence_values: list[EvidenceItem] = []
        observations: list[SourceObservation] = []
        for source_index, source_url in enumerate(item.official_sources):
            parsed = urllib.parse.urlsplit(source_url)
            allowed_hosts = self._policy.allowed_hosts_by_adapter
            company_hosts = () if allowed_hosts is None else allowed_hosts["company_ir"]
            if (
                parsed.hostname not in company_hosts
                or parsed.hostname not in self._policy.allowed_https_domains
            ):
                continue
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
                    raise ValueError("company IR content has no parseable event timestamp")
                event_time = _timestamp(parser.event_time)
                published_time = (
                    _timestamp(parser.published_time) if parser.published_time else None
                )
                if published_time is not None and (
                    published_time > cutoff_at or published_time > now
                ):
                    raise ValueError("company IR publication is after the evidence cutoff")
                if not start <= event_time < end:
                    continue
                verified = identity.casefold() in text.casefold()
                evidence_id = f"company-ir-evidence-{item.symbol}-{source_index}"
                observation = SourceObservation(
                    observation_id=f"company-ir-observation-{item.symbol}-{source_index}",
                    provider="company_ir",
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
            except (RequestRejected, ValueError, TypeError):
                return EventCollection(
                    provider="company_ir",
                    events=tuple(events),
                    evidence=tuple(evidence_values),
                    source_observations=tuple(observations),
                    source_health=(
                        unavailable(
                            "company_ir",
                            True,
                            ErrorCode.PROVIDER_UNAVAILABLE,
                            "configured company IR source was unavailable or unverified",
                        ),
                    ),
                )
        return EventCollection(
            provider="company_ir",
            events=tuple(events),
            evidence=tuple(evidence_values),
            source_observations=tuple(observations),
            source_health=(available("company_ir", True, empty_valid=not events),),
        )
