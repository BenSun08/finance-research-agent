import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from finance_research_agent.domain.eligibility import evaluate_instrument_eligibility
from finance_research_agent.domain.enums import (
    Capability,
    Coverage,
    GateStatus,
    PlanStatus,
    Session,
)
from finance_research_agent.domain.events import EventAssessment
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    CurrentSessionBar,
    GateResult,
    InstrumentIdentity,
    MarketSnapshot,
    SourceHealth,
    SourceObservation,
)
from finance_research_agent.domain.policies import SetupPolicy, WatchlistItem
from finance_research_agent.domain.quality import evaluate_data_quality
from finance_research_agent.domain.setups import (
    SetupType,
    assess_setups,
    calculate_plan_levels,
    detect_setups,
)

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "market"
_CUTOFF = datetime(2026, 9, 22, 12, tzinfo=UTC)


def _policy() -> SetupPolicy:
    return SetupPolicy(
        version="1",
        required_historical_sessions=10,
        minimum_price="5",
        minimum_median_dollar_volume="10000000",
        moving_average_windows=(3, 5, 8),
        trend_slope_windows=(2, 3),
        breakout_lookback=4,
        atr_window=3,
        entry_zone_atr_buffers=("0.25", "0.50"),
        extension_limits=("0.08", "0.12"),
        pullback_support_tolerances=("0.02", "0.05"),
        restrengthening_conditions=("above_support", "positive_close"),
        minimum_reward_to_risk="2",
        plan_lifetime_sessions=10,
        earnings_blackout_sessions=5,
        score_weights=("25", "20", "20", "15", "10", "10"),
        penalty_names=(
            "EXTENSION_PENALTY",
            "EVENT_UNCERTAINTY_PENALTY",
            "CORRELATION_CONCENTRATION_PENALTY",
            "DATA_QUALITY_PENALTY",
        ),
    )


def _identity(symbol: str) -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id=f"instrument-{symbol.lower()}",
        symbol=symbol,
        name=f"{symbol} Incorporated",
        instrument_type="COMMON_STOCK",
        primary_exchange="NASDAQ",
        listing_country="US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )


def _snapshot(
    symbol: str,
    closes: tuple[str, ...],
    *,
    volume: int,
    premarket_volume: int = 0,
) -> MarketSnapshot:
    instrument = _identity(symbol)
    source = SourceObservation(
        observation_id=f"source-{symbol.lower()}",
        provider="fixture",
        source_url=None,
        source_hash_sha256="a" * 64,
        observed_at=_CUTOFF,
        retrieved_at=_CUTOFF,
        content_type="application/json",
        excerpt="",
        persistence_allowed=True,
        quality_flags=(),
    )
    first_date = date(2026, 9, 1)
    bars = tuple(
        CompletedDailyBar(
            instrument_id=instrument.instrument_id,
            session_date=first_date + timedelta(days=index),
            source_timestamp=_CUTOFF,
            open=Decimal(close) - Decimal("0.25"),
            high=Decimal(close) + Decimal("1"),
            low=Decimal(close) - Decimal("1"),
            close=Decimal(close),
            volume=volume,
            session=Session.COMPLETED_SESSION,
            provider="fixture",
            feed="sip",
            coverage=Coverage.CONSOLIDATED,
            adjustment="split",
            retrieved_at=_CUTOFF,
            evidence_cutoff_at=_CUTOFF,
            evidence_id=f"evidence-{symbol.lower()}-{index}",
            quality_flags=(),
        )
        for index, close in enumerate(closes)
    )
    current_bars = (
        (
            CurrentSessionBar(
                instrument_id=instrument.instrument_id,
                session=Session.PRE_MARKET,
                start_at=_CUTOFF - timedelta(minutes=5),
                end_at=_CUTOFF,
                open=bars[-1].close,
                high=bars[-1].close + Decimal("0.5"),
                low=bars[-1].close - Decimal("0.5"),
                close=bars[-1].close,
                volume=premarket_volume,
                provider="fixture",
                feed="iex",
                coverage=Coverage.SINGLE_EXCHANGE,
                retrieved_at=_CUTOFF,
                evidence_id=f"evidence-{symbol.lower()}-premarket",
                quality_flags=("IEX_SINGLE_EXCHANGE",),
            ),
        )
        if premarket_volume
        else ()
    )
    return MarketSnapshot(
        instrument=instrument,
        latest_price=None,
        completed_daily_bars=bars,
        current_session_bars=current_bars,
        source_observations=(source,),
        quality_flags=(),
    )


def _load_context(name: str) -> dict[str, object]:
    payload = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    context = {
        "snapshot": _snapshot(
            payload["symbol"],
            tuple(payload["closes"]),
            volume=payload["volume"],
            premarket_volume=payload["premarket_volume"],
        ),
        "benchmark": _snapshot("SPY", tuple(payload["benchmark_closes"]), volume=1_000_000),
        "sector_proxy": _snapshot("XLK", tuple(payload["sector_closes"]), volume=1_000_000),
        "setup_policy": _policy(),
        "evidence_cutoff_at": _CUTOFF,
    }
    snapshot = context["snapshot"]
    context["eligibility_gates"] = evaluate_instrument_eligibility(
        instrument=snapshot.instrument,
        snapshot=snapshot,
        watchlist_item=WatchlistItem(
            symbol=snapshot.instrument.symbol,
            role="SATELLITE_ELIGIBLE",
            research_rationale="Fixture",
            benchmark_symbol="SPY",
            sector_proxy_symbol="XLK",
        ),
        setup_policy=context["setup_policy"],
    )
    context["event_assessment"] = EventAssessment(plan_status=PlanStatus.DRAFT, gates=())
    context["data_quality"] = evaluate_data_quality(
        source_health=tuple(
            SourceHealth(provider=name, available=True, required=True)
            for name in ("alpaca", "market-calendar", "macro-calendar", "sec_edgar")
        )
    )
    return context


@pytest.fixture
def breakout_context() -> dict[str, object]:
    return _load_context("valid-breakout.json")


@pytest.fixture
def pullback_context() -> dict[str, object]:
    return _load_context("valid-pullback.json")


def test_valid_breakout_has_conditional_trigger_and_invalidation(
    breakout_context: dict[str, object],
) -> None:
    setups = detect_setups(**breakout_context)

    assert len(setups) == 1
    assert setups[0].setup_type is SetupType.BREAKOUT_CONTINUATION
    assert setups[0].entry_condition != str(setups[0].entry_zone.lower.value)
    assert "consolidated volume confirmation" in setups[0].entry_condition
    assert setups[0].candidate_stop.value < setups[0].entry_zone.lower.value


def test_valid_pullback_requires_restrengthening_and_preserves_level_evidence(
    pullback_context: dict[str, object],
) -> None:
    setups = detect_setups(**pullback_context)

    assert len(setups) == 1
    setup = setups[0]
    assert setup.setup_type is SetupType.TREND_PULLBACK
    assert "re-strengthening" in setup.entry_condition
    assert setup.candidate_stop.value < setup.support_reference.value
    assert setup.entry_zone.lower.observed_at == _CUTOFF
    assert setup.entry_zone.lower.feed == "sip"
    assert setup.entry_zone.lower.evidence_id.startswith("evidence-bbb-")
    assert all(target.reward_to_risk >= Decimal("2") for target in setup.target_scenarios)


def test_large_decline_without_positive_trend_is_not_pullback(
    pullback_context: dict[str, object],
) -> None:
    snapshot = _snapshot(
        "BBB",
        ("112", "110", "108", "105", "102", "99", "96", "92", "88", "84"),
        volume=250_000,
    )

    assert detect_setups(**{**pullback_context, "snapshot": snapshot}) == ()


def test_single_exchange_premarket_volume_is_supporting_only(
    breakout_context: dict[str, object],
) -> None:
    setup = detect_setups(**breakout_context)[0]

    assert setup.supporting_evidence_ids[-1] == "evidence-aaa-premarket"
    assert "consolidated volume confirmation" in setup.entry_condition
    assert "premarket" not in setup.entry_condition.lower()


def test_calculate_plan_levels_is_deterministic_and_uses_policy_reward_risk(
    breakout_context: dict[str, object],
) -> None:
    setup = detect_setups(**breakout_context)[0]

    levels = calculate_plan_levels(
        setup,
        breakout_context["snapshot"],
        breakout_context["setup_policy"],
    )

    assert levels == setup.levels
    assert levels.target_scenarios[0].reward_to_risk == Decimal("2")
    assert levels.target_scenarios[1].reward_to_risk == Decimal("3")
    assert levels.candidate_stop.coverage is Coverage.CONSOLIDATED


def test_only_approved_setup_families_exist() -> None:
    assert tuple(SetupType) == (
        SetupType.BREAKOUT_CONTINUATION,
        SetupType.TREND_PULLBACK,
    )


def _block() -> GateResult:
    return GateResult(
        gate_id="eligibility-fixture",
        status=GateStatus.BLOCK,
        reason_code="UNSUPPORTED_INSTRUMENT",
        message="Authoritative R5 rejection",
        evidence_ids=("ev-r5",),
        capability=Capability.PLAN_DRAFT_AVAILABLE,
        rule_version="r5-eligibility-1",
    )


def test_r5_block_is_retained_without_running_setup_calculations(breakout_context):
    context = {**breakout_context, "eligibility_gates": (_block(),), "benchmark": None}
    result = assess_setups(**context)
    assert result.setups == ()
    assert result.exclusions[0].gates[0] == _block()
    assert result.exclusions[0].symbol == "AAA"


@pytest.mark.parametrize("missing", ["benchmark", "sector_proxy"])
def test_required_missing_comparison_is_an_exclusion(breakout_context, missing):
    result = assess_setups(**{**breakout_context, missing: None})
    assert result.setups == ()
    assert "REQUIRED_DATA_MISSING" in result.exclusions[0].reason_codes


def test_disabled_quality_capability_cannot_be_offset(breakout_context):
    quality = evaluate_data_quality(source_health=())
    result = assess_setups(**{**breakout_context, "data_quality": quality})
    assert not result.setups
    assert result.exclusions[0].gates


@pytest.mark.parametrize("change", ["short", "late", "misaligned", "single_exchange", "volume"])
def test_invalid_required_history_is_excluded(breakout_context, change):
    snapshot = breakout_context["snapshot"]
    bars = snapshot.completed_daily_bars
    if change == "short":
        bars = bars[-3:]
    elif change == "late":
        late = _CUTOFF + timedelta(seconds=1)
        bars = (
            *bars[:-1],
            bars[-1].model_copy(
                update={
                    "source_timestamp": late,
                    "retrieved_at": late,
                    "evidence_cutoff_at": late,
                }
            ),
        )
    elif change == "misaligned":
        bars = (*bars[:-1], bars[-1].model_copy(update={"session_date": date(2026, 9, 12)}))
    elif change == "single_exchange":
        bars = tuple(bar.model_copy(update={"coverage": Coverage.SINGLE_EXCHANGE}) for bar in bars)
    else:
        bars = (*bars[:-1], bars[-1].model_copy(update={"volume": None}))
    result = assess_setups(
        **{
            **breakout_context,
            "snapshot": snapshot.model_copy(update={"completed_daily_bars": bars}),
        }
    )
    assert result.setups == ()
    assert result.exclusions


def test_live_setup_assessment_excludes_completed_bars_retrieved_after_cutoff(
    breakout_context,
):
    snapshot = breakout_context["snapshot"]
    late_retrieval = _CUTOFF + timedelta(hours=4)
    bars = tuple(
        bar.model_copy(update={"retrieved_at": late_retrieval})
        for bar in snapshot.completed_daily_bars
    )

    result = assess_setups(
        **{
            **breakout_context,
            "snapshot": snapshot.model_copy(update={"completed_daily_bars": bars}),
        }
    )

    assert result.setups == ()
    assert "INVALID_EVIDENCE" in result.exclusions[0].reason_codes


def test_negative_benchmark_relative_strength_excludes_breakout(breakout_context):
    fast = _snapshot("SPY", tuple(str(20 + 10 * i) for i in range(11)), volume=1000000)
    result = assess_setups(**{**breakout_context, "benchmark": fast})
    assert not result.setups
    assert "RELATIVE_STRENGTH_FAILED" in result.exclusions[0].reason_codes


def test_excessive_extension_and_invalid_policy_fail_closed(breakout_context):
    policy = breakout_context["setup_policy"].model_copy(
        update={
            "extension_limits": (Decimal("0.001"), Decimal("0.002")),
        }
    )
    result = assess_setups(**{**breakout_context, "setup_policy": policy})
    assert not result.setups
    assert "EXTENSION_LIMIT" in result.exclusions[0].reason_codes
    with pytest.raises(ValueError, match="policy"):
        detect_setups(
            **{
                **breakout_context,
                "setup_policy": policy.model_copy(
                    update={"restrengthening_conditions": ("invented",)}
                ),
            }
        )


def test_calculated_levels_have_complete_inputs_and_exact_worst_entry_risk(breakout_context):
    setup = detect_setups(**breakout_context)[0]
    # Last three true ranges: 3, 4, 2.5; ATR = 19/6.
    assert setup.entry_zone.lower.value.quantize(Decimal("0.0001")) == Decimal("100.7917")
    assert setup.entry_zone.upper.value.quantize(Decimal("0.0001")) == Decimal("101.5833")
    assert setup.candidate_stop.value.quantize(Decimal("0.0001")) == Decimal("95.5083")
    assert setup.entry_zone.lower.input_evidence_ids
    assert setup.metrics
    assert setup.input_snapshots[0] == breakout_context["snapshot"]
    for target in setup.target_scenarios:
        ratio = (target.price.value - setup.entry_zone.upper.value) / (
            setup.entry_zone.upper.value - setup.candidate_stop.value
        )
        assert abs(ratio - target.reward_to_risk) < Decimal("1e-20")


def test_setup_json_roundtrip_preserves_evidence(breakout_context):
    setup = detect_setups(**breakout_context)[0]
    assert type(setup).model_validate_json(setup.model_dump_json()) == setup
