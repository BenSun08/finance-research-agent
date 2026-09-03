from datetime import UTC, date, datetime
from typing import cast

import pytest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.requests import StockBarsRequest

import finance_research_agent.application.regime_research as research_module
from finance_research_agent.adapters.alpaca_historical import AlpacaHistoricalBarsClient
from finance_research_agent.application.ports import HistoricalBarsFetcher
from finance_research_agent.application.regime_research import run_regime_research
from finance_research_agent.domain.regime import (
    RegimePolicy,
    RegimeResult,
    calculate_regime,
)
from finance_research_agent.market_data.historical import (
    FAILURE_SCHEMA_VERSION,
    BarAdjustment,
    HistoricalBarsFailure,
    HistoricalBarsFetchResult,
    HistoricalBarsOutcome,
    HistoricalBarsProvenance,
    HistoricalBarsRequestFailure,
    HistoricalBarsRequestFailureReason,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBarsRequest,
    MarketDataCoverage,
    MarketDataFeed,
)

CUTOFF = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 8, 25, 12, 40, tzinfo=UTC)


def _request() -> HistoricalDailyBarsRequest:
    return HistoricalDailyBarsRequest(
        symbols=("SPY",),
        start_at=datetime(2026, 8, 21, 4, tzinfo=UTC),
        end_at=datetime(2026, 8, 25, 3, 59, 59, tzinfo=UTC),
        expected_sessions=(date(2026, 8, 21), date(2026, 8, 24)),
        completed_through_session=date(2026, 8, 24),
        feed=MarketDataFeed.IEX,
        adjustment=BarAdjustment.SPLIT,
        evidence_cutoff_at=CUTOFF,
    )


def _failure() -> HistoricalBarsFailure:
    return HistoricalBarsFailure(
        schema_version=FAILURE_SCHEMA_VERSION,
        symbol="SPY",
        reason=HistoricalBarsUnavailableReason.NO_DATA,
        provenance=HistoricalBarsProvenance(
            provider="test-provider",
            feed=MarketDataFeed.IEX,
            coverage=MarketDataCoverage.SINGLE_EXCHANGE,
            adjustment=BarAdjustment.SPLIT,
            requested_start_at=datetime(2026, 8, 21, 4, tzinfo=UTC),
            requested_end_at=datetime(2026, 8, 25, 3, 59, 59, tzinfo=UTC),
            retrieved_at=RETRIEVED_AT,
            evidence_cutoff_at=CUTOFF,
            completed_through_session=date(2026, 8, 24),
            adapter_version="test-adapter-v1",
        ),
        missing_sessions=(date(2026, 8, 24),),
        quality_flags=("NO_DATA",),
    )


class _FakeHistoricalBarsFetcher:
    def __init__(self, result: HistoricalBarsFetchResult) -> None:
        self._result = result
        self.requests: list[HistoricalDailyBarsRequest] = []

    def fetch_daily_bars(
        self,
        request: HistoricalDailyBarsRequest,
    ) -> HistoricalBarsFetchResult:
        self.requests.append(request)
        return self._result


class _FailingHistoricalBarsFetcher:
    def __init__(self, error: RuntimeError) -> None:
        self._error = error
        self.requests: list[HistoricalDailyBarsRequest] = []

    def fetch_daily_bars(
        self,
        request: HistoricalDailyBarsRequest,
    ) -> HistoricalBarsFetchResult:
        self.requests.append(request)
        raise self._error


def test_success_fetches_once_and_passes_exact_inputs_to_inner_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    outcomes: tuple[HistoricalBarsOutcome, ...] = (_failure(),)
    fetcher = _FakeHistoricalBarsFetcher(outcomes)
    policy = RegimePolicy()
    expected = calculate_regime({}, policy, CUTOFF)
    calls: list[tuple[tuple[HistoricalBarsOutcome, ...], RegimePolicy, datetime]] = []

    def capture_workflow(
        received_outcomes: tuple[HistoricalBarsOutcome, ...],
        received_policy: RegimePolicy,
        cutoff_at: datetime,
    ) -> RegimeResult:
        calls.append((received_outcomes, received_policy, cutoff_at))
        return expected

    monkeypatch.setattr(research_module, "run_regime_workflow", capture_workflow)

    result = run_regime_research(fetcher, request, policy, CUTOFF)

    assert isinstance(result, RegimeResult)
    assert result is expected
    assert len(fetcher.requests) == 1
    assert fetcher.requests[0] is request
    assert len(calls) == 1
    received_outcomes, received_policy, received_cutoff = calls[0]
    assert received_outcomes is outcomes
    assert received_policy is policy
    assert received_cutoff is CUTOFF


def test_request_global_failure_is_returned_unchanged_without_inner_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    failure = HistoricalBarsRequestFailure(
        reason=HistoricalBarsRequestFailureReason.RATE_LIMITED
    )
    fetcher = _FakeHistoricalBarsFetcher(failure)
    inner_calls = 0

    def fail_if_called(
        outcomes: tuple[HistoricalBarsOutcome, ...],
        policy: RegimePolicy,
        cutoff_at: datetime,
    ) -> RegimeResult:
        nonlocal inner_calls
        inner_calls += 1
        raise AssertionError("request-global failure must bypass the inner workflow")

    monkeypatch.setattr(research_module, "run_regime_workflow", fail_if_called)

    result = run_regime_research(fetcher, request, RegimePolicy(), CUTOFF)

    assert result is failure
    assert len(fetcher.requests) == 1
    assert fetcher.requests[0] is request
    assert inner_calls == 0


def test_fetcher_programmer_error_propagates() -> None:
    request = _request()
    programmer_error = RuntimeError("fetcher bug")
    fetcher = _FailingHistoricalBarsFetcher(programmer_error)

    with pytest.raises(RuntimeError, match="fetcher bug") as raised:
        run_regime_research(fetcher, request, RegimePolicy(), CUTOFF)

    assert raised.value is programmer_error
    assert fetcher.requests == [request]


def test_inner_workflow_programmer_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    programmer_error = RuntimeError("inner workflow bug")
    fetcher = _FakeHistoricalBarsFetcher((_failure(),))

    def fail_workflow(
        outcomes: tuple[HistoricalBarsOutcome, ...],
        policy: RegimePolicy,
        cutoff_at: datetime,
    ) -> RegimeResult:
        raise programmer_error

    monkeypatch.setattr(research_module, "run_regime_workflow", fail_workflow)

    with pytest.raises(RuntimeError, match="inner workflow bug") as raised:
        run_regime_research(fetcher, _request(), RegimePolicy(), CUTOFF)

    assert raised.value is programmer_error


class _FakeStockHistoricalDataClient:
    def __init__(self, response: BarSet) -> None:
        self._response = response

    def get_stock_bars(self, request_params: StockBarsRequest) -> BarSet:
        return self._response


def test_alpaca_client_structurally_satisfies_historical_bars_fetcher() -> None:
    response = BarSet(
        {
            "SPY": [
                {
                    "t": datetime(2026, 8, 21, 4, tzinfo=UTC),
                    "o": 100.0,
                    "h": 102.0,
                    "l": 99.0,
                    "c": 101.0,
                    "v": 1_000_000.0,
                    "n": 1.0,
                    "vw": 100.5,
                },
                {
                    "t": datetime(2026, 8, 24, 4, tzinfo=UTC),
                    "o": 101.0,
                    "h": 103.0,
                    "l": 100.0,
                    "c": 102.0,
                    "v": 1_100_000.0,
                    "n": 1.0,
                    "vw": 101.5,
                },
            ]
        }
    )
    sdk_client = _FakeStockHistoricalDataClient(response)
    fetcher: HistoricalBarsFetcher = AlpacaHistoricalBarsClient(
        cast(StockHistoricalDataClient, sdk_client),
        clock=lambda: RETRIEVED_AT,
    )

    result = run_regime_research(fetcher, _request(), RegimePolicy(), CUTOFF)

    assert isinstance(result, RegimeResult)
