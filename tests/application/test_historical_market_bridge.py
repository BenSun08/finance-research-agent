from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from finance_research_agent.application.market_bridge import (
    canonical_to_regime_input,
    historical_outcome_to_evidence,
    historical_request_failure_to_evidence,
    historical_to_canonical_snapshot,
)
from finance_research_agent.domain.enums import Coverage, Session
from finance_research_agent.domain.indicators import sma
from finance_research_agent.domain.market import (
    DailyBar,
    RegimeMarketSnapshot,
)
from finance_research_agent.domain.market import (
    MarketSnapshot as LegacyMarketSnapshot,
)
from finance_research_agent.domain.models import InstrumentIdentity, SourceObservation
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.market_data.historical import (
    FAILURE_SCHEMA_VERSION,
    BarAdjustment,
    DailyBarObservation,
    HistoricalBarsFailure,
    HistoricalBarsProvenance,
    HistoricalBarsRequestFailure,
    HistoricalBarsRequestFailureReason,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
    MarketDataCoverage,
    MarketDataFeed,
    to_market_snapshot,
)

CUTOFF = datetime(2026, 8, 26, 12, 45, tzinfo=UTC)
RETRIEVED = datetime(2026, 8, 26, 12, 40, tzinfo=UTC)
SESSIONS = (date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25))


def _provenance(*, retrieved_at: datetime = RETRIEVED) -> HistoricalBarsProvenance:
    return HistoricalBarsProvenance(
        provider="fixture",
        feed=MarketDataFeed.IEX,
        coverage=MarketDataCoverage.SINGLE_EXCHANGE,
        adjustment=BarAdjustment.SPLIT,
        requested_start_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
        requested_end_at=datetime(2026, 8, 25, 21, tzinfo=UTC),
        retrieved_at=retrieved_at,
        evidence_cutoff_at=CUTOFF,
        completed_through_session=SESSIONS[-1],
        adapter_version="fixture-v1",
    )


def _history(
    *,
    retrieved_at: datetime = RETRIEVED,
    closes: tuple[str, ...] = ("100", "102", "104"),
) -> HistoricalDailyBars:
    observations = tuple(
        DailyBarObservation(
            source_timestamp=datetime.combine(session, time(21), tzinfo=UTC),
            bar=DailyBar(
                session_date=session,
                open=close - Decimal("1"),
                high=close + Decimal("1"),
                low=close - Decimal("2"),
                close=close,
                volume=1_000_000,
            ),
        )
        for session, close in zip(
            SESSIONS,
            tuple(Decimal(value) for value in closes),
            strict=True,
        )
    )
    return HistoricalDailyBars.create(
        symbol="SPY",
        observations=observations,
        provenance=_provenance(retrieved_at=retrieved_at),
        quality_flags=("CHARACTERIZATION",),
    )


def _instrument() -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id="SPY",
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        instrument_type="ETF",
        primary_exchange="NYSE_ARCA",
        listing_country="US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )


def test_history_evidence_is_stable_and_preserves_provenance_without_publication_time() -> None:
    history = _history()

    first = historical_outcome_to_evidence(history, authority_tier=1)
    second = historical_outcome_to_evidence(history, authority_tier=1)
    later = historical_outcome_to_evidence(
        _history(retrieved_at=RETRIEVED + timedelta(minutes=1)), authority_tier=1
    )

    assert first == second
    assert first.evidence_id.startswith("ev-history-")
    assert first.evidence_id != later.evidence_id
    assert first.published_time is None
    assert first.event_time == history.observations[-1].source_timestamp
    assert first.source.observation_id == history.history_id
    assert first.source.provider == history.provenance.provider
    assert first.source.observed_at == history.observations[-1].source_timestamp
    assert first.source.retrieved_at == history.provenance.retrieved_at
    assert first.source.quality_flags == history.quality_flags
    assert first.structured_fields == FrozenMap(
        {
            "adapter_version": "fixture-v1",
            "adjustment": "split",
            "completed_through_session": "2026-08-25",
            "coverage": "single_exchange",
            "evidence_cutoff_at": "2026-08-26T12:45:00+00:00",
            "feed": "iex",
            "history_id": history.history_id,
            "outcome_kind": "AVAILABLE",
            "provider": "fixture",
            "requested_end_at": "2026-08-25T21:00:00+00:00",
            "requested_start_at": "2026-08-21T21:00:00+00:00",
            "retrieved_at": "2026-08-26T12:40:00+00:00",
            "symbol": "SPY",
        }
    )


def test_history_evidence_identity_uses_existing_decimal_canonicalization() -> None:
    low_precision = _history()
    high_precision = _history(closes=("100.00", "102.00", "104.00"))

    assert low_precision.history_id == high_precision.history_id
    assert historical_outcome_to_evidence(
        low_precision, authority_tier=1
    ).evidence_id == historical_outcome_to_evidence(
        high_precision, authority_tier=1
    ).evidence_id


def test_canonical_history_snapshot_discloses_every_bar_provenance_field() -> None:
    history = _history()
    evidence = historical_outcome_to_evidence(history, authority_tier=1)

    snapshot = historical_to_canonical_snapshot(history, instrument=_instrument())

    assert snapshot.instrument == _instrument()
    assert snapshot.latest_price is None
    assert snapshot.current_session_bars == ()
    assert snapshot.source_observations == (evidence.source,)
    assert snapshot.quality_flags == history.quality_flags
    assert tuple(bar.session_date for bar in snapshot.completed_daily_bars) == SESSIONS
    assert tuple(bar.source_timestamp for bar in snapshot.completed_daily_bars) == tuple(
        observation.source_timestamp for observation in history.observations
    )
    assert all(bar.session is Session.COMPLETED_SESSION for bar in snapshot.completed_daily_bars)
    assert all(bar.provider == "fixture" for bar in snapshot.completed_daily_bars)
    assert all(bar.feed == "iex" for bar in snapshot.completed_daily_bars)
    assert all(
        bar.coverage is Coverage.SINGLE_EXCHANGE for bar in snapshot.completed_daily_bars
    )
    assert all(bar.adjustment == "split" for bar in snapshot.completed_daily_bars)
    assert all(bar.retrieved_at == RETRIEVED for bar in snapshot.completed_daily_bars)
    assert all(bar.evidence_cutoff_at == CUTOFF for bar in snapshot.completed_daily_bars)
    assert all(bar.evidence_id == evidence.evidence_id for bar in snapshot.completed_daily_bars)


def test_canonical_projection_preserves_legacy_snapshot_and_metric_identity() -> None:
    history = _history()
    canonical = historical_to_canonical_snapshot(history, instrument=_instrument())

    projected = canonical_to_regime_input(canonical)
    compatibility = to_market_snapshot(history)
    metric = sma(projected, window=2, cutoff_at=CUTOFF)

    assert LegacyMarketSnapshot is RegimeMarketSnapshot
    assert projected == compatibility
    assert projected.snapshot_id == "normalized-78f54ca9e1eaaeeb41b5a347"
    assert projected.input_evidence_ids == tuple(
        dict.fromkeys(bar.evidence_id for bar in canonical.completed_daily_bars)
    )
    assert metric.metric_id == "metric-ab7988438da9b4249685aed0"
    assert metric.value == Decimal("103")
    assert metric.input_snapshot_ids == (projected.snapshot_id,)
    assert metric.input_evidence_ids == projected.input_evidence_ids


def test_per_symbol_failure_is_evidence_without_fabricated_market_data() -> None:
    failure = HistoricalBarsFailure(
        schema_version=FAILURE_SCHEMA_VERSION,
        symbol="SPY",
        reason=HistoricalBarsUnavailableReason.MISSING_EXPECTED_SESSION,
        provenance=_provenance(),
        missing_sessions=(date(2026, 8, 25),),
        quality_flags=("MISSING_EXPECTED_SESSION",),
    )

    evidence = historical_outcome_to_evidence(failure, authority_tier=1)

    assert evidence.instrument_id == "SPY"
    assert evidence.event_time is None
    assert evidence.published_time is None
    assert evidence.structured_fields["outcome_kind"] == "SYMBOL_FAILURE"
    assert evidence.structured_fields["reason"] == "missing_expected_session"
    assert evidence.structured_fields["missing_sessions"] == ("2026-08-25",)
    assert not hasattr(evidence, "completed_daily_bars")


def test_request_global_failure_remains_global_and_uses_supplied_source_context() -> None:
    source = SourceObservation(
        observation_id="request-attempt-1",
        provider="fixture",
        source_url=None,
        source_hash_sha256="a" * 64,
        observed_at=RETRIEVED,
        retrieved_at=RETRIEVED,
        content_type="application/vnd.finance-research-agent.request-failure+json",
        excerpt="",
        persistence_allowed=True,
        quality_flags=(),
    )
    failure = HistoricalBarsRequestFailure(
        reason=HistoricalBarsRequestFailureReason.RATE_LIMITED
    )

    evidence = historical_request_failure_to_evidence(
        failure, source=source, authority_tier=1
    )

    assert evidence.instrument_id is None
    assert evidence.source == source
    assert evidence.event_time is None
    assert evidence.published_time is None
    assert evidence.structured_fields == FrozenMap(
        {"outcome_kind": "REQUEST_FAILURE", "reason": "rate_limited"}
    )
    assert not hasattr(failure, "symbol")
