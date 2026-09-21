"""Offline contract tests for R4 official and discovery event evidence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from finance_research_agent.adapters.alpaca_news import AlpacaNewsDiscoveryAdapter
from finance_research_agent.adapters.company_ir import CompanyIrAdapter
from finance_research_agent.adapters.http_client import SafeHttpClient
from finance_research_agent.adapters.macro import MacroCalendarAdapter, MacroCalendarSource
from finance_research_agent.adapters.sec import SecEdgarAdapter
from finance_research_agent.domain.models import EventRecord, EvidenceItem, SourceHealth
from finance_research_agent.domain.policies import SourcePolicy, WatchlistItem
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.settings import Settings

FIXTURES = Path(__file__).parents[1] / "fixtures"
CUTOFF = datetime(2026, 8, 19, 12, 58, tzinfo=UTC)


def _policy() -> SourcePolicy:
    hosts = (
        "data.sec.gov",
        "www.sec.gov",
        "www.federalreserve.gov",
        "www.bls.gov",
        "www.bea.gov",
        "ir.example.com",
        "data.alpaca.markets",
        "news.alpaca.markets",
    )
    return SourcePolicy(
        version="1",
        allowed_adapters=("sec_edgar", "macro", "company_ir", "alpaca_news"),
        allowed_https_domains=hosts,
        allowed_hosts_by_adapter=FrozenMap(
            {
                "sec_edgar": ("data.sec.gov", "www.sec.gov"),
                "macro": ("www.federalreserve.gov", "www.bls.gov", "www.bea.gov"),
                "company_ir": ("ir.example.com",),
                "alpaca_news": ("data.alpaca.markets", "news.alpaca.markets"),
            }
        ),
        freshness_by_data_type=FrozenMap({"official_events": 86400, "news": 3600}),
        cache_retention_seconds=86400,
        request_deadline_seconds=Decimal("10"),
        retry_attempts=0,
        retry_backoff_seconds=Decimal("0.1"),
        retry_jitter_seconds=Decimal("0.01"),
        per_run_request_budgets=FrozenMap({"official_events": 20, "news": 20}),
        maximum_response_bytes=1000000,
        allowed_content_types=("application/json", "text/html"),
        excerpt_limits=FrozenMap({"application/json": 500, "text/html": 500}),
        licensed_content_persistence="METADATA_ONLY",
    )


def _client(
    responder: Callable[[httpx.Request], httpx.Response],
    *,
    policy: SourcePolicy | None = None,
    clock: datetime = CUTOFF,
) -> SafeHttpClient:
    return SafeHttpClient(
        policy or _policy(),
        transport=httpx.MockTransport(responder),
        resolver=lambda host, port: ("93.184.216.34",),
        clock=lambda: clock,
    )


def _response(
    path: str, content: bytes, content_type: str
) -> Callable[[httpx.Request], httpx.Response]:
    def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        return httpx.Response(200, headers={"content-type": content_type}, content=content)

    return responder


@pytest.fixture
def sec_adapter() -> SecEdgarAdapter:
    payload = (FIXTURES / "sec/submissions.json").read_bytes()
    return SecEdgarAdapter(
        http_client=_client(
            _response("/submissions/CIK0000320193.json", payload, "application/json")
        ),
        source_policy=_policy(),
        user_agent="finance-research-agent/0.5 contact@example.test",
        clock=lambda: CUTOFF,
    )


def test_sec_filing_is_authoritative_bounded_evidence(sec_adapter: SecEdgarAdapter) -> None:
    result = sec_adapter.collect_filings(cik="0000320193", cutoff_at=CUTOFF)
    assert result[0].source.provider == "sec_edgar"
    assert result[0].authority_tier == 1
    assert len(result[0].source.excerpt) <= 500
    assert result[0].structured_fields["form"] == "8-K"


def test_post_cutoff_filing_is_excluded_from_revision(sec_adapter: SecEdgarAdapter) -> None:
    result = sec_adapter.collect_filings(cik="0000320193", cutoff_at=CUTOFF)
    assert len(result) == 1
    assert all(evidence.source.retrieved_at <= CUTOFF for evidence in result)


def test_sec_rejects_collection_when_retrieval_is_after_cutoff() -> None:
    payload = (FIXTURES / "sec/submissions.json").read_bytes()
    adapter = SecEdgarAdapter(
        http_client=_client(
            _response("/submissions/CIK0000320193.json", payload, "application/json"),
            clock=datetime(2026, 8, 19, 13, 0, tzinfo=UTC),
        ),
        source_policy=_policy(),
        user_agent="finance-research-agent/0.5 contact@example.test",
        clock=lambda: datetime(2026, 8, 19, 13, 0, tzinfo=UTC),
    )
    assert adapter.collect_filings(cik="0000320193", cutoff_at=CUTOFF) == ()


def test_missing_required_macro_calendar_is_explicit() -> None:
    source = MacroCalendarSource(
        provider="federal_reserve",
        url="https://www.federalreserve.gov/releases/calendar.htm",
        timezone="America/New_York",
        required=True,
    )

    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"content-type": "text/html"}, content=b"unavailable")

    adapter = MacroCalendarAdapter(
        http_client=_client(unavailable),
        source_policy=_policy(),
        sources=(source,),
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(
        symbols=(),
        start=datetime(2026, 8, 19, tzinfo=UTC),
        end=datetime(2026, 8, 20, tzinfo=UTC),
        cutoff_at=CUTOFF,
    )
    assert result.health.available is False
    assert result.health.required is True
    assert result.health.error_code == "PROVIDER_UNAVAILABLE"


def test_valid_empty_macro_calendar_is_not_unavailable() -> None:
    source = MacroCalendarSource(
        provider="bea",
        url="https://www.bea.gov/calendar/",
        timezone="America/New_York",
        required=True,
    )
    adapter = MacroCalendarAdapter(
        http_client=_client(
            _response("/calendar/", b"<html><body>No releases</body></html>", "text/html")
        ),
        source_policy=_policy(),
        sources=(source,),
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(
        symbols=(),
        start=datetime(2026, 8, 19, tzinfo=UTC),
        end=datetime(2026, 8, 20, tzinfo=UTC),
        cutoff_at=CUTOFF,
    )
    assert result.events == ()
    assert result.health.available is True
    assert result.health.empty_valid is True


def test_macro_event_timezone_is_normalized_to_utc() -> None:
    source = MacroCalendarSource(
        provider="federal_reserve",
        url="https://www.federalreserve.gov/releases/calendar.htm",
        timezone="America/New_York",
        required=True,
    )
    payload = (FIXTURES / "macro/fed-calendar.html").read_bytes()
    adapter = MacroCalendarAdapter(
        http_client=_client(_response("/releases/calendar.htm", payload, "text/html")),
        source_policy=_policy(),
        sources=(source,),
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(
        symbols=(),
        start=datetime(2026, 8, 19, tzinfo=UTC),
        end=datetime(2026, 8, 20, tzinfo=UTC),
        cutoff_at=CUTOFF,
    )
    assert result.events[0].event_time == datetime(2026, 8, 19, 12, 30, tzinfo=UTC)
    assert result.events[0].verified is True
    assert result.events[0].supporting_evidence_ids


def test_invalid_macro_timestamp_is_not_treated_as_empty_valid() -> None:
    source = MacroCalendarSource(
        provider="bls",
        url="https://www.bls.gov/schedule/news_release/",
        timezone="America/New_York",
        required=True,
    )
    payload = b'<table><tr data-event-time="not-a-timestamp"><td>Release</td></tr></table>'
    adapter = MacroCalendarAdapter(
        http_client=_client(_response("/schedule/news_release/", payload, "text/html")),
        source_policy=_policy(),
        sources=(source,),
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(
        symbols=(),
        start=datetime(2026, 8, 19, tzinfo=UTC),
        end=datetime(2026, 8, 20, tzinfo=UTC),
        cutoff_at=CUTOFF,
    )
    assert result.events == ()
    assert result.health.available is False
    assert result.health.error_code == "INVALID_RESPONSE"


def test_company_ir_accepts_only_configured_official_watchlist_source() -> None:
    payload = (FIXTURES / "company_ir/earnings-release.html").read_bytes()
    item = WatchlistItem(
        symbol="AAPL",
        role="CORE_MONITOR",
        research_rationale="Apple research",
        official_sources=("https://ir.example.com/releases/aapl.html",),
    )
    adapter = CompanyIrAdapter(
        http_client=_client(_response("/releases/aapl.html", payload, "text/html")),
        source_policy=_policy(),
        company_names={"AAPL": "Apple Inc."},
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(
        item,
        start=datetime(2026, 8, 19, tzinfo=UTC),
        end=datetime(2026, 8, 20, tzinfo=UTC),
        cutoff_at=CUTOFF,
    )
    assert result.events[0].verified is True
    assert result.events[0].subject_symbol == "AAPL"
    assert result.evidence[0].authority_tier == 1


def test_company_ir_cross_domain_redirect_fails_closed() -> None:
    item = WatchlistItem(
        symbol="AAPL",
        role="CORE_MONITOR",
        research_rationale="Apple research",
        official_sources=("https://ir.example.com/releases/aapl.html",),
    )

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://attacker.example.test/aapl.html"},
        )

    adapter = CompanyIrAdapter(
        http_client=_client(redirect), source_policy=_policy(), company_names={"AAPL": "Apple Inc."}
    )
    result = adapter.collect_events(
        item,
        start=datetime(2026, 8, 19, tzinfo=UTC),
        end=datetime(2026, 8, 20, tzinfo=UTC),
        cutoff_at=CUTOFF,
    )
    assert result.health.available is False
    assert result.events == ()


def test_company_ir_identity_mismatch_is_an_explicit_unverified_event() -> None:
    payload = (FIXTURES / "company_ir/earnings-release.html").read_bytes()
    item = WatchlistItem(
        symbol="AAPL",
        role="CORE_MONITOR",
        research_rationale="Apple research",
        official_sources=("https://ir.example.com/releases/aapl.html",),
    )
    adapter = CompanyIrAdapter(
        http_client=_client(_response("/releases/aapl.html", payload, "text/html")),
        source_policy=_policy(),
        company_names={"AAPL": "Different Company"},
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(
        item,
        start=datetime(2026, 8, 19, tzinfo=UTC),
        end=datetime(2026, 8, 20, tzinfo=UTC),
        cutoff_at=CUTOFF,
    )
    assert result.events[0].verified is False
    assert result.evidence[0].authority_tier > 1
    assert result.evidence[0].source.quality_flags == ("UNVERIFIED_CONTENT",)


def test_news_is_discovery_only_until_officially_verified() -> None:
    payload = (FIXTURES / "alpaca/news.json").read_bytes()
    settings = Settings(
        data_dir=Path("data"), alpaca_api_key="fixture-key", alpaca_api_secret="fixture-secret"
    )
    adapter = AlpacaNewsDiscoveryAdapter(
        http_client=_client(_response("/v1beta1/news", payload, "application/json")),
        source_policy=_policy(),
        settings=settings,
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(
        symbols=("AAPL",),
        start=datetime(2026, 8, 19, tzinfo=UTC),
        end=datetime(2026, 8, 20, tzinfo=UTC),
        cutoff_at=CUTOFF,
    )
    assert result.events[0].verified is False
    assert result.events[0].supporting_evidence_ids
    assert result.evidence[0].authority_tier > 1


def test_news_outage_is_scoped_and_does_not_look_like_empty_valid() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"content-type": "application/json"}, content=b"{}")

    adapter = AlpacaNewsDiscoveryAdapter(
        http_client=_client(unavailable),
        source_policy=_policy(),
        settings=Settings(data_dir=Path("data")),
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(
        symbols=("AAPL",),
        start=datetime(2026, 8, 19, tzinfo=UTC),
        end=datetime(2026, 8, 20, tzinfo=UTC),
        cutoff_at=CUTOFF,
    )
    assert result.events == ()
    assert result.health.available is False
    assert result.health.required is False
    assert result.health.empty_valid is False


def test_news_uses_market_data_credentials_without_promoting_news_authority() -> None:
    payload = (FIXTURES / "alpaca/news.json").read_bytes()

    def responder(request: httpx.Request) -> httpx.Response:
        assert request.headers["APCA-API-KEY-ID"] == "fixture-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "fixture-secret"
        return httpx.Response(200, headers={"content-type": "application/json"}, content=payload)

    adapter = AlpacaNewsDiscoveryAdapter(
        http_client=_client(responder),
        source_policy=_policy(),
        settings=Settings(
            data_dir=Path("data"),
            alpaca_api_key="fixture-key",
            alpaca_api_secret="fixture-secret",
        ),
        clock=lambda: CUTOFF,
    )
    result = adapter.collect_events(
        symbols=("AAPL",),
        start=datetime(2026, 8, 19, tzinfo=UTC),
        end=datetime(2026, 8, 20, tzinfo=UTC),
        cutoff_at=CUTOFF,
    )
    assert result.events[0].verified is False


def test_composite_retains_conflict_evidence_and_health() -> None:
    from finance_research_agent.adapters.events import CompositeEventProvider

    evidence_a = EvidenceItem(
        evidence_id="ev_a",
        source={
            "observation_id": "obs_a",
            "provider": "sec_edgar",
            "source_url": "https://www.sec.gov/a",
            "source_hash_sha256": "a" * 64,
            "observed_at": CUTOFF,
            "retrieved_at": CUTOFF,
            "content_type": "application/json",
            "excerpt": "official",
            "persistence_allowed": True,
            "quality_flags": (),
        },
        authority_tier=1,
        instrument_id="AAPL",
        event_time=CUTOFF,
        published_time=CUTOFF,
        structured_fields=FrozenMap({"kind": "official"}),
        citation_label="SEC official",
    )
    evidence_b = evidence_a.model_copy(
        update={
            "evidence_id": "ev_b",
            "source": evidence_a.source.model_copy(
                update={"observation_id": "obs_b", "provider": "alpaca_news"}
            ),
            "authority_tier": 2,
            "citation_label": "News discovery",
        }
    )
    event_a = EventRecord(
        event_id="event_same",
        event_type="EARNINGS",
        subject_symbol="AAPL",
        event_time=CUTOFF,
        verified=True,
        materiality="HIGH",
        supporting_evidence_ids=("ev_a",),
        conflict_evidence_ids=(),
    )
    event_b = event_a.model_copy(update={"verified": False, "supporting_evidence_ids": ("ev_b",)})

    class FakeProvider:
        provider_id = "fake"

        def __init__(self, provider: str, event: EventRecord, evidence: EvidenceItem) -> None:
            self._result = {
                "events": (event,),
                "evidence": (evidence,),
                "source_observations": (evidence.source,),
                "source_health": (SourceHealth(provider=provider, available=True, required=False),),
            }

        def collect_events(self, *args: object, **kwargs: object) -> object:
            from finance_research_agent.domain.models import EventCollection

            return EventCollection.model_validate(self._result)

    result = CompositeEventProvider(
        (
            FakeProvider("sec_edgar", event_a, evidence_a),
            FakeProvider("alpaca_news", event_b, evidence_b),
        )
    ).collect_events(("AAPL",), CUTOFF, datetime(2026, 8, 20, tzinfo=UTC), CUTOFF)
    assert result.events[0].conflict_evidence_ids == ("ev_a", "ev_b")
    assert {item.evidence_id for item in result.evidence} == {"ev_a", "ev_b"}
    assert [health.provider for health in result.source_health] == ["alpaca_news", "sec_edgar"]
