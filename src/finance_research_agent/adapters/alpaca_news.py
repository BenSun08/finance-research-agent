"""Bounded Alpaca news discovery; never an authority-bearing event source."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

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
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.settings import Settings

Clock = Callable[[], datetime]
NEWS_HOST = "data.alpaca.markets"


class _NewsItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: int | str
    headline: str
    summary: str | None = None
    author: str | None = None
    created_at: str
    updated_at: str
    url: str
    symbols: list[str]


class _NewsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    news: list[_NewsItem]


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("news timestamp must be UTC")
    return parsed


class AlpacaNewsDiscoveryAdapter:
    """Collect licensed bounded metadata with scoped optional-source health."""

    def __init__(
        self,
        http_client: SafeHttpClient,
        source_policy: SourcePolicy,
        *,
        settings: Settings,
        clock: Clock = lambda: datetime.now(UTC),
        host: str = NEWS_HOST,
    ) -> None:
        self._http_client = http_client
        self._policy = source_policy
        self._settings = settings
        self._clock = clock
        self._host = host

    def collect_events(
        self, *, symbols: tuple[str, ...], start: datetime, end: datetime
    ) -> EventCollection:
        now = self._clock()
        try:
            request = request_for_url(
                "alpaca_news",
                f"https://{self._host}/v1beta1/news",
                self._policy,
                accepted_content_types=("application/json",),
            ).model_copy(
                update={
                    "query": FrozenMap(
                        {
                            "symbols": ",".join(symbols),
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "limit": "50",
                        }
                    )
                }
            )
            credentials = None
            if (
                self._settings.alpaca_api_key is not None
                and self._settings.alpaca_api_secret is not None
            ):
                credentials = (
                    self._settings.alpaca_api_key.get_secret_value(),
                    self._settings.alpaca_api_secret.get_secret_value(),
                )
            response = self._http_client.request(
                request,
                deadline=deadline(now, self._policy),
                provider_credentials=credentials,
            )
            if not 200 <= response.status_code < 300:
                raise RequestRejected("Alpaca news response was not successful")
            payload = _NewsPayload.model_validate_json(response.content)
        except (RequestRejected, ValueError, TypeError):
            return EventCollection(
                provider="alpaca_news",
                source_health=(
                    unavailable(
                        "alpaca_news",
                        False,
                        ErrorCode.PROVIDER_UNAVAILABLE,
                        "news discovery is unavailable",
                    ),
                ),
            )

        events: list[EventRecord] = []
        evidence: list[EvidenceItem] = []
        observations: list[SourceObservation] = []
        excerpt_limit = self._policy.excerpt_limits.get("application/json", 500)
        for item in payload.news:
            if not set(item.symbols).intersection(symbols):
                continue
            try:
                event_time = _timestamp(item.created_at)
                updated_at = _timestamp(item.updated_at)
                if not start <= event_time < end or event_time > now or updated_at > now:
                    continue
                source_hash = digest(response.content)
                evidence_id = f"alpaca-news-evidence-{item.id}"
                observation = SourceObservation(
                    observation_id=f"alpaca-news-observation-{item.id}",
                    provider="alpaca_news",
                    source_url=item.url,
                    source_hash_sha256=source_hash,
                    observed_at=event_time,
                    retrieved_at=now,
                    content_type=response.content_type,
                    excerpt=sanitize_external_text(
                        f"{item.headline}. {item.summary or ''}".encode(),
                        "text/plain",
                        min(excerpt_limit, 500),
                    ).text,
                    persistence_allowed=self._policy.licensed_content_persistence == "ALLOWED",
                    quality_flags=("DISCOVERY_ONLY",),
                )
                item_evidence = EvidenceItem(
                    evidence_id=evidence_id,
                    source=observation,
                    authority_tier=2,
                    instrument_id=item.symbols[0] if item.symbols else None,
                    event_time=event_time,
                    published_time=updated_at,
                    structured_fields=FrozenMap(
                        {
                            "headline": item.headline[:512],
                            "author": item.author or "",
                            "discovery_only": True,
                        }
                    ),
                    citation_label="Alpaca news discovery",
                )
            except (ValueError, TypeError):
                continue
            evidence.append(item_evidence)
            observations.append(observation)
            events.append(
                EventRecord(
                    event_id=f"event-{evidence_id}",
                    event_type="NEWS_DISCOVERY",
                    subject_symbol=item.symbols[0] if item.symbols else None,
                    event_time=event_time,
                    verified=False,
                    materiality="UNKNOWN",
                    supporting_evidence_ids=(evidence_id,),
                    conflict_evidence_ids=(),
                )
            )
        return EventCollection(
            provider="alpaca_news",
            events=tuple(sorted(events, key=lambda item: (item.event_time, item.event_id))),
            evidence=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
            source_observations=tuple(sorted(observations, key=lambda item: item.observation_id)),
            source_health=(available("alpaca_news", False, empty_valid=not events),),
        )
