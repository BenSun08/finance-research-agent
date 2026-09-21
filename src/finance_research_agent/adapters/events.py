"""Deterministic composition of event-provider results."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from finance_research_agent.application.ports import EventProvider
from finance_research_agent.domain.errors import ErrorCode
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

    provider_id = "composite"

    def __init__(self, providers: Sequence[EventProvider]) -> None:
        invalid = tuple(
            provider for provider in providers if not isinstance(provider, EventProvider)
        )
        if invalid:
            raise TypeError("all event providers must implement EventProvider")
        self._providers = tuple(providers)

    def collect_events(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        cutoff_at: datetime,
    ) -> EventCollection:
        collections: list[EventCollection] = []
        source_health: list[SourceHealth] = []
        failures: list[ProviderFailure] = []
        for provider in self._providers:
            try:
                value = provider.collect_events(symbols, start, end, cutoff_at)
                if not isinstance(value, EventCollection):
                    raise TypeError("event provider must return EventCollection")
            except Exception as error:
                message = f"event provider failed: {type(error).__name__}"[:256]
                source_health.append(
                    SourceHealth(
                        provider=provider.provider_id,
                        available=False,
                        required=False,
                        error_code=ErrorCode.INTERNAL_ERROR,
                        message=message,
                    )
                )
                failures.append(
                    ProviderFailure(
                        provider=provider.provider_id,
                        error_code=ErrorCode.INTERNAL_ERROR,
                        retryable=False,
                        message=message,
                    )
                )
                continue
            collections.append(value)
            source_health.extend(value.source_health)
            failures.extend(value.failures)

        evidence: list[EvidenceItem] = []
        observations: list[SourceObservation] = []
        grouped: dict[tuple[str, str | None, datetime], list[EventRecord]] = {}
        for collection in collections:
            for item in collection.evidence:
                if item not in evidence:
                    evidence.append(item)
            for observation in collection.source_observations:
                if observation not in observations:
                    observations.append(observation)
            for event in collection.events:
                grouped.setdefault(
                    (event.event_type, event.subject_symbol, event.event_time), []
                ).append(event)

        events: list[EventRecord] = []
        materiality_rank = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        for key, grouped_records in grouped.items():
            records: list[EventRecord] = []
            for record in grouped_records:
                if record not in records:
                    records.append(record)
            supporting_ids = tuple(
                sorted(
                    {
                        evidence_id
                        for record in records
                        for evidence_id in record.supporting_evidence_ids
                    }
                )
            )
            explicit_conflicts = {
                evidence_id
                for record in records
                for evidence_id in record.conflict_evidence_ids
            }
            competing_values = {
                (record.verified, record.materiality) for record in records
            }
            conflict_ids = set(explicit_conflicts)
            if len(competing_values) > 1:
                conflict_ids.update(supporting_ids)
            first = min(records, key=lambda record: record.event_id)
            events.append(
                EventRecord(
                    event_id=first.event_id,
                    event_type=key[0],
                    subject_symbol=key[1],
                    event_time=key[2],
                    verified=any(record.verified for record in records),
                    materiality=max(
                        (record.materiality for record in records),
                        key=lambda value: materiality_rank[value],
                    ),
                    supporting_evidence_ids=supporting_ids,
                    conflict_evidence_ids=tuple(sorted(conflict_ids)),
                )
            )
        return EventCollection(
            provider=self.provider_id,
            events=tuple(
                sorted(
                    events,
                    key=lambda event: (event.event_time, event.event_type, event.event_id),
                )
            ),
            evidence=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
            source_observations=tuple(
                sorted(observations, key=lambda item: item.observation_id)
            ),
            source_health=tuple(
                sorted(
                    source_health,
                    key=lambda item: (
                        item.provider,
                        item.error_code.value if item.error_code else "",
                        item.message,
                    ),
                )
            ),
            failures=tuple(
                sorted(
                    failures,
                    key=lambda item: (
                        item.provider,
                        item.symbol or "",
                        item.error_code.value,
                        item.message,
                    ),
                )
            ),
        )
