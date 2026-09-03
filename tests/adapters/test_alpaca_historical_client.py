from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest
from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

import finance_research_agent.adapters.alpaca_historical as alpaca_historical
from finance_research_agent.adapters.alpaca import AlpacaDailyBarRecord
from finance_research_agent.adapters.alpaca_historical import AlpacaHistoricalBarsClient
from finance_research_agent.market_data.historical import (
    BarAdjustment,
    HistoricalBarsFailure,
    HistoricalBarsRequestFailure,
    HistoricalBarsRequestFailureReason,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
    HistoricalDailyBarsRequest,
    InvalidMarketDataError,
    MarketDataFeed,
)

CUTOFF = datetime(2026, 8, 25, 12, 45, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 8, 25, 12, 40, tzinfo=UTC)


class _FakeStockHistoricalDataClient:
    def __init__(self, result: object) -> None:
        self.result = result
        self.requests: list[StockBarsRequest] = []

    def get_stock_bars(self, request_params: StockBarsRequest) -> object:
        self.requests.append(request_params)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _FakeHttpError:
    def __init__(self, status_code: int) -> None:
        self.response = SimpleNamespace(status_code=status_code)


def _request(
    *,
    symbols: tuple[str, ...] = ("SPY",),
    feed: MarketDataFeed = MarketDataFeed.IEX,
    adjustment: BarAdjustment = BarAdjustment.SPLIT,
) -> HistoricalDailyBarsRequest:
    return HistoricalDailyBarsRequest(
        symbols=symbols,
        start_at=datetime(2026, 8, 21, 4, tzinfo=UTC),
        end_at=datetime(2026, 8, 25, 3, 59, 59, tzinfo=UTC),
        expected_sessions=(date(2026, 8, 21), date(2026, 8, 24)),
        completed_through_session=date(2026, 8, 24),
        feed=feed,
        adjustment=adjustment,
        evidence_cutoff_at=CUTOFF,
    )


def _raw_bar(
    session: date,
    *,
    close: float = 101.0,
    volume: float = 1_000_000.0,
) -> dict[str, object]:
    return {
        "t": datetime(session.year, session.month, session.day, 4, tzinfo=UTC),
        "o": 100.0,
        "h": 102.0,
        "l": 99.0,
        "c": close,
        "v": volume,
        "n": 1.0,
        "vw": 100.5,
    }


def _complete_bar_set(*symbols: str) -> BarSet:
    return BarSet(
        {
            symbol: [
                _raw_bar(date(2026, 8, 24), close=101.25),
                _raw_bar(date(2026, 8, 21), close=100.1),
            ]
            for symbol in symbols
        }
    )


def _client(
    result: object,
    *,
    retrieved_at: datetime = RETRIEVED_AT,
) -> tuple[AlpacaHistoricalBarsClient, _FakeStockHistoricalDataClient]:
    sdk_client = _FakeStockHistoricalDataClient(result)
    client = AlpacaHistoricalBarsClient(
        cast(StockHistoricalDataClient, sdk_client),
        clock=lambda: retrieved_at,
    )
    return client, sdk_client


@pytest.mark.parametrize(
    ("feed", "expected_feed"),
    [
        (MarketDataFeed.IEX, DataFeed.IEX),
        (MarketDataFeed.SIP, DataFeed.SIP),
    ],
)
@pytest.mark.parametrize(
    ("adjustment", "expected_adjustment"),
    [
        (BarAdjustment.RAW, Adjustment.RAW),
        (BarAdjustment.SPLIT, Adjustment.SPLIT),
        (BarAdjustment.DIVIDEND, Adjustment.DIVIDEND),
        (BarAdjustment.ALL, Adjustment.ALL),
    ],
)
def test_request_maps_exhaustively_to_stock_bars_request(
    feed: MarketDataFeed,
    expected_feed: DataFeed,
    adjustment: BarAdjustment,
    expected_adjustment: Adjustment,
) -> None:
    request = _request(feed=feed, adjustment=adjustment)
    client, sdk_client = _client(_complete_bar_set("SPY"))

    result = client.fetch_daily_bars(request)

    assert isinstance(result, tuple)
    assert len(sdk_client.requests) == 1
    sdk_request = sdk_client.requests[0]
    assert sdk_request.symbol_or_symbols == ["SPY"]
    assert str(sdk_request.timeframe) == str(TimeFrame.Day) == "1Day"
    assert sdk_request.start == request.start_at.replace(tzinfo=None)
    assert sdk_request.end == request.end_at.replace(tzinfo=None)
    assert sdk_request.feed is expected_feed
    assert sdk_request.adjustment is expected_adjustment
    assert sdk_request.limit is None
    assert sdk_request.sort is None
    assert sdk_request.currency is None
    assert sdk_request.asof is None
    request_fields = sdk_request.to_request_fields()
    assert request_fields["symbols"] == "SPY"
    assert request_fields["start"] == request.start_at.isoformat()
    assert request_fields["end"] == request.end_at.isoformat()
    assert str(request_fields["timeframe"]) == "1Day"
    assert request_fields["feed"] is expected_feed
    assert request_fields["adjustment"] is expected_adjustment
    assert "limit" not in request_fields
    assert "page_token" not in request_fields


@pytest.mark.parametrize("error_type", [KeyError, TypeError])
def test_request_mapping_programmer_errors_propagate(
    error_type: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sdk_client = _client(_complete_bar_set("SPY"))

    def fail_mapping(request: HistoricalDailyBarsRequest) -> StockBarsRequest:
        raise error_type("mapping implementation defect")

    monkeypatch.setattr(alpaca_historical, "_to_stock_bars_request", fail_mapping)

    with pytest.raises(error_type, match="mapping implementation defect"):
        client.fetch_daily_bars(_request())

    assert sdk_client.requests == []


def test_request_mapping_validation_failure_is_invalid_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sdk_client = _client(_complete_bar_set("SPY"))

    def reject_request(request: HistoricalDailyBarsRequest) -> StockBarsRequest:
        raise ValueError("SDK request validation failed")

    monkeypatch.setattr(alpaca_historical, "_to_stock_bars_request", reject_request)

    result = client.fetch_daily_bars(_request())

    assert isinstance(result, HistoricalBarsRequestFailure)
    assert result.reason is HistoricalBarsRequestFailureReason.INVALID_REQUEST
    assert sdk_client.requests == []


def test_multi_symbol_bar_set_is_materialized_before_one_normalization_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(symbols=("QQQ", "SPY"))
    client, sdk_client = _client(_complete_bar_set("SPY", "QQQ"))
    calls: list[
        tuple[
            dict[str, tuple[AlpacaDailyBarRecord, ...]],
            HistoricalDailyBarsRequest,
            datetime,
        ]
    ] = []

    def capture_normalization(
        records_by_symbol: dict[str, tuple[AlpacaDailyBarRecord, ...]],
        *,
        request: HistoricalDailyBarsRequest,
        retrieved_at: datetime,
    ) -> tuple[()]:
        calls.append((records_by_symbol, request, retrieved_at))
        return ()

    monkeypatch.setattr(
        alpaca_historical,
        "normalize_alpaca_daily_bars",
        capture_normalization,
    )

    result = client.fetch_daily_bars(request)

    assert result == ()
    assert len(sdk_client.requests) == 1
    assert len(calls) == 1
    records, captured_request, captured_retrieved_at = calls[0]
    assert set(records) == {"QQQ", "SPY"}
    assert all(
        isinstance(item, AlpacaDailyBarRecord)
        for items in records.values()
        for item in items
    )
    assert all(isinstance(items, tuple) for items in records.values())
    assert records["SPY"][0].close == 101.25
    assert captured_request is request
    assert captured_retrieved_at == RETRIEVED_AT


def test_multi_symbol_result_preserves_normalized_public_contract() -> None:
    request = _request(symbols=("QQQ", "SPY"))
    client, _ = _client(_complete_bar_set("SPY", "QQQ"))

    result = client.fetch_daily_bars(request)

    assert isinstance(result, tuple)
    assert tuple(outcome.symbol for outcome in result) == ("QQQ", "SPY")
    assert all(isinstance(outcome, HistoricalDailyBars) for outcome in result)
    spy = cast(HistoricalDailyBars, result[1])
    assert spy.observations[-1].bar.close == Decimal("101.25")
    assert spy.provenance.retrieved_at == RETRIEVED_AT


@pytest.mark.parametrize("bar_set", [_complete_bar_set("SPY"), BarSet({})])
def test_missing_symbol_in_complete_response_remains_data_quality_failure(
    bar_set: BarSet,
) -> None:
    request = _request(symbols=("QQQ", "SPY"))
    client, _ = _client(bar_set)

    result = client.fetch_daily_bars(request)

    assert isinstance(result, tuple)
    assert isinstance(result[0], HistoricalBarsFailure)
    assert result[0].symbol == "QQQ"
    assert result[0].reason is HistoricalBarsUnavailableReason.NO_DATA
    if "SPY" in bar_set.data:
        assert isinstance(result[1], HistoricalDailyBars)
    else:
        assert isinstance(result[1], HistoricalBarsFailure)
        assert result[1].reason is HistoricalBarsUnavailableReason.NO_DATA


def test_unexpected_provider_symbol_is_invalid_response_not_silent_filtering() -> None:
    client, _ = _client(_complete_bar_set("QQQ", "SPY"))

    result = client.fetch_daily_bars(_request())

    assert isinstance(result, HistoricalBarsRequestFailure)
    assert result.reason is HistoricalBarsRequestFailureReason.INVALID_RESPONSE


@pytest.mark.parametrize(
    ("status_code", "expected_reason"),
    [
        (400, HistoricalBarsRequestFailureReason.INVALID_REQUEST),
        (401, HistoricalBarsRequestFailureReason.AUTHENTICATION),
        (403, HistoricalBarsRequestFailureReason.PERMISSION_DENIED),
        (408, HistoricalBarsRequestFailureReason.TRANSPORT_UNAVAILABLE),
        (422, HistoricalBarsRequestFailureReason.INVALID_REQUEST),
        (429, HistoricalBarsRequestFailureReason.RATE_LIMITED),
        (500, HistoricalBarsRequestFailureReason.PROVIDER_UNAVAILABLE),
        (503, HistoricalBarsRequestFailureReason.PROVIDER_UNAVAILABLE),
    ],
)
def test_http_provider_failures_are_typed_and_request_global(
    status_code: int,
    expected_reason: HistoricalBarsRequestFailureReason,
) -> None:
    error = APIError(
        '{"code": "secret-sentinel", "message": "secret-sentinel"}',
        _FakeHttpError(status_code),
    )
    client, sdk_client = _client(error)

    result = client.fetch_daily_bars(_request())

    assert isinstance(result, HistoricalBarsRequestFailure)
    assert result.reason is expected_reason
    assert not hasattr(result, "status_code")
    assert not hasattr(result, "retryable")
    assert "secret-sentinel" not in repr(result)
    assert len(sdk_client.requests) == 1


def test_provider_error_without_status_is_neutral_provider_unavailable() -> None:
    client, _ = _client(APIError('{"code": 0, "message": "unknown"}'))

    result = client.fetch_daily_bars(_request())

    assert isinstance(result, HistoricalBarsRequestFailure)
    assert result.reason is HistoricalBarsRequestFailureReason.PROVIDER_UNAVAILABLE
    assert not hasattr(result, "status_code")
    assert not hasattr(result, "retryable")


def test_transport_failure_is_typed_and_does_not_reach_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sdk_client = _client(OSError("network unavailable"))

    def fail_if_called(*args: object, **kwargs: object) -> tuple[()]:
        raise AssertionError("normalization must not receive a partial provider response")

    monkeypatch.setattr(
        alpaca_historical,
        "normalize_alpaca_daily_bars",
        fail_if_called,
    )

    result = client.fetch_daily_bars(_request())

    assert isinstance(result, HistoricalBarsRequestFailure)
    assert result.reason is HistoricalBarsRequestFailureReason.TRANSPORT_UNAVAILABLE
    assert len(sdk_client.requests) == 1


def test_raw_or_unrecognized_sdk_response_is_invalid_response() -> None:
    client, _ = _client({"bars": {}})

    result = client.fetch_daily_bars(_request())

    assert isinstance(result, HistoricalBarsRequestFailure)
    assert result.reason is HistoricalBarsRequestFailureReason.INVALID_RESPONSE


def test_sdk_response_parsing_failure_is_invalid_response() -> None:
    client, _ = _client(ValueError("provider schema drift"))

    result = client.fetch_daily_bars(_request())

    assert isinstance(result, HistoricalBarsRequestFailure)
    assert result.reason is HistoricalBarsRequestFailureReason.INVALID_RESPONSE


@pytest.mark.parametrize("error_type", [AttributeError, TypeError, ValueError])
def test_materialization_shape_errors_are_invalid_response(
    error_type: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(_complete_bar_set("SPY"))

    def fail_materialization(
        response: BarSet,
    ) -> dict[str, tuple[AlpacaDailyBarRecord, ...]]:
        raise error_type("malformed SDK bar")

    monkeypatch.setattr(alpaca_historical, "_materialize_bar_set", fail_materialization)

    result = client.fetch_daily_bars(_request())

    assert isinstance(result, HistoricalBarsRequestFailure)
    assert result.reason is HistoricalBarsRequestFailureReason.INVALID_RESPONSE


@pytest.mark.parametrize("error_type", [AttributeError, TypeError, ValueError])
def test_normalizer_programmer_errors_propagate(
    error_type: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(_complete_bar_set("SPY"))

    def fail_normalization(*args: object, **kwargs: object) -> tuple[()]:
        raise error_type("normalizer implementation defect")

    monkeypatch.setattr(
        alpaca_historical,
        "normalize_alpaca_daily_bars",
        fail_normalization,
    )

    with pytest.raises(error_type, match="normalizer implementation defect"):
        client.fetch_daily_bars(_request())


@pytest.mark.parametrize(
    "retrieved_at",
    [
        datetime(2026, 8, 25, 12, 40),
        datetime(2026, 8, 25, 3, 59, 58, tzinfo=UTC),
        CUTOFF + timedelta(microseconds=1),
    ],
)
def test_invalid_retrieval_time_fails_before_normalization(
    retrieved_at: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sdk_client = _client(_complete_bar_set("SPY"), retrieved_at=retrieved_at)

    def fail_if_called(*args: object, **kwargs: object) -> tuple[()]:
        raise AssertionError("invalid point-in-time evidence must not be normalized")

    monkeypatch.setattr(
        alpaca_historical,
        "normalize_alpaca_daily_bars",
        fail_if_called,
    )

    with pytest.raises(InvalidMarketDataError, match="retrieved_at"):
        client.fetch_daily_bars(_request())

    assert len(sdk_client.requests) == 1
