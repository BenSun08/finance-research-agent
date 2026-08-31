"""Concrete Alpaca SDK client for completed historical daily stock bars."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from finance_research_agent.adapters.alpaca import (
    AlpacaDailyBarRecord,
    normalize_alpaca_daily_bars,
)
from finance_research_agent.market_data.historical import (
    BarAdjustment,
    HistoricalBarsOutcome,
    HistoricalDailyBarsRequest,
    InvalidMarketDataError,
    MarketDataFeed,
)

__all__ = [
    "AlpacaHistoricalBarsClient",
    "AlpacaHistoricalBarsFetchResult",
    "AlpacaProviderFailure",
    "AlpacaProviderFailureReason",
]


class AlpacaProviderFailureReason(StrEnum):
    """Request-global failures produced before trustworthy normalization."""

    AUTHENTICATION = "authentication"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True, slots=True)
class AlpacaProviderFailure:
    """Redacted Alpaca request failure that contains no provider payload."""

    reason: AlpacaProviderFailureReason
    status_code: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.reason, AlpacaProviderFailureReason):
            raise ValueError("provider failure requires a typed reason")
        if self.status_code is not None and (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("provider status code must be an HTTP status or None")

    @property
    def retryable(self) -> bool:
        """Whether later orchestration may consider retrying this failure."""

        if self.reason in {
            AlpacaProviderFailureReason.RATE_LIMITED,
            AlpacaProviderFailureReason.TRANSPORT_UNAVAILABLE,
        }:
            return True
        return (
            self.reason is AlpacaProviderFailureReason.PROVIDER_UNAVAILABLE
            and self.status_code is not None
            and 500 <= self.status_code <= 599
        )


type AlpacaHistoricalBarsFetchResult = (
    tuple[HistoricalBarsOutcome, ...] | AlpacaProviderFailure
)


_FEED_MAP = {
    MarketDataFeed.IEX: DataFeed.IEX,
    MarketDataFeed.SIP: DataFeed.SIP,
}

_ADJUSTMENT_MAP = {
    BarAdjustment.RAW: Adjustment.RAW,
    BarAdjustment.SPLIT: Adjustment.SPLIT,
    BarAdjustment.DIVIDEND: Adjustment.DIVIDEND,
    BarAdjustment.ALL: Adjustment.ALL,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _request_failure(reason: AlpacaProviderFailureReason) -> AlpacaProviderFailure:
    return AlpacaProviderFailure(reason=reason, status_code=None)


def _classify_api_error(error: APIError) -> AlpacaProviderFailure:
    raw_status_code = error.status_code
    status_code = (
        raw_status_code
        if isinstance(raw_status_code, int) and not isinstance(raw_status_code, bool)
        else None
    )
    if status_code == 401:
        reason = AlpacaProviderFailureReason.AUTHENTICATION
    elif status_code == 403:
        reason = AlpacaProviderFailureReason.PERMISSION_DENIED
    elif status_code == 408:
        reason = AlpacaProviderFailureReason.TRANSPORT_UNAVAILABLE
    elif status_code == 429:
        reason = AlpacaProviderFailureReason.RATE_LIMITED
    elif status_code is not None and 400 <= status_code <= 499:
        reason = AlpacaProviderFailureReason.INVALID_REQUEST
    else:
        reason = AlpacaProviderFailureReason.PROVIDER_UNAVAILABLE
    return AlpacaProviderFailure(reason=reason, status_code=status_code)


def _to_stock_bars_request(request: HistoricalDailyBarsRequest) -> StockBarsRequest:
    return StockBarsRequest(
        symbol_or_symbols=list(request.symbols),
        timeframe=TimeFrame.Day,
        start=request.start_at,
        end=request.end_at,
        feed=_FEED_MAP[request.feed],
        adjustment=_ADJUSTMENT_MAP[request.adjustment],
    )


def _validate_retrieved_at(
    retrieved_at: datetime,
    request: HistoricalDailyBarsRequest,
) -> None:
    if (
        not isinstance(retrieved_at, datetime)
        or retrieved_at.tzinfo is None
        or retrieved_at.utcoffset() != timedelta(0)
    ):
        raise InvalidMarketDataError("retrieved_at must be timezone-aware UTC")
    if retrieved_at < request.end_at:
        raise InvalidMarketDataError("retrieved_at cannot be before request end_at")
    if retrieved_at > request.evidence_cutoff_at:
        raise InvalidMarketDataError("retrieved_at cannot be after evidence cutoff")


def _materialize_bar_set(
    bar_set: BarSet,
) -> dict[str, tuple[AlpacaDailyBarRecord, ...]]:
    return {
        symbol: tuple(
            AlpacaDailyBarRecord(
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in bars
        )
        for symbol, bars in bar_set.data.items()
    }


class AlpacaHistoricalBarsClient:
    """Fetch, materialize, and normalize completed Alpaca daily stock bars."""

    def __init__(
        self,
        sdk_client: StockHistoricalDataClient,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._sdk_client = sdk_client
        self._clock = clock

    def fetch_daily_bars(
        self,
        request: HistoricalDailyBarsRequest,
    ) -> AlpacaHistoricalBarsFetchResult:
        """Fetch one complete multi-symbol response and normalize it atomically."""

        if not isinstance(request, HistoricalDailyBarsRequest):
            raise InvalidMarketDataError("request must be HistoricalDailyBarsRequest")

        try:
            sdk_request = _to_stock_bars_request(request)
        except ValueError:
            return _request_failure(AlpacaProviderFailureReason.INVALID_REQUEST)

        try:
            response = self._sdk_client.get_stock_bars(sdk_request)
        except APIError as error:
            return _classify_api_error(error)
        except (TypeError, ValueError):
            return _request_failure(AlpacaProviderFailureReason.INVALID_RESPONSE)
        except OSError:
            return _request_failure(AlpacaProviderFailureReason.TRANSPORT_UNAVAILABLE)

        retrieved_at = self._clock()
        _validate_retrieved_at(retrieved_at, request)

        if not isinstance(response, BarSet):
            return _request_failure(AlpacaProviderFailureReason.INVALID_RESPONSE)

        try:
            records_by_symbol = _materialize_bar_set(response)
        except (AttributeError, TypeError, ValueError):
            return _request_failure(AlpacaProviderFailureReason.INVALID_RESPONSE)

        try:
            return normalize_alpaca_daily_bars(
                records_by_symbol,
                request=request,
                retrieved_at=retrieved_at,
            )
        except InvalidMarketDataError:
            return _request_failure(AlpacaProviderFailureReason.INVALID_RESPONSE)
