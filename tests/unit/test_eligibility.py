from datetime import UTC, date, datetime
from decimal import Decimal, localcontext

import pytest

from finance_research_agent.domain.eligibility import evaluate_instrument_eligibility
from finance_research_agent.domain.enums import Capability, Coverage, GateStatus, Session
from finance_research_agent.domain.errors import ErrorCode
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
    "instrument_type, leveraged, inverse, otc, active, reason",
    [
        ("OTHER", False, False, True, True, "UNSUPPORTED_INSTRUMENT"),
        ("OTHER", False, False, False, True, "UNSUPPORTED_INSTRUMENT"),
        ("ETF", True, False, False, True, "UNSUPPORTED_INSTRUMENT"),
        ("ETF", False, True, False, True, "UNSUPPORTED_INSTRUMENT"),
        ("COMMON_STOCK", False, False, False, False, "UNSUPPORTED_INSTRUMENT"),
    ],
)
def test_ineligible_instrument_is_blocked_before_scoring(
    instrument_type: str, leveraged: bool, inverse: bool, otc: bool, active: bool, reason: str
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
    assert {gate.reason_code for gate in gates} == {reason}
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


def _eligible_instrument() -> InstrumentIdentity:
    return InstrumentIdentity(
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


def _watchlist_item() -> WatchlistItem:
    return WatchlistItem(
        symbol="MSFT",
        role="SATELLITE_ELIGIBLE",
        research_rationale="Research.",
        benchmark_symbol="SPY",
        sector_proxy_symbol="XLK",
    )


def _snapshot_with_prices_and_volumes(
    instrument: InstrumentIdentity,
    values: tuple[tuple[Decimal, int], ...],
) -> MarketSnapshot:
    snapshot = _snapshot(instrument)
    bars = tuple(
        CompletedDailyBar(
            instrument_id=bar.instrument_id,
            session_date=bar.session_date,
            source_timestamp=bar.source_timestamp,
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=volume,
            session=bar.session,
            provider=bar.provider,
            feed=bar.feed,
            coverage=bar.coverage,
            adjustment=bar.adjustment,
            retrieved_at=bar.retrieved_at,
            evidence_cutoff_at=bar.evidence_cutoff_at,
            evidence_id=bar.evidence_id,
            quality_flags=bar.quality_flags,
        )
        for bar, (close, volume) in zip(
            snapshot.completed_daily_bars[: len(values)], values, strict=True
        )
    )
    return snapshot.model_copy(update={"completed_daily_bars": bars})


def test_liquidity_equal_to_threshold_is_eligible() -> None:
    instrument = _eligible_instrument()
    snapshot = _snapshot_with_prices_and_volumes(
        instrument,
        ((Decimal("10"), 9_000), (Decimal("10"), 11_000)),
    )
    policy = _setup_policy().model_copy(update={"required_historical_sessions": 2})

    gates = evaluate_instrument_eligibility(
        instrument=instrument,
        watchlist_item=_watchlist_item(),
        snapshot=snapshot,
        setup_policy=policy,
    )

    assert gates == ()


def test_even_sample_liquidity_uses_mean_of_middle_values() -> None:
    instrument = _eligible_instrument()
    snapshot = _snapshot_with_prices_and_volumes(
        instrument,
        ((Decimal("10"), 9_000), (Decimal("10"), 11_000)),
    )
    policy = _setup_policy().model_copy(
        update={
            "required_historical_sessions": 2,
            "minimum_median_dollar_volume": Decimal("100001"),
        }
    )

    gates = evaluate_instrument_eligibility(
        instrument=instrument,
        watchlist_item=_watchlist_item(),
        snapshot=snapshot,
        setup_policy=policy,
    )

    assert {gate.reason_code for gate in gates} == {ErrorCode.UNSUPPORTED_INSTRUMENT.value}
    assert {gate.message for gate in gates} == {"liquidity is below setup policy minimum"}


def test_liquidity_decimal_arithmetic_ignores_ambient_context() -> None:
    instrument = _eligible_instrument()
    snapshot = _snapshot_with_prices_and_volumes(
        instrument,
        ((Decimal("123456.789"), 1), (Decimal("123456.791"), 1)),
    )
    policy = _setup_policy().model_copy(
        update={
            "required_historical_sessions": 2,
            "minimum_median_dollar_volume": Decimal("123456.795"),
        }
    )

    with localcontext() as context:
        context.prec = 5
        gates = evaluate_instrument_eligibility(
            instrument=instrument,
            watchlist_item=_watchlist_item(),
            snapshot=snapshot,
            setup_policy=policy,
        )

    assert {gate.message for gate in gates} == {"liquidity is below setup policy minimum"}


@pytest.mark.parametrize(
    (
        "direction, halted, history_count, close, volume, role, benchmark, "
        "sector_proxy, expected_reason"
    ),
    (
        (
            "SHORT",
            False,
            3,
            Decimal("20"),
            10_000,
            "SATELLITE_ELIGIBLE",
            "SPY",
            "XLK",
            ErrorCode.UNSUPPORTED_INSTRUMENT,
        ),
        (
            "LONG",
            True,
            3,
            Decimal("20"),
            10_000,
            "SATELLITE_ELIGIBLE",
            "SPY",
            "XLK",
            ErrorCode.UNSUPPORTED_INSTRUMENT,
        ),
        (
            "LONG",
            False,
            2,
            Decimal("20"),
            10_000,
            "SATELLITE_ELIGIBLE",
            "SPY",
            "XLK",
            ErrorCode.PROVIDER_MISSING_SESSION,
        ),
        (
            "LONG",
            False,
            3,
            Decimal("9"),
            10_000,
            "SATELLITE_ELIGIBLE",
            "SPY",
            "XLK",
            ErrorCode.UNSUPPORTED_INSTRUMENT,
        ),
        (
            "LONG",
            False,
            3,
            Decimal("20"),
            None,
            "SATELLITE_ELIGIBLE",
            "SPY",
            "XLK",
            ErrorCode.PROVIDER_NO_DATA,
        ),
        (
            "LONG",
            False,
            3,
            Decimal("20"),
            1,
            "SATELLITE_ELIGIBLE",
            "SPY",
            "XLK",
            ErrorCode.UNSUPPORTED_INSTRUMENT,
        ),
        (
            "LONG",
            False,
            3,
            Decimal("20"),
            10_000,
            "CORE_MONITOR",
            "SPY",
            "XLK",
            ErrorCode.CONFIGURATION_INVALID,
        ),
        (
            "LONG",
            False,
            3,
            Decimal("20"),
            10_000,
            "RESEARCH_ONLY",
            "SPY",
            "XLK",
            ErrorCode.CONFIGURATION_INVALID,
        ),
        (
            "LONG",
            False,
            3,
            Decimal("20"),
            10_000,
            "SATELLITE_ELIGIBLE",
            None,
            "XLK",
            ErrorCode.CONFIGURATION_INVALID,
        ),
        (
            "LONG",
            False,
            3,
            Decimal("20"),
            10_000,
            "SATELLITE_ELIGIBLE",
            "SPY",
            None,
            ErrorCode.CONFIGURATION_INVALID,
        ),
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
    expected_reason: ErrorCode,
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
        CompletedDailyBar(
            instrument_id=bar.instrument_id,
            session_date=bar.session_date,
            source_timestamp=bar.source_timestamp,
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=volume,
            session=bar.session,
            provider=bar.provider,
            feed=bar.feed,
            coverage=bar.coverage,
            adjustment=bar.adjustment,
            retrieved_at=bar.retrieved_at,
            evidence_cutoff_at=bar.evidence_cutoff_at,
            evidence_id=bar.evidence_id,
            quality_flags=bar.quality_flags,
        )
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

    assert {gate.status for gate in gates} == {GateStatus.BLOCK}
    assert {gate.reason_code for gate in gates} == {expected_reason.value}
