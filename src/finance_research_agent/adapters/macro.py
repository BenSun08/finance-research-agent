"""Official macro-calendar parsing with explicit per-source health."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

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
    SourceHealth,
    SourceObservation,
)
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap

Clock = Callable[[], datetime]
_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})"
)


class MacroCalendarSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    provider: str = Field(min_length=1, max_length=64)
    url: HttpUrl
    timezone: str = "America/New_York"
    required: bool = True
    materiality: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"] = "HIGH"
    event_type: str = "MACRO_RELEASE"


class _CalendarRow:
    def __init__(self, attributes: dict[str, str]) -> None:
        self.attributes = attributes
        self.text: list[str] = []


class _CalendarParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_CalendarRow] = []
        self._row: _CalendarRow | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "tr":
            self._row = _CalendarRow({name.casefold(): value or "" for name, value in attrs})

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._row is not None:
            self._row.text.append(data)


def _parse_timestamp(value: str, timezone: str) -> datetime:
    candidate = value.strip()
    if not candidate:
        raise ValueError("macro event timestamp is missing")
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
        return parsed.astimezone(UTC)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("macro event timestamp is invalid") from error


class MacroCalendarAdapter:
    """Parse configured official macro pages without treating HTML as instructions."""

    def __init__(
        self,
        http_client: SafeHttpClient,
        source_policy: SourcePolicy,
        *,
        sources: tuple[MacroCalendarSource, ...] = (),
        calendars: tuple[MacroCalendarSource, ...] | None = None,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._http_client = http_client
        self._policy = source_policy
        self._sources = sources if calendars is None else calendars
        self._clock = clock

    def collect_events(
        self, *, symbols: Iterable[str], start: datetime, end: datetime
    ) -> EventCollection:
        del symbols
        all_events: list[EventRecord] = []
        all_evidence: list[EvidenceItem] = []
        health: list[SourceHealth] = []
        observations: list[SourceObservation] = []
        for source in self._sources:
            provider = source.provider
            now = self._clock()
            try:
                request = request_for_url(
                    "macro",
                    str(source.url),
                    self._policy,
                    accepted_content_types=("text/html",),
                )
                response = self._http_client.request(
                    request,
                    deadline=deadline(now, self._policy),
                )
                if not 200 <= response.status_code < 300:
                    health.append(
                        unavailable(
                            provider,
                            source.required,
                            ErrorCode.PROVIDER_UNAVAILABLE,
                            "official macro calendar was unavailable",
                        )
                    )
                    continue
                parser = _CalendarParser()
                parser.feed(response.content.decode("utf-8", errors="replace"))
                excerpt = sanitize_external_text(
                    response.content,
                    response.content_type,
                    min(500, self._policy.excerpt_limits.get("text/html", 500)),
                ).text
                for row_index, row in enumerate(parser.rows):
                    raw_event_time = row.attributes.get("data-event-time", "")
                    if not raw_event_time:
                        matches = _ISO_TIMESTAMP.findall(" ".join(row.text))
                        raw_event_time = matches[0] if matches else ""
                    event_time = _parse_timestamp(raw_event_time, source.timezone)
                    if not start <= event_time < end:
                        continue
                    raw_published = row.attributes.get("data-published", "")
                    published_time = (
                        _parse_timestamp(raw_published, "UTC") if raw_published else None
                    )
                    if published_time is not None and published_time > now:
                        raise ValueError("macro publication is after retrieval")
                    evidence_id = f"macro-evidence-{provider}-{row_index}"
                    observation = SourceObservation(
                        observation_id=f"macro-observation-{provider}-{row_index}",
                        provider=provider,
                        source_url=str(source.url),
                        source_hash_sha256=digest(response.content),
                        observed_at=now,
                        retrieved_at=now,
                        content_type=response.content_type,
                        excerpt=excerpt,
                        persistence_allowed=self._policy.licensed_content_persistence != "NONE",
                        quality_flags=(),
                    )
                    evidence = EvidenceItem(
                        evidence_id=evidence_id,
                        source=observation,
                        authority_tier=1,
                        instrument_id=None,
                        event_time=event_time,
                        published_time=published_time,
                        structured_fields=FrozenMap(
                            {
                                "provider": provider,
                                "title": " ".join(row.text).strip()[:256]
                                or "Official macro event",
                            }
                        ),
                        citation_label=f"Official {provider} macro calendar",
                    )
                    all_evidence.append(evidence)
                    observations.append(observation)
                    all_events.append(
                        EventRecord(
                            event_id=f"event-{evidence_id}",
                            event_type=source.event_type,
                            subject_symbol=None,
                            event_time=event_time,
                            verified=True,
                            materiality=source.materiality,
                            supporting_evidence_ids=(evidence_id,),
                            conflict_evidence_ids=(),
                        )
                    )
                health.append(
                    available(
                        provider,
                        source.required,
                        empty_valid=not any(
                            item.source.provider == provider for item in all_evidence
                        ),
                    )
                )
            except (RequestRejected, ValueError, TypeError):
                health.append(
                    unavailable(
                        provider,
                        source.required,
                        ErrorCode.INVALID_RESPONSE,
                        "official macro calendar was unavailable or invalid",
                    )
                )
        return EventCollection(
            provider="macro",
            events=tuple(sorted(all_events, key=lambda item: (item.event_time, item.event_id))),
            evidence=tuple(sorted(all_evidence, key=lambda item: item.evidence_id)),
            source_observations=tuple(sorted(observations, key=lambda item: item.observation_id)),
            source_health=tuple(sorted(health, key=lambda item: item.provider)),
        )
