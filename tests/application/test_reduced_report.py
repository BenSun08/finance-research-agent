from datetime import timedelta
from decimal import Decimal

from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.reduced_report import render_reduced_report_base
from finance_research_agent.domain.enums import Coverage, Session
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    CurrentSessionBar,
    EventRecord,
    InstrumentIdentity,
    MarketSnapshot,
    PriceObservation,
    SourceObservation,
)


def test_reduced_report_discloses_market_coverage_and_conflicting_evidence(
    valid_packet,
) -> None:
    cutoff = valid_packet.run.require_evidence_cutoff()
    observed_at = cutoff - timedelta(minutes=1)
    source = SourceObservation(
        observation_id="source-aapl-history",
        provider="alpaca",
        source_url=None,
        source_hash_sha256="a" * 64,
        observed_at=observed_at,
        retrieved_at=cutoff,
        content_type="application/vnd.finance-research-agent.historical-bars+json",
        excerpt="",
        persistence_allowed=True,
        quality_flags=(),
    )
    instrument = InstrumentIdentity(
        instrument_id="NASDAQ:AAPL",
        symbol="AAPL",
        name="Apple Inc.",
        instrument_type="COMMON_STOCK",
        primary_exchange="NASDAQ",
        listing_country="US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )
    bar = CompletedDailyBar(
        instrument_id=instrument.instrument_id,
        session_date=valid_packet.run.market_date - timedelta(days=1),
        source_timestamp=observed_at,
        open=Decimal("100"),
        high=Decimal("104"),
        low=Decimal("99"),
        close=Decimal("103"),
        volume=1000,
        session=Session.COMPLETED_SESSION,
        provider="alpaca",
        feed="iex",
        coverage=Coverage.SINGLE_EXCHANGE,
        adjustment="SPLIT",
        retrieved_at=cutoff,
        evidence_cutoff_at=cutoff,
        evidence_id="evidence-00",
        quality_flags=(),
    )
    earlier_bar = bar.model_copy(
        update={
            "session_date": bar.session_date - timedelta(days=1),
            "source_timestamp": observed_at - timedelta(days=1),
        }
    )
    price = PriceObservation(
        instrument_id=instrument.instrument_id,
        value=Decimal("103"),
        currency="USD",
        session=Session.PRE_MARKET,
        provider="alpaca",
        feed="iex",
        coverage=Coverage.SINGLE_EXCHANGE,
        observed_at=observed_at,
        retrieved_at=cutoff,
        evidence_id="evidence-00",
        quality_flags=(),
    )
    current_bar = CurrentSessionBar(
        instrument_id=instrument.instrument_id,
        session=Session.PRE_MARKET,
        start_at=cutoff - timedelta(minutes=15),
        end_at=cutoff - timedelta(minutes=10),
        open=Decimal("102"),
        high=Decimal("104"),
        low=Decimal("101"),
        close=Decimal("103"),
        volume=1000,
        provider="alpaca",
        feed="sip",
        coverage=Coverage.CONSOLIDATED,
        retrieved_at=cutoff,
        evidence_id="evidence-00",
        quality_flags=(),
    )
    snapshot = MarketSnapshot(
        instrument=instrument,
        latest_price=price,
        completed_daily_bars=(earlier_bar, bar),
        current_session_bars=(current_bar,),
        source_observations=(source,),
        quality_flags=(),
    )
    conflict = EventRecord(
        event_id="event-aapl-conflict",
        event_type="EARNINGS",
        subject_symbol="AAPL",
        event_time=observed_at,
        verified=True,
        materiality="HIGH",
        supporting_evidence_ids=("evidence-00",),
        conflict_evidence_ids=("evidence-01",),
    )
    packet = build_research_packet(
        run=valid_packet.run,
        evidence=valid_packet.evidence,
        snapshots={instrument.instrument_id: snapshot},
        events=(conflict,),
        metrics=valid_packet.metrics,
        gates=valid_packet.gates,
        candidates=valid_packet.candidates,
        exclusions=valid_packet.candidate_exclusions,
        plans=valid_packet.deterministic_plan_inputs,
        capabilities=valid_packet.capability_states,
        observations=valid_packet.prior_plan_observations,
        max_serialized_bytes=valid_packet.synthesis_constraints.max_serialized_bytes,
    )

    report = render_reduced_report_base(packet).decode("utf-8")

    assert len(packet.market) == 1
    assert report.count(
        "Market-data source: AAPL latest price; provider=alpaca; feed=iex; "
        "coverage=single\\_exchange (one exchange only); session=PRE\\_MARKET; "
        f"observed_at={observed_at.isoformat()}; retrieved_at={cutoff.isoformat()}."
    ) == 2, report
    assert report.count(
        "Market-data source: AAPL completed daily bars; provider=alpaca; feed=iex; "
        "coverage=single\\_exchange (one exchange only); "
        "session=COMPLETED\\_SESSION; "
        f"observed_at={(observed_at - timedelta(days=1)).isoformat()} through "
        f"{observed_at.isoformat()}; "
        f"retrieved_at={cutoff.isoformat()}."
    ) == 2
    assert report.count(
        "Market-data source: AAPL current-session bars; provider=alpaca; feed=sip; "
        "coverage=consolidated (U.S. consolidated coverage); session=PRE\\_MARKET; "
        f"observed_at={(cutoff - timedelta(minutes=15)).isoformat()} through "
        f"{(cutoff - timedelta(minutes=10)).isoformat()}; "
        f"retrieved_at={cutoff.isoformat()}."
    ) == 2
    assert report.count("Source conflict: event-aapl-conflict") == 2
    assert report.count("materiality=HIGH") == 2
    assert report.count("supporting evidence=evidence-00") == 2
    assert report.count("conflicting evidence=evidence-01") == 2

    without_latest_price = build_research_packet(
        run=valid_packet.run,
        evidence=valid_packet.evidence,
        snapshots={
            instrument.instrument_id: snapshot.model_copy(update={"latest_price": None})
        },
        events=(conflict,),
        metrics=valid_packet.metrics,
        gates=valid_packet.gates,
        candidates=valid_packet.candidates,
        exclusions=valid_packet.candidate_exclusions,
        plans=valid_packet.deterministic_plan_inputs,
        capabilities=valid_packet.capability_states,
        observations=valid_packet.prior_plan_observations,
        max_serialized_bytes=valid_packet.synthesis_constraints.max_serialized_bytes,
    )
    without_price_report = render_reduced_report_base(without_latest_price).decode("utf-8")
    assert "Market-data source: AAPL latest price" not in without_price_report
    assert "Market-data source: AAPL completed daily bars" in without_price_report
