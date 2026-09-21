"""TDD regressions for the Task 8 boundary fix round."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
from test_official_sources import CUTOFF, FIXTURES, _client, _policy, _response

from finance_research_agent.adapters.alpaca_news import AlpacaNewsDiscoveryAdapter
from finance_research_agent.adapters.company_ir import CompanyIrAdapter
from finance_research_agent.adapters.events import CompositeEventProvider
from finance_research_agent.adapters.macro import MacroCalendarAdapter, MacroCalendarSource
from finance_research_agent.adapters.sec import SecEdgarAdapter
from finance_research_agent.application.ports import EventProvider
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    EventCollection,
    EventRecord,
    EvidenceItem,
    SourceHealth,
)
from finance_research_agent.domain.policies import WatchlistItem


def _window() -> tuple[datetime, datetime]:
    return datetime(2026, 8, 19, tzinfo=UTC), datetime(2026, 8, 20, tzinfo=UTC)


def _company_item(source: str = "https://ir.example.com/releases/aapl.html") -> WatchlistItem:
    return WatchlistItem(
        symbol="AAPL",
        role="CORE_MONITOR",
        research_rationale="Apple research",
        official_sources=(source,),
    )


def test_event_provider_requires_uniform_cutoff_and_all_sources_implement_it() -> None:
    from finance_research_agent.settings import Settings

    assert isinstance(
        MacroCalendarAdapter(_client(lambda request: httpx.Response(200)), _policy()),
        EventProvider,
    )
    assert isinstance(
        AlpacaNewsDiscoveryAdapter(
            _client(lambda request: httpx.Response(200)),
            _policy(),
            settings=Settings(data_dir=Path("data")),
        ),
        EventProvider,
    )


def test_macro_retrieval_after_cutoff_is_unavailable_not_empty_valid() -> None:
    source = MacroCalendarSource(
        provider="federal_reserve",
        url="https://www.federalreserve.gov/releases/calendar.htm",
    )
    start, end = _window()
    adapter = MacroCalendarAdapter(
        _client(
            _response(
                "/releases/calendar.htm",
                (FIXTURES / "macro/fed-calendar.html").read_bytes(),
                "text/html",
            ),
            clock=datetime(2026, 8, 19, 13, 0, tzinfo=UTC),
        ),
        _policy(),
        sources=(source,),
        clock=lambda: datetime(2026, 8, 19, 13, 0, tzinfo=UTC),
    )
    result = adapter.collect_events((), start, end, CUTOFF)
    assert result.events == ()
    assert result.health.error_code == "EVIDENCE_CUTOFF_VIOLATION"
    assert result.health.empty_valid is False


def test_news_retrieval_after_cutoff_is_unavailable_not_empty_valid() -> None:
    from finance_research_agent.settings import Settings

    start, end = _window()
    now = datetime(2026, 8, 19, 13, 0, tzinfo=UTC)
    adapter = AlpacaNewsDiscoveryAdapter(
        _client(
            _response(
                "/v1beta1/news",
                (FIXTURES / "alpaca/news.json").read_bytes(),
                "application/json",
            ),
            clock=now,
        ),
        _policy(),
        settings=Settings(data_dir=Path("data")),
        clock=lambda: now,
    )
    result = adapter.collect_events(("AAPL",), start, end, CUTOFF)
    assert result.events == ()
    assert result.health.error_code == "EVIDENCE_CUTOFF_VIOLATION"
    assert result.health.empty_valid is False


def test_sec_transport_failure_is_typed_and_not_empty_valid() -> None:
    def transport_failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    start, end = _window()
    adapter = SecEdgarAdapter(
        _client(transport_failure),
        _policy(),
        user_agent="finance-research-agent/0.5 contact@example.test",
        clock=lambda: CUTOFF,
        cik_by_symbol={"AAPL": "0000320193"},
    )
    result = adapter.collect_events(("AAPL",), start, end, CUTOFF)
    assert result.health.available is False
    assert result.health.empty_valid is False
    assert result.health.error_code == "PROVIDER_UNAVAILABLE"
    assert result.failures[0].error_code == "PROVIDER_UNAVAILABLE"
    assert result.failures[0].retryable is True


def test_sec_empty_material_form_configuration_is_preserved() -> None:
    payload = (FIXTURES / "sec/submissions.json").read_bytes()
    start, end = _window()
    adapter = SecEdgarAdapter(
        _client(_response("/submissions/CIK0000320193.json", payload, "application/json")),
        _policy(),
        user_agent="finance-research-agent/0.5 contact@example.test",
        clock=lambda: CUTOFF,
        material_forms=frozenset(),
        cik_by_symbol={"AAPL": "0000320193"},
    )
    result = adapter.collect_events(("AAPL",), start, end, CUTOFF)
    assert result.events == ()
    assert result.health.available is True
    assert result.health.empty_valid is True
    assert result.failures == ()


def test_sec_malformed_material_row_is_typed_schema_failure() -> None:
    payload = json.loads((FIXTURES / "sec/submissions.json").read_text())
    payload["filings"]["recent"]["filingDate"][0] = "not-a-date"
    start, end = _window()
    adapter = SecEdgarAdapter(
        _client(
            _response(
                "/submissions/CIK0000320193.json",
                json.dumps(payload).encode(),
                "application/json",
            )
        ),
        _policy(),
        user_agent="finance-research-agent/0.5 contact@example.test",
        clock=lambda: CUTOFF,
        cik_by_symbol={"AAPL": "0000320193"},
    )
    result = adapter.collect_events(("AAPL",), start, end, CUTOFF)
    assert result.events == ()
    assert result.evidence == ()
    assert result.health.available is False
    assert result.health.empty_valid is False
    assert result.health.error_code == ErrorCode.PROVIDER_SCHEMA_DRIFT
    assert result.failures[0].error_code == ErrorCode.PROVIDER_SCHEMA_DRIFT
    assert result.failures[0].retryable is False


def test_macro_transport_failure_retains_transport_failure_code() -> None:
    source = MacroCalendarSource(
        provider="federal_reserve",
        url="https://www.federalreserve.gov/releases/calendar.htm",
    )

    def transport_failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    start, end = _window()
    adapter = MacroCalendarAdapter(
        _client(transport_failure), _policy(), sources=(source,), clock=lambda: CUTOFF
    )
    result = adapter.collect_events((), start, end, CUTOFF)
    assert result.health.error_code == "PROVIDER_UNAVAILABLE"
    assert result.failures[0].retryable is True


def test_news_multi_symbol_article_emits_only_requested_symbols() -> None:
    from finance_research_agent.settings import Settings

    payload = b'{"news":[{"id":1,"headline":"Two symbols","summary":"x","author":"a","created_at":"2026-08-19T12:30:00Z","updated_at":"2026-08-19T12:31:00Z","url":"https://news.alpaca.markets/articles/1","symbols":["MSFT","AAPL"]}]}'
    start, end = _window()
    adapter = AlpacaNewsDiscoveryAdapter(
        _client(_response("/v1beta1/news", payload, "application/json")),
        _policy(),
        settings=Settings(data_dir=Path("data")),
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(("AAPL",), start, end, CUTOFF)
    assert [event.subject_symbol for event in result.events] == ["AAPL"]


def test_company_ir_missing_timestamp_returns_unverified_evidence() -> None:
    payload = b"<html><body><h1>Apple Inc. release</h1><p>No timestamp.</p></body></html>"
    start, end = _window()
    adapter = CompanyIrAdapter(
        _client(_response("/releases/aapl.html", payload, "text/html")),
        _policy(),
        watchlist_items=(_company_item(),),
        company_names={"AAPL": "Apple Inc."},
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(("AAPL",), start, end, CUTOFF)
    assert result.events == ()
    assert result.evidence
    assert result.evidence[0].event_time is None
    assert result.evidence[0].authority_tier > 1
    assert result.evidence[0].source.quality_flags == ("UNVERIFIED_CONTENT",)
    assert result.health.available is True
    assert result.health.empty_valid is False


def test_company_ir_disallowed_configured_url_is_invalid_health() -> None:
    start, end = _window()
    adapter = CompanyIrAdapter(
        _client(lambda request: httpx.Response(200)),
        _policy(),
        watchlist_items=(_company_item("https://unconfigured.example/releases/aapl.html"),),
        company_names={"AAPL": "Apple Inc."},
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(("AAPL",), start, end, CUTOFF)
    assert result.events == ()
    assert result.health.available is False
    assert result.health.empty_valid is False
    assert result.health.error_code == "CONFIGURATION_INVALID"


def test_malformed_news_item_is_invalid_health_not_empty_valid() -> None:
    from finance_research_agent.settings import Settings

    payload = b'{"news":[{"id":1,"headline":"Bad timestamp","summary":"x","author":"a","created_at":"bad","updated_at":"bad","url":"https://news.alpaca.markets/articles/1","symbols":["AAPL"]}]}'
    start, end = _window()
    adapter = AlpacaNewsDiscoveryAdapter(
        _client(_response("/v1beta1/news", payload, "application/json")),
        _policy(),
        settings=Settings(data_dir=Path("data")),
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(("AAPL",), start, end, CUTOFF)
    assert result.events == ()
    assert result.health.available is False
    assert result.health.empty_valid is False
    assert result.health.error_code == "INVALID_RESPONSE"


def test_composite_preserves_health_and_only_marks_real_conflicts() -> None:
    start, end = _window()

    def evidence(evidence_id: str, provider: str) -> EvidenceItem:
        return EvidenceItem(
            evidence_id=evidence_id,
            source={
                "observation_id": f"obs-{evidence_id}",
                "provider": provider,
                "source_url": "https://www.sec.gov/a",
                "source_hash_sha256": "a" * 64,
                "observed_at": CUTOFF,
                "retrieved_at": CUTOFF,
                "content_type": "application/json",
                "excerpt": provider,
                "persistence_allowed": True,
                "quality_flags": (),
            },
            authority_tier=1,
            instrument_id="AAPL",
            event_time=CUTOFF,
            published_time=CUTOFF,
            structured_fields={},
            citation_label=provider,
        )

    class FakeProvider:
        def __init__(self, provider_id: str, verified: bool, health: SourceHealth) -> None:
            self.provider_id = provider_id
            item = evidence(f"ev-{provider_id}", provider_id)
            self.collection = EventCollection(
                provider=provider_id,
                events=(
                    EventRecord(
                        event_id="same-event",
                        event_type="EARNINGS",
                        subject_symbol="AAPL",
                        event_time=CUTOFF,
                        verified=verified,
                        materiality="HIGH",
                        supporting_evidence_ids=(item.evidence_id,),
                        conflict_evidence_ids=(),
                    ),
                ),
                evidence=(item,),
                source_observations=(item.source,),
                source_health=(health,),
            )

        def collect_events(
            self,
            symbols: Sequence[str],
            start: datetime,
            end: datetime,
            cutoff_at: datetime,
        ) -> EventCollection:
            assert cutoff_at == CUTOFF
            return self.collection

    first = FakeProvider(
        "source-a",
        True,
        SourceHealth(provider="source-a", available=True, required=False),
    )
    second = FakeProvider(
        "source-b",
        True,
        SourceHealth(
            provider="source-b",
            available=False,
            required=True,
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
        ),
    )
    assert isinstance(first, EventProvider)
    corroborated = CompositeEventProvider((first, second)).collect_events(
        ("AAPL",), start, end, CUTOFF
    )
    assert len(corroborated.source_health) == 2
    assert corroborated.events[0].conflict_evidence_ids == ()

    conflicting = CompositeEventProvider(
        (
            first,
            FakeProvider(
                "source-c",
                False,
                SourceHealth(provider="source-c", available=True, required=False),
            ),
        )
    ).collect_events(("AAPL",), start, end, CUTOFF)
    assert conflicting.events[0].conflict_evidence_ids == ("ev-source-a", "ev-source-c")
