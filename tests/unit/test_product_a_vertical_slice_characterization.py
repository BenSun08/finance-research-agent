from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from importlib import import_module
from typing import cast

import pytest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.requests import StockBarsRequest

from finance_research_agent.adapters.alpaca import (
    AlpacaDailyBarRecord,
    normalize_alpaca_daily_bars,
)
from finance_research_agent.adapters.alpaca_historical import AlpacaHistoricalBarsClient
from finance_research_agent.application import run_regime_research, run_regime_workflow
from finance_research_agent.domain.indicators import sma, sma_slope
from finance_research_agent.domain.market import (
    DailyBar,
    InvalidMarketDataError,
    MarketDataSource,
)
from finance_research_agent.domain.metrics import MetricDirection, MetricStatus
from finance_research_agent.domain.regime import (
    Regime,
    RegimeComponentState,
    RegimePolicy,
    RegimeResult,
)
from finance_research_agent.evals import RegimeEvalCase, RegimeReplayCase
from finance_research_agent.market_data import (
    BarAdjustment,
    DailyBarObservation,
    HistoricalBarsFailure,
    HistoricalBarsFetchResult,
    HistoricalBarsProvenance,
    HistoricalBarsRequestFailure,
    HistoricalBarsRequestFailureReason,
    HistoricalDailyBars,
    HistoricalDailyBarsRequest,
    MarketDataCoverage,
    MarketDataFeed,
    to_market_snapshot,
)
from tests.support.synthetic_market import make_regime_case

FIXTURE_CUTOFF = datetime(2026, 8, 26, 12, 45, tzinfo=UTC)
FIXTURE_RETRIEVED_AT = datetime(2026, 8, 26, 12, 40, tzinfo=UTC)
LIVE_CUTOFF = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)
LIVE_RETRIEVED_AT = datetime(2026, 8, 25, 12, 40, tzinfo=UTC)
LATE_RETRIEVED_AT = LIVE_CUTOFF + timedelta(microseconds=1)
SIMPLE_SESSIONS = (date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25))

PUBLIC_COMPATIBILITY_IMPORTS = (
    "finance_research_agent.domain.market.MarketSnapshot",
    "finance_research_agent.domain.metrics.MetricResult",
    "finance_research_agent.domain.regime.RegimePolicy",
    "finance_research_agent.domain.regime.RegimeResult",
    "finance_research_agent.domain.indicators.sma",
    "finance_research_agent.adapters.alpaca_historical.AlpacaHistoricalBarsClient",
    "finance_research_agent.market_data.HistoricalDailyBars",
    "finance_research_agent.market_data.to_market_snapshot",
    "finance_research_agent.application.HistoricalBarsFetcher",
    "finance_research_agent.application.run_regime_research",
    "finance_research_agent.application.run_regime_workflow",
    "finance_research_agent.evals.RegimeReplayCase",
    "finance_research_agent.agent.AgentRuntime",
)


@dataclass
class _StaticFetcher:
    result: HistoricalBarsFetchResult

    def fetch_daily_bars(
        self,
        request: HistoricalDailyBarsRequest,
    ) -> HistoricalBarsFetchResult:
        return self.result


class _EmptyStockHistoricalDataClient:
    def get_stock_bars(self, request_params: StockBarsRequest) -> BarSet:
        return BarSet({})


def _live_request(*, symbols: tuple[str, ...] = ("SPY",)) -> HistoricalDailyBarsRequest:
    return HistoricalDailyBarsRequest(
        symbols=symbols,
        start_at=datetime(2026, 8, 21, 4, tzinfo=UTC),
        end_at=datetime(2026, 8, 25, 3, 59, 59, tzinfo=UTC),
        expected_sessions=(date(2026, 8, 21), date(2026, 8, 24)),
        completed_through_session=date(2026, 8, 24),
        feed=MarketDataFeed.IEX,
        adjustment=BarAdjustment.SPLIT,
        evidence_cutoff_at=LIVE_CUTOFF,
    )


def _simple_history() -> HistoricalDailyBars:
    bars = tuple(
        DailyBar(
            session_date=session,
            open=close - Decimal("1"),
            high=close + Decimal("1"),
            low=close - Decimal("2"),
            close=close,
            volume=1_000_000,
        )
        for session, close in zip(
            SIMPLE_SESSIONS,
            (Decimal("100"), Decimal("102"), Decimal("104")),
            strict=True,
        )
    )
    observations = tuple(
        DailyBarObservation(
            source_timestamp=datetime.combine(
                bar.session_date,
                time(21),
                tzinfo=UTC,
            ),
            bar=bar,
        )
        for bar in bars
    )
    return HistoricalDailyBars.create(
        symbol="SPY",
        observations=observations,
        provenance=HistoricalBarsProvenance(
            provider="fixture",
            feed=MarketDataFeed.IEX,
            coverage=MarketDataCoverage.SINGLE_EXCHANGE,
            adjustment=BarAdjustment.RAW,
            requested_start_at=observations[0].source_timestamp,
            requested_end_at=observations[-1].source_timestamp,
            retrieved_at=FIXTURE_RETRIEVED_AT,
            evidence_cutoff_at=FIXTURE_CUTOFF,
            completed_through_session=SIMPLE_SESSIONS[-1],
            adapter_version="r0-fixture-v1",
        ),
        quality_flags=("CHARACTERIZATION",),
    )


def _history_from_synthetic_snapshot(symbol: str) -> HistoricalDailyBars:
    snapshot = make_regime_case("risk-on").snapshots[symbol]
    observations = tuple(
        DailyBarObservation(
            source_timestamp=datetime.combine(
                bar.session_date,
                time(21),
                tzinfo=UTC,
            ),
            bar=bar,
        )
        for bar in snapshot.completed_daily_bars
    )
    return HistoricalDailyBars.create(
        symbol=symbol,
        observations=observations,
        provenance=HistoricalBarsProvenance(
            provider="fixture",
            feed=MarketDataFeed.IEX,
            coverage=MarketDataCoverage.SINGLE_EXCHANGE,
            adjustment=BarAdjustment.RAW,
            requested_start_at=observations[0].source_timestamp,
            requested_end_at=observations[-1].source_timestamp,
            retrieved_at=FIXTURE_RETRIEVED_AT,
            evidence_cutoff_at=FIXTURE_CUTOFF,
            completed_through_session=observations[-1].bar.session_date,
            adapter_version="r0-fixture-v1",
        ),
        quality_flags=("CHARACTERIZATION",),
    )


def _neutral_late_retrieval() -> HistoricalBarsProvenance:
    request = _live_request()
    return HistoricalBarsProvenance(
        provider="offline-fixture",
        feed=request.feed,
        coverage=MarketDataCoverage.SINGLE_EXCHANGE,
        adjustment=request.adjustment,
        requested_start_at=request.start_at,
        requested_end_at=request.end_at,
        retrieved_at=LATE_RETRIEVED_AT,
        evidence_cutoff_at=request.evidence_cutoff_at,
        completed_through_session=request.completed_through_session,
        adapter_version="offline-fixture-v1",
    )


def _normalize_live_late_retrieval() -> object:
    return normalize_alpaca_daily_bars(
        {},
        request=_live_request(),
        retrieved_at=LATE_RETRIEVED_AT,
    )


def _fetch_live_late_retrieval() -> object:
    client = AlpacaHistoricalBarsClient(
        cast(StockHistoricalDataClient, _EmptyStockHistoricalDataClient()),
        clock=lambda: LATE_RETRIEVED_AT,
    )
    return client.fetch_daily_bars(_live_request())


@pytest.mark.parametrize("qualified_name", PUBLIC_COMPATIBILITY_IMPORTS)
def test_r0_public_contract_inventory_remains_importable(qualified_name: str) -> None:
    module_name, attribute_name = qualified_name.rsplit(".", 1)

    module = import_module(module_name)

    assert getattr(module, attribute_name) is not None


def test_available_failure_and_request_failure_ownership_remains_distinct() -> None:
    request = _live_request(symbols=("QQQ", "SPY"))
    records = {
        "SPY": (
            AlpacaDailyBarRecord(
                symbol="SPY",
                timestamp=datetime(2026, 8, 21, 4, tzinfo=UTC),
                open=100.0,
                high=102.0,
                low=99.0,
                close=101.0,
                volume=1_000_000.0,
            ),
            AlpacaDailyBarRecord(
                symbol="SPY",
                timestamp=datetime(2026, 8, 24, 4, tzinfo=UTC),
                open=101.0,
                high=103.0,
                low=100.0,
                close=102.0,
                volume=1_100_000.0,
            ),
        )
    }

    outcomes = normalize_alpaca_daily_bars(
        records,
        request=request,
        retrieved_at=LIVE_RETRIEVED_AT,
    )
    request_failure = HistoricalBarsRequestFailure(
        reason=HistoricalBarsRequestFailureReason.RATE_LIMITED
    )

    assert tuple((outcome.symbol, type(outcome)) for outcome in outcomes) == (
        ("QQQ", HistoricalBarsFailure),
        ("SPY", HistoricalDailyBars),
    )
    assert not hasattr(outcomes[0], "observations")
    assert not hasattr(request_failure, "symbol")
    assert (
        run_regime_research(
            _StaticFetcher(request_failure),
            request,
            RegimePolicy(),
            LIVE_CUTOFF,
        )
        is request_failure
    )


@pytest.mark.parametrize(
    ("operation", "late_retrieval_is_valid"),
    [
        pytest.param(_neutral_late_retrieval, True, id="neutral-offline-provenance"),
        pytest.param(_normalize_live_late_retrieval, False, id="live-normalizer"),
        pytest.param(_fetch_live_late_retrieval, False, id="live-sdk-client"),
    ],
)
def test_late_retrieval_semantics_are_owned_by_the_collection_boundary(
    operation: Callable[[], object],
    late_retrieval_is_valid: bool,
) -> None:
    if late_retrieval_is_valid:
        provenance = operation()
        assert isinstance(provenance, HistoricalBarsProvenance)
        assert provenance.retrieved_at == LATE_RETRIEVED_AT
        assert provenance.evidence_cutoff_at == LIVE_CUTOFF
        return

    with pytest.raises(InvalidMarketDataError, match="after evidence cutoff"):
        operation()


def test_replay_decision_remains_distinct_from_late_dataset_retrieval() -> None:
    history = _simple_history()
    late_history = HistoricalDailyBars.create(
        symbol=history.symbol,
        observations=history.observations,
        provenance=HistoricalBarsProvenance(
            provider=history.provenance.provider,
            feed=history.provenance.feed,
            coverage=history.provenance.coverage,
            adjustment=history.provenance.adjustment,
            requested_start_at=history.provenance.requested_start_at,
            requested_end_at=history.provenance.requested_end_at,
            retrieved_at=FIXTURE_CUTOFF + timedelta(days=1),
            evidence_cutoff_at=history.provenance.evidence_cutoff_at,
            completed_through_session=history.provenance.completed_through_session,
            adapter_version=history.provenance.adapter_version,
        ),
        quality_flags=history.quality_flags,
    )
    case = RegimeEvalCase(
        case_id="r0-late-retrieval",
        outcomes=(late_history,),
        policy=RegimePolicy(),
        cutoff_at=FIXTURE_CUTOFF,
        expected_regime=Regime.UNKNOWN,
    )

    replay = RegimeReplayCase(case=case, decision_at=FIXTURE_CUTOFF)

    assert replay.decision_at == replay.case.cutoff_at == FIXTURE_CUTOFF
    assert late_history.provenance.retrieved_at > replay.decision_at
    assert late_history.provenance.evidence_cutoff_at <= replay.decision_at


def test_historical_projection_preserves_exact_indicator_outputs_and_ids() -> None:
    history = _simple_history()

    snapshot = to_market_snapshot(history)
    average = sma(snapshot, window=2, cutoff_at=FIXTURE_CUTOFF)
    slope = sma_slope(snapshot, window=2, lookback=1, cutoff_at=FIXTURE_CUTOFF)

    assert history.history_id == "history-8d3a4ef56c6c9e81215b3574"
    assert snapshot.snapshot_id == "normalized-8d3a4ef56c6c9e81215b3574"
    assert snapshot.symbol == "SPY"
    assert snapshot.as_of == FIXTURE_CUTOFF
    assert snapshot.currency == "USD"
    assert snapshot.source is MarketDataSource.NORMALIZED_PROVIDER
    assert snapshot.quality_flags == ("CHARACTERIZATION",)
    assert snapshot.completed_daily_bars == tuple(
        observation.bar for observation in history.observations
    )
    assert (
        average.value,
        average.status,
        average.direction,
        average.metric_id,
    ) == (
        Decimal("103"),
        MetricStatus.AVAILABLE,
        MetricDirection.NOT_APPLICABLE,
        "metric-6a63143b40053f8b3ff6f73c",
    )
    assert (slope.value, slope.status, slope.direction, slope.metric_id) == (
        Decimal("2"),
        MetricStatus.AVAILABLE,
        MetricDirection.UP,
        "metric-efed32bc40b48adaac58e98f",
    )


def test_historical_daily_bars_to_regime_projection_has_exact_golden_result() -> None:
    policy = RegimePolicy()
    histories = tuple(
        _history_from_synthetic_snapshot(symbol) for symbol in policy.required_symbols
    )

    result = run_regime_workflow(histories, policy, FIXTURE_CUTOFF)

    assert isinstance(result, RegimeResult)
    assert result.result_id == "regime-3345b300c406c9482c108274"
    assert result.regime is Regime.PERMISSIVE
    assert result.score == Decimal("100")
    assert (
        tuple(component.state for component in result.components)
        == (RegimeComponentState.POSITIVE,) * 5
    )
    assert len(result.metrics) == 38
    assert result.input_snapshot_ids == tuple(
        sorted(f"normalized-{history.history_id.removeprefix('history-')}" for history in histories)
    )
