from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from finance_research_agent.domain.eligibility import evaluate_instrument_eligibility
from finance_research_agent.domain.enums import Capability, Coverage, GateStatus, Session
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    InstrumentIdentity,
    MarketSnapshot,
    SourceObservation,
)
from finance_research_agent.domain.policies import SetupPolicy, WatchlistItem


def _setup_policy() -> SetupPolicy:
    return SetupPolicy(
        version="1",
        required_historical_sessions=3,
        minimum_price="10",
        minimum_median_dollar_volume="100000",
        moving_average_windows=(2,),
        trend_slope_windows=(2,),
        breakout_lookback=2,
        atr_window=2,
        entry_zone_atr_buffers=("1",),
        extension_limits=("1",),
        pullback_support_tolerances=("1",),
        restrengthening_conditions=("close",),
        minimum_reward_to_risk="1",
        plan_lifetime_sessions=2,
        earnings_blackout_sessions=0,
        score_weights=("1", "1", "1", "1", "1", "1"),
        penalty_names=(
            "EXTENSION_PENALTY",
            "EVENT_UNCERTAINTY_PENALTY",
            "CORRELATION_CONCENTRATION_PENALTY",
            "DATA_QUALITY_PENALTY",
        ),
    )


def _snapshot(instrument: InstrumentIdentity) -> MarketSnapshot:
    retrieved = datetime(2026, 9, 21, 12, tzinfo=UTC)
    source = SourceObservation(
        observation_id="obs-msft",
        provider="fixture",
        source_url=None,
        source_hash_sha256="a" * 64,
        observed_at=retrieved,
        retrieved_at=retrieved,
        content_type="application/json",
        excerpt="",
        persistence_allowed=True,
        quality_flags=(),
    )
    bars = tuple(
        CompletedDailyBar(
            instrument_id=instrument.instrument_id,
            session_date=date(2026, 9, 16 + index),
            source_timestamp=retrieved,
            open=Decimal("20"),
            high=Decimal("21"),
            low=Decimal("19"),
            close=Decimal("20"),
            volume=10_000,
            session=Session.COMPLETED_SESSION,
            provider="fixture",
            feed="iex",
            coverage=Coverage.SINGLE_EXCHANGE,
            adjustment="split",
            retrieved_at=retrieved,
            evidence_cutoff_at=retrieved,
            evidence_id="ev-msft",
            quality_flags=(),
        )
        for index in range(3)
    )
    return MarketSnapshot(
        instrument=instrument,
        latest_price=None,
        completed_daily_bars=bars,
        current_session_bars=(),
        source_observations=(source,),
        quality_flags=(),
    )


@pytest.mark.parametrize(
    "instrument_type, leveraged, inverse, otc, active",
    [
        ("OTHER", False, False, True, True),
        ("OTHER", False, False, False, True),
        ("ETF", True, False, False, True),
        ("ETF", False, True, False, True),
        ("COMMON_STOCK", False, False, False, False),
    ],
)
def test_ineligible_instrument_is_blocked_before_scoring(
    instrument_type: str, leveraged: bool, inverse: bool, otc: bool, active: bool
) -> None:
    instrument = InstrumentIdentity(
        instrument_id="MSFT",
        symbol="MSFT",
        name="Microsoft",
        instrument_type=instrument_type,  # type: ignore[arg-type]
        primary_exchange="NASDAQ",
        listing_country="US",
        currency="USD",
        is_active=active,
        is_leveraged=leveraged,
        is_inverse=inverse,
        is_otc=otc,
    )

    gates = evaluate_instrument_eligibility(
        instrument=instrument,
        watchlist_item=WatchlistItem(
            symbol="MSFT", role="SATELLITE_ELIGIBLE", research_rationale="Research."
        ),
        snapshot=_snapshot(instrument),
        setup_policy=_setup_policy(),
    )

    assert any(gate.status is GateStatus.BLOCK for gate in gates)
    assert all(gate.capability is Capability.PLAN_DRAFT_AVAILABLE for gate in gates)


def test_only_satellite_role_with_required_proxies_can_enter_setup_detection() -> None:
    instrument = InstrumentIdentity(
        instrument_id="MSFT",
        symbol="MSFT",
        name="Microsoft",
        instrument_type="COMMON_STOCK",
        primary_exchange="NASDAQ",
        listing_country="US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )
    gates = evaluate_instrument_eligibility(
        instrument=instrument,
        watchlist_item=WatchlistItem(
            symbol="MSFT",
            role="SATELLITE_ELIGIBLE",
            research_rationale="Research.",
            benchmark_symbol="SPY",
            sector_proxy_symbol="XLK",
        ),
        snapshot=_snapshot(instrument),
        setup_policy=_setup_policy(),
    )

    assert gates == ()


@pytest.mark.parametrize("field", ("primary_exchange", "listing_country"))
def test_incomplete_instrument_identity_blocks_before_scoring(field: str) -> None:
    instrument = InstrumentIdentity(
        instrument_id="MSFT",
        symbol="MSFT",
        name="Microsoft",
        instrument_type="COMMON_STOCK",
        primary_exchange=None if field == "primary_exchange" else "NASDAQ",
        listing_country=None if field == "listing_country" else "US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )

    gates = evaluate_instrument_eligibility(
        instrument=instrument,
        watchlist_item=WatchlistItem(
            symbol="MSFT",
            role="SATELLITE_ELIGIBLE",
            research_rationale="Research.",
            benchmark_symbol="SPY",
            sector_proxy_symbol="XLK",
        ),
        snapshot=_snapshot(instrument),
        setup_policy=_setup_policy(),
    )

    assert any(gate.status is GateStatus.BLOCK for gate in gates)
    assert any(gate.reason_code == "UNSUPPORTED_INSTRUMENT" for gate in gates)


@pytest.mark.parametrize(
    "direction, halted, history_count, close, volume, role, benchmark, sector_proxy",
    (
        ("SHORT", False, 3, Decimal("20"), 10_000, "SATELLITE_ELIGIBLE", "SPY", "XLK"),
        ("LONG", True, 3, Decimal("20"), 10_000, "SATELLITE_ELIGIBLE", "SPY", "XLK"),
        ("LONG", False, 2, Decimal("20"), 10_000, "SATELLITE_ELIGIBLE", "SPY", "XLK"),
        ("LONG", False, 3, Decimal("9"), 10_000, "SATELLITE_ELIGIBLE", "SPY", "XLK"),
        ("LONG", False, 3, Decimal("20"), None, "SATELLITE_ELIGIBLE", "SPY", "XLK"),
        ("LONG", False, 3, Decimal("20"), 1, "SATELLITE_ELIGIBLE", "SPY", "XLK"),
        ("LONG", False, 3, Decimal("20"), 10_000, "CORE_MONITOR", "SPY", "XLK"),
        ("LONG", False, 3, Decimal("20"), 10_000, "RESEARCH_ONLY", "SPY", "XLK"),
        ("LONG", False, 3, Decimal("20"), 10_000, "SATELLITE_ELIGIBLE", None, "XLK"),
        ("LONG", False, 3, Decimal("20"), 10_000, "SATELLITE_ELIGIBLE", "SPY", None),
    ),
)
def test_each_binary_eligibility_failure_blocks_plan(
    direction: str,
    halted: bool,
    history_count: int,
    close: Decimal,
    volume: int | None,
    role: str,
    benchmark: str | None,
    sector_proxy: str | None,
) -> None:
    instrument = InstrumentIdentity(
        instrument_id="MSFT",
        symbol="MSFT",
        name="Microsoft",
        instrument_type="COMMON_STOCK",
        primary_exchange="NASDAQ",
        listing_country="US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )
    snapshot = _snapshot(instrument)
    bars = tuple(
        bar.model_copy(update={"close": close, "volume": volume})
        for bar in snapshot.completed_daily_bars[:history_count]
    )
    snapshot = snapshot.model_copy(update={"completed_daily_bars": bars})

    gates = evaluate_instrument_eligibility(
        instrument=instrument,
        watchlist_item=WatchlistItem(
            symbol="MSFT",
            role=role,  # type: ignore[arg-type]
            research_rationale="Research.",
            benchmark_symbol=benchmark,
            sector_proxy_symbol=sector_proxy,
        ),
        snapshot=snapshot,
        setup_policy=_setup_policy(),
        direction=direction,
        halted=halted,
    )

    assert any(gate.status is GateStatus.BLOCK for gate in gates)
