"""Deterministic composition of event-provider results."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from finance_research_agent.domain.models import (
    EventCollection,
    EventRecord,
    EvidenceItem,
    ProviderFailure,
    SourceHealth,
    SourceObservation,
)


class CompositeEventProvider:
    """Combine bounded providers without hiding source outages or conflicts."""

    def __init__(self, providers: Sequence[object]) -> None:
        self._providers = tuple(providers)

    def collect_events(
        self, *, symbols: Sequence[str], start: datetime, end: datetime
    ) -> EventCollection:
        collections: list[EventCollection] = []
        for provider in self._providers:
            collect = getattr(provider, "collect_events", None)
            if not callable(collect):
                continue
            value = collect(symbols=symbols, start=start, end=end)
            if not isinstance(value, EventCollection):
                raise TypeError("event provider must return EventCollection")
            collections.append(value)

        evidence_by_id: dict[str, EvidenceItem] = {}
        observations_by_id: dict[str, SourceObservation] = {}
        health_by_provider: dict[str, SourceHealth] = {}
        failures_by_key: dict[tuple[str, str | None], ProviderFailure] = {}
        grouped: dict[tuple[str, str | None, datetime], list[EventRecord]] = {}
        for collection in collections:
            for evidence in collection.evidence:
                evidence_by_id.setdefault(evidence.evidence_id, evidence)
            for observation in collection.source_observations:
                observations_by_id.setdefault(observation.observation_id, observation)
            for health in collection.source_health:
                health_by_provider[health.provider] = health
            for failure in collection.failures:
                failures_by_key[(failure.provider, failure.symbol)] = failure
            for event in collection.events:
                key = (event.event_type, event.subject_symbol, event.event_time)
                grouped.setdefault(key, []).append(event)

        events: list[EventRecord] = []
        for key, records in grouped.items():
            all_supporting = tuple(
                sorted(
                    {
                        evidence_id
                        for record in records
                        for evidence_id in record.supporting_evidence_ids
                    }
                )
            )
            existing_conflicts = {
                evidence_id for record in records for evidence_id in record.conflict_evidence_ids
            }
            if len(records) > 1:
                existing_conflicts.update(all_supporting)
            verified = any(record.verified for record in records)
            materiality_rank = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
            materiality = max(
                (record.materiality for record in records),
                key=lambda value: materiality_rank[value],
            )
            first = min(records, key=lambda record: record.event_id)
            events.append(
                EventRecord(
                    event_id=first.event_id,
                    event_type=key[0],
                    subject_symbol=key[1],
                    event_time=key[2],
                    verified=verified,
                    materiality=materiality,
                    supporting_evidence_ids=all_supporting,
                    conflict_evidence_ids=tuple(sorted(existing_conflicts)),
                )
            )
        return EventCollection(
            provider="composite",
            events=tuple(
                sorted(
                    events, key=lambda event: (event.event_time, event.event_type, event.event_id)
                )
            ),
            evidence=tuple(sorted(evidence_by_id.values(), key=lambda item: item.evidence_id)),
            source_observations=tuple(
                sorted(observations_by_id.values(), key=lambda item: item.observation_id)
            ),
            source_health=tuple(
                sorted(health_by_provider.values(), key=lambda item: item.provider)
            ),
            failures=tuple(
                sorted(
                    failures_by_key.values(), key=lambda item: (item.provider, item.symbol or "")
                )
            ),
        )
