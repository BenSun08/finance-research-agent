"""Bounded SEC EDGAR filing metadata and evidence normalization."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finance_research_agent.adapters._event_common import (
    available,
    deadline,
    digest,
    request_for_url,
)
from finance_research_agent.adapters.http_client import (
    RequestRejected,
    SafeHttpClient,
    sanitize_external_text,
)
from finance_research_agent.domain.models import (
    EventCollection,
    EventRecord,
    EvidenceItem,
    SourceObservation,
)
from finance_research_agent.domain.policies import SourcePolicy
from finance_research_agent.domain.types import FrozenMap

SEC_PROVIDER = "sec_edgar"
SEC_ADAPTER = "sec_edgar"
SEC_HOST = "data.sec.gov"
SEC_USER_AGENT_MAX_LENGTH = 256


class _SecPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _RecentFilings(_SecPayload):
    accession_number: list[str] = Field(alias="accessionNumber")
    filing_date: list[str] = Field(alias="filingDate")
    report_date: list[str] = Field(alias="reportDate")
    acceptance_datetime: list[str] = Field(alias="acceptanceDateTime")
    form: list[str]
    primary_document: list[str] = Field(alias="primaryDocument")

    @model_validator(mode="after")
    def aligned_rows(self) -> _RecentFilings:
        lengths = {
            len(self.accession_number),
            len(self.filing_date),
            len(self.report_date),
            len(self.acceptance_datetime),
            len(self.form),
            len(self.primary_document),
        }
        if len(lengths) != 1:
            raise ValueError("SEC recent filing fields must have equal lengths")
        return self


class _Filings(_SecPayload):
    recent: _RecentFilings


class _Submissions(_SecPayload):
    cik: str
    name: str
    filings: _Filings


Clock = Callable[[], datetime]


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("SEC timestamp must be UTC")
    return parsed


def _filing_date(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), time.min, tzinfo=UTC)


class SecEdgarAdapter:
    """Collect only bounded, authority-bearing SEC filing metadata."""

    MATERIAL_FORMS: ClassVar[frozenset[str]] = frozenset({"8-K", "10-K", "10-Q", "20-F", "6-K"})

    def __init__(
        self,
        http_client: SafeHttpClient,
        source_policy: SourcePolicy,
        *,
        user_agent: str,
        clock: Clock,
        host: str = SEC_HOST,
        material_forms: frozenset[str] | None = None,
    ) -> None:
        if (
            not user_agent
            or len(user_agent) > SEC_USER_AGENT_MAX_LENGTH
            or any(ord(character) < 32 for character in user_agent)
        ):
            raise ValueError("SEC user_agent must be bounded identifying text")
        self._http_client = http_client
        self._policy = source_policy
        self._user_agent = user_agent
        self._clock = clock
        self._host = host
        self._material_forms = material_forms or self.MATERIAL_FORMS

    def collect_filings(self, *, cik: str, cutoff_at: datetime) -> tuple[EvidenceItem, ...]:
        now = self._clock()
        if now > cutoff_at:
            return ()
        normalized_cik = cik.strip()
        if len(normalized_cik) != 10 or not normalized_cik.isdigit():
            return ()
        url = f"https://{self._host}/submissions/CIK{normalized_cik}.json"
        try:
            request = request_for_url(
                SEC_ADAPTER,
                url,
                self._policy,
                accepted_content_types=("application/json",),
            )
            response = self._http_client.request(
                request,
                deadline=deadline(now, self._policy),
                user_agent=self._user_agent,
            )
            if not 200 <= response.status_code < 300:
                return ()
            payload = _Submissions.model_validate_json(response.content)
        except (RequestRejected, ValueError, TypeError):
            return ()
        if payload.cik != normalized_cik:
            return ()
        excerpt = sanitize_external_text(
            response.content,
            response.content_type,
            min(500, self._policy.excerpt_limits.get("application/json", 500)),
        ).text
        values: list[EvidenceItem] = []
        recent = payload.filings.recent
        for index, form in enumerate(recent.form):
            if form not in self._material_forms:
                continue
            try:
                event_time = _filing_date(recent.filing_date[index])
                published_time = _timestamp(recent.acceptance_datetime[index])
            except (ValueError, IndexError):
                continue
            if (
                event_time > cutoff_at
                or published_time > cutoff_at
                or event_time > now
                or published_time > now
            ):
                continue
            accession = recent.accession_number[index]
            document = recent.primary_document[index]
            accession_path = accession.replace("-", "")
            source_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(normalized_cik)}/"
                f"{accession_path}/{document}"
            )
            observation_id = f"sec-observation-{normalized_cik}-{accession}"
            evidence_id = f"sec-evidence-{normalized_cik}-{accession}"
            source = SourceObservation(
                observation_id=observation_id,
                provider=SEC_PROVIDER,
                source_url=source_url,
                source_hash_sha256=digest(response.content),
                observed_at=published_time,
                retrieved_at=now,
                content_type=response.content_type,
                excerpt=excerpt,
                persistence_allowed=self._policy.licensed_content_persistence != "NONE",
                quality_flags=(),
            )
            values.append(
                EvidenceItem(
                    evidence_id=evidence_id,
                    source=source,
                    authority_tier=1,
                    instrument_id=None,
                    event_time=event_time,
                    published_time=published_time,
                    structured_fields=FrozenMap(
                        {
                            "accession_number": accession,
                            "cik": normalized_cik,
                            "form": form,
                            "filing_date": recent.filing_date[index],
                            "primary_document": document,
                        }
                    ),
                    citation_label=f"SEC {form} {accession}",
                )
            )
        return tuple(values)

    def collect_events(
        self, *, cik: str, cutoff_at: datetime, symbol: str | None = None
    ) -> EventCollection:
        evidence = self.collect_filings(cik=cik, cutoff_at=cutoff_at)
        events = tuple(
            EventRecord(
                event_id=f"event-{item.evidence_id}",
                event_type="SEC_FILING",
                subject_symbol=symbol,
                event_time=item.event_time or cutoff_at,
                verified=True,
                materiality="HIGH",
                supporting_evidence_ids=(item.evidence_id,),
                conflict_evidence_ids=(),
            )
            for item in evidence
        )
        return EventCollection(
            provider=SEC_PROVIDER,
            events=events,
            evidence=evidence,
            source_observations=tuple(item.source for item in evidence),
            source_health=(available(SEC_PROVIDER, True, empty_valid=not evidence),),
        )
