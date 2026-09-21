"""Offline normalization of Alpaca-shaped completed daily-bar records."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from math import isfinite
from re import fullmatch

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from finance_research_agent.adapters.http_client import (
    AllowedRequest,
    RequestRejected,
    SafeHttpClient,
)
from finance_research_agent.domain.enums import Coverage, Session
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.market import DailyBar
from finance_research_agent.domain.models import (
    InstrumentIdentity,
    PriceObservation,
    ProviderFailure,
    ProviderReadiness,
)
from finance_research_agent.market_data.historical import (
    FAILURE_SCHEMA_VERSION,
    BarAdjustment,
    DailyBarObservation,
    HistoricalBarsFailure,
    HistoricalBarsOutcome,
    HistoricalBarsProvenance,
    HistoricalBarsUnavailableReason,
    HistoricalDailyBars,
    HistoricalDailyBarsRequest,
    InvalidMarketDataError,
    MarketDataFeed,
    coverage_for_feed,
    create_daily_bar_observation,
)
from finance_research_agent.settings import Settings

ADAPTER_VERSION = "alpaca-daily-bars-v1"
MARKET_DATA_ADAPTER = "alpaca"
MARKET_DATA_HOST = "data.alpaca.markets"


class _AlpacaPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _AssetPayload(_AlpacaPayload):
    id: str
    symbol: str
    name: str
    exchange: str
    asset_class: str = Field(alias="class")
    status: str
    tradable: bool
    fractionable: bool


class _BarPayload(_AlpacaPayload):
    timestamp: str = Field(alias="t")
    open: int | float = Field(alias="o")
    high: int | float = Field(alias="h")
    low: int | float = Field(alias="l")
    close: int | float = Field(alias="c")
    volume: int | float = Field(alias="v")


class _BarsPayload(_AlpacaPayload):
    bars: dict[str, tuple[_BarPayload, ...]]
    next_page_token: str | None = None


class _TradePayload(_AlpacaPayload):
    price: int | float = Field(alias="p")
    size: int = Field(alias="s")
    timestamp: str = Field(alias="t")
    exchange: str = Field(alias="x")


class _QuotePayload(_AlpacaPayload):
    ask_price: int | float = Field(alias="ap")
    bid_price: int | float = Field(alias="bp")
    timestamp: str = Field(alias="t")


class _LatestPayload(_AlpacaPayload):
    trades: dict[str, _TradePayload] | None = None
    quotes: dict[str, _QuotePayload] | None = None

    @model_validator(mode="after")
    def _has_latest_values(self) -> "_LatestPayload":
        if self.trades is None and self.quotes is None:
            raise ValueError("latest response must contain trades or quotes")
        if self.trades is not None and self.quotes is not None:
            raise ValueError("latest response must not mix trades and quotes")
        return self


_ASSETS_ADAPTER = TypeAdapter(list[_AssetPayload])


def _utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("provider timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("provider timestamp must be UTC")
    return parsed


def _positive_decimal(value: int | float) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("numeric provider value is invalid")
    decimal = Decimal(str(value))
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError("numeric provider value must be positive and finite")
    return decimal


def _symbol_sequence(symbols: Sequence[str]) -> tuple[str, ...]:
    values = tuple(symbols)
    if any(
        type(symbol) is not str
        or not symbol.isascii()
        or not symbol
        or symbol != symbol.upper()
        or fullmatch(r"[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*", symbol) is None
        for symbol in values
    ):
        raise ValueError("symbols must be uppercase ASCII tickers")
    if len(values) != len(set(values)):
        raise ValueError("symbols must be unique")
    return values


def _evidence_id(symbol: str, payload: object) -> str:
    encoded = json.dumps(
        {"adapter": ADAPTER_VERSION, "symbol": symbol, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"ev-alpaca-{sha256(encoded).hexdigest()[:24]}"


@dataclass(frozen=True, slots=True)
class AlpacaDailyBarRecord:
    """Minimum attributes mirrored from one future Alpaca SDK daily Bar."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def _price(value: float) -> Decimal:
    if not isinstance(value, float) or not isfinite(value):
        raise InvalidMarketDataError("Alpaca bar prices must be finite floats")
    return Decimal(str(value))


def _volume(value: float) -> int:
    if not isinstance(value, float) or not isfinite(value) or value < 0 or not value.is_integer():
        raise InvalidMarketDataError(
            "Alpaca bar volume must be a finite non-negative integer value"
        )
    return int(value)


def _provenance(
    request: HistoricalDailyBarsRequest,
    retrieved_at: datetime,
) -> HistoricalBarsProvenance:
    provenance = HistoricalBarsProvenance(
        provider="alpaca",
        feed=request.feed,
        coverage=coverage_for_feed(request.feed),
        adjustment=request.adjustment,
        requested_start_at=request.start_at,
        requested_end_at=request.end_at,
        retrieved_at=retrieved_at,
        evidence_cutoff_at=request.evidence_cutoff_at,
        completed_through_session=request.completed_through_session,
        adapter_version=ADAPTER_VERSION,
    )
    # Keep the live evidence freeze at the adapter boundary; neutral historical
    # provenance also supports offline datasets retrieved after their cutoff.
    if provenance.retrieved_at > request.evidence_cutoff_at:
        raise InvalidMarketDataError("retrieved_at cannot be after evidence cutoff")
    return provenance


def _base_quality_flags(
    feed: MarketDataFeed,
    adjustment: BarAdjustment,
) -> tuple[str, ...]:
    feed_flag = (
        "FEED_IEX_SINGLE_EXCHANGE" if feed is MarketDataFeed.IEX else "FEED_SIP_CONSOLIDATED_US"
    )
    return tuple(
        sorted(
            (
                f"ADJUSTMENT_{adjustment.value.upper()}",
                feed_flag,
                "SOURCE_ORDER_NORMALIZED",
            )
        )
    )


def _failure(
    *,
    symbol: str,
    reason: HistoricalBarsUnavailableReason,
    provenance: HistoricalBarsProvenance,
    missing_sessions: tuple[date, ...],
    quality_flags: tuple[str, ...],
) -> HistoricalBarsFailure:
    return HistoricalBarsFailure(
        schema_version=FAILURE_SCHEMA_VERSION,
        symbol=symbol,
        reason=reason,
        provenance=provenance,
        missing_sessions=missing_sessions,
        quality_flags=quality_flags,
    )


def _normalize_record(
    record: AlpacaDailyBarRecord,
    symbol: str,
) -> DailyBarObservation:
    if not isinstance(record, AlpacaDailyBarRecord) or record.symbol != symbol:
        raise InvalidMarketDataError("Alpaca bar symbol does not match the requested symbol")
    if record.timestamp.tzinfo is None:
        raise InvalidMarketDataError("Alpaca bar timestamp must be timezone-aware")
    source_timestamp = record.timestamp.astimezone(UTC)
    return create_daily_bar_observation(
        source_timestamp=source_timestamp,
        open_price=_price(record.open),
        high=_price(record.high),
        low=_price(record.low),
        close=_price(record.close),
        volume=_volume(record.volume),
    )


def normalize_alpaca_daily_bars(
    records_by_symbol: Mapping[str, Sequence[AlpacaDailyBarRecord]],
    *,
    request: HistoricalDailyBarsRequest,
    retrieved_at: datetime,
) -> tuple[HistoricalBarsOutcome, ...]:
    """Normalize one already-materialized Alpaca-shaped response without I/O."""

    if not isinstance(records_by_symbol, Mapping):
        raise InvalidMarketDataError("Alpaca records must be keyed by symbol")
    if not isinstance(request, HistoricalDailyBarsRequest):
        raise InvalidMarketDataError("request must be HistoricalDailyBarsRequest")
    unexpected_symbols = set(records_by_symbol) - set(request.symbols)
    if unexpected_symbols:
        raise InvalidMarketDataError("Alpaca response contains unrequested symbols")

    provenance = _provenance(request, retrieved_at)
    base_flags = _base_quality_flags(request.feed, request.adjustment)
    expected_dates = set(request.expected_sessions)
    outcomes: list[HistoricalBarsOutcome] = []

    for symbol in request.symbols:
        provider_records = tuple(records_by_symbol.get(symbol, ()))
        if not provider_records:
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.NO_DATA,
                    provenance=provenance,
                    missing_sessions=request.expected_sessions,
                    quality_flags=base_flags,
                )
            )
            continue

        try:
            normalized = tuple(_normalize_record(record, symbol) for record in provider_records)
        except (AttributeError, InvalidMarketDataError, TypeError, ValueError):
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.MALFORMED_BAR,
                    provenance=provenance,
                    missing_sessions=(),
                    quality_flags=base_flags,
                )
            )
            continue

        by_session: dict[date, DailyBarObservation] = {}
        duplicate_removed = False
        duplicate_conflict = False
        for observation in normalized:
            session = observation.bar.session_date
            existing = by_session.get(session)
            if existing is None:
                by_session[session] = observation
            elif existing == observation:
                duplicate_removed = True
            else:
                duplicate_conflict = True
                break

        if duplicate_conflict:
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.DUPLICATE_CONFLICT,
                    provenance=provenance,
                    missing_sessions=(),
                    quality_flags=base_flags,
                )
            )
            continue

        observations = tuple(by_session[session] for session in sorted(by_session))
        dates = tuple(observation.bar.session_date for observation in observations)
        flags = set(base_flags)
        if duplicate_removed:
            flags.add("EXACT_DUPLICATE_REMOVED")
        quality_flags = tuple(sorted(flags))

        if any(session > request.completed_through_session for session in dates):
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.FUTURE_OR_INCOMPLETE_BAR,
                    provenance=provenance,
                    missing_sessions=(),
                    quality_flags=quality_flags,
                )
            )
            continue
        if any(session not in expected_dates for session in dates):
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.MALFORMED_BAR,
                    provenance=provenance,
                    missing_sessions=(),
                    quality_flags=quality_flags,
                )
            )
            continue

        missing_sessions = tuple(
            session for session in request.expected_sessions if session not in set(dates)
        )
        if dates[-1] < request.completed_through_session:
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.STALE,
                    provenance=provenance,
                    missing_sessions=missing_sessions,
                    quality_flags=quality_flags,
                )
            )
            continue
        if missing_sessions:
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.MISSING_EXPECTED_SESSION,
                    provenance=provenance,
                    missing_sessions=missing_sessions,
                    quality_flags=quality_flags,
                )
            )
            continue

        try:
            outcomes.append(
                HistoricalDailyBars.create(
                    symbol=symbol,
                    observations=observations,
                    provenance=provenance,
                    quality_flags=quality_flags,
                )
            )
        except InvalidMarketDataError:
            outcomes.append(
                _failure(
                    symbol=symbol,
                    reason=HistoricalBarsUnavailableReason.MALFORMED_BAR,
                    provenance=provenance,
                    missing_sessions=(),
                    quality_flags=quality_flags,
                )
            )

    return tuple(outcomes)


class AlpacaMarketDataProvider:
    """Constrained market-data-only Alpaca adapter for Product A collection."""

    def __init__(
        self,
        settings: Settings,
        http_client: SafeHttpClient,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        host: str = MARKET_DATA_HOST,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._clock = clock
        self._host = host

    def readiness(self) -> ProviderReadiness:
        configured = (
            self._settings.alpaca_api_key is not None
            and self._settings.alpaca_api_secret is not None
        )
        return ProviderReadiness(
            provider="alpaca",
            configured=configured,
            available=configured,
            error_code=None if configured else ErrorCode.CREDENTIALS_MISSING,
        )

    def fetch_instruments(
        self, symbols: Sequence[str]
    ) -> dict[str, InstrumentIdentity | ProviderFailure]:
        try:
            requested = _symbol_sequence(symbols)
        except ValueError:
            raw_symbols = tuple(symbols)
            return {
                symbol: self._failure(
                    error_code=ErrorCode.INVALID_REQUEST,
                    retryable=False,
                    symbol=None,
                    message="invalid instrument request",
                )
                for symbol in raw_symbols
                if isinstance(symbol, str)
            }
        if not requested:
            return {}

        request_failure, payload = self._get_json(
            "/v2/assets",
            {"status": "active", "asset_class": "us_equity"},
        )
        if request_failure is not None:
            return {symbol: request_failure for symbol in requested}
        assert payload is not None
        try:
            assets = _ASSETS_ADAPTER.validate_json(payload)
        except ValidationError:
            schema_failure = self._failure(
                error_code=ErrorCode.PROVIDER_SCHEMA_DRIFT,
                retryable=False,
                message="instrument response schema drift",
            )
            return {symbol: schema_failure for symbol in requested}

        by_symbol = {asset.symbol: asset for asset in assets}
        if set(by_symbol) - set(requested):
            schema_failure = self._failure(
                error_code=ErrorCode.PROVIDER_SCHEMA_DRIFT,
                retryable=False,
                message="instrument response contains an unrequested symbol",
            )
            return {symbol: schema_failure for symbol in requested}

        result: dict[str, InstrumentIdentity | ProviderFailure] = {}
        for symbol in requested:
            asset = by_symbol.get(symbol)
            if asset is None:
                result[symbol] = self._failure(
                    error_code=ErrorCode.UNSUPPORTED_INSTRUMENT,
                    retryable=False,
                    symbol=symbol,
                    message="instrument is absent from provider response",
                )
                continue
            if asset.symbol != symbol or asset.asset_class != "us_equity":
                result[symbol] = self._failure(
                    error_code=ErrorCode.PROVIDER_SCHEMA_DRIFT,
                    retryable=False,
                    symbol=symbol,
                    message="instrument identity does not match request",
                )
                continue
            result[symbol] = InstrumentIdentity(
                instrument_id=symbol,
                symbol=symbol,
                name=asset.name,
                instrument_type="COMMON_STOCK",
                primary_exchange=asset.exchange,
                listing_country="US",
                currency="USD",
                is_active=asset.status == "active",
                is_leveraged=None,
                is_inverse=None,
                is_otc=asset.exchange == "OTC",
            )
        return result

    def fetch_daily_bars(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        feed: MarketDataFeed = MarketDataFeed.IEX,
        adjustment: BarAdjustment = BarAdjustment.ALL,
    ) -> dict[str, tuple[DailyBar, ...] | ProviderFailure]:
        try:
            requested = _symbol_sequence(symbols)
        except ValueError:
            raw_symbols = tuple(symbols)
            return {
                symbol: self._failure(
                    error_code=ErrorCode.INVALID_REQUEST,
                    retryable=False,
                    symbol=None,
                    message="invalid daily-bars request",
                )
                for symbol in raw_symbols
                if isinstance(symbol, str)
            }
        if not requested:
            return {}
        if (
            type(start) is not date
            or type(end) is not date
            or end < start
            or not isinstance(feed, MarketDataFeed)
            or not isinstance(adjustment, BarAdjustment)
        ):
            request_failure = self._failure(
                error_code=ErrorCode.INVALID_REQUEST,
                retryable=False,
                message="daily-bars date range is invalid",
            )
            return {symbol: request_failure for symbol in requested}

        query = {
            "symbols": ",".join(requested),
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "feed": feed.value,
            "adjustment": adjustment.value,
            "sort": "asc",
        }
        response_failure, payload = self._get_json("/v2/stocks/bars", query)
        if response_failure is not None:
            return {symbol: response_failure for symbol in requested}
        assert payload is not None
        records: dict[str, list[AlpacaDailyBarRecord]] = {}
        next_payload = payload
        page_tokens: set[str] = set()
        try:
            while True:
                page = _BarsPayload.model_validate_json(next_payload)
                for symbol, bars in page.bars.items():
                    records.setdefault(symbol, []).extend(
                        AlpacaDailyBarRecord(
                            symbol=symbol,
                            timestamp=_utc_timestamp(bar.timestamp),
                            open=float(bar.open),
                            high=float(bar.high),
                            low=float(bar.low),
                            close=float(bar.close),
                            volume=float(bar.volume),
                        )
                        for bar in bars
                    )
                token = page.next_page_token
                if token is None:
                    break
                if token in page_tokens:
                    raise ValueError("provider pagination repeated a token")
                page_tokens.add(token)
                next_failure, fetched_payload = self._get_json(
                    "/v2/stocks/bars",
                    {**query, "page_token": token},
                )
                if next_failure is not None:
                    return {symbol: next_failure for symbol in requested}
                assert fetched_payload is not None
                next_payload = fetched_payload
        except (ValidationError, TypeError, ValueError, OverflowError):
            schema_failure = self._failure(
                error_code=ErrorCode.PROVIDER_SCHEMA_DRIFT,
                retryable=False,
                message="daily-bars response schema drift",
            )
            return {symbol: schema_failure for symbol in requested}

        if set(records) - set(requested):
            schema_failure = self._failure(
                error_code=ErrorCode.PROVIDER_SCHEMA_DRIFT,
                retryable=False,
                message="daily-bars response contains an unrequested symbol",
            )
            return {symbol: schema_failure for symbol in requested}

        expected_sessions = tuple(
            current
            for offset in range((end - start).days + 1)
            if (current := start + timedelta(days=offset)).weekday() < 5
        )
        if not expected_sessions:
            request_failure = self._failure(
                error_code=ErrorCode.INVALID_REQUEST,
                retryable=False,
                message="daily-bars request contains no weekday sessions",
            )
            return {symbol: request_failure for symbol in requested}
        retrieved_at = self._now()
        normalization_request = HistoricalDailyBarsRequest(
            symbols=requested,
            start_at=datetime.combine(start, time.min, tzinfo=UTC),
            end_at=min(datetime.combine(end, time.max, tzinfo=UTC), retrieved_at),
            expected_sessions=expected_sessions,
            completed_through_session=expected_sessions[-1],
            feed=feed,
            adjustment=adjustment,
            evidence_cutoff_at=retrieved_at + timedelta(days=1),
        )
        outcomes = normalize_alpaca_daily_bars(
            records,
            request=normalization_request,
            retrieved_at=retrieved_at,
        )
        result: dict[str, tuple[DailyBar, ...] | ProviderFailure] = {}
        for outcome in outcomes:
            if isinstance(outcome, HistoricalDailyBars):
                result[outcome.symbol] = tuple(
                    observation.bar for observation in outcome.observations
                )
            else:
                result[outcome.symbol] = self._failure(
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=False,
                    symbol=outcome.symbol,
                    message=f"historical data unavailable: {outcome.reason.value}",
                )
        return result

    def fetch_premarket_observations(
        self, symbols: Sequence[str], as_of: datetime
    ) -> dict[str, PriceObservation | ProviderFailure]:
        try:
            requested = _symbol_sequence(symbols)
        except ValueError:
            raw_symbols = tuple(symbols)
            return {
                symbol: self._failure(
                    error_code=ErrorCode.INVALID_REQUEST,
                    retryable=False,
                    symbol=None,
                    message="invalid premarket request",
                )
                for symbol in raw_symbols
                if isinstance(symbol, str)
            }
        if not requested:
            return {}
        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            request_failure = self._failure(
                error_code=ErrorCode.INVALID_REQUEST,
                retryable=False,
                message="premarket as_of must be UTC",
            )
            return {symbol: request_failure for symbol in requested}

        response_failure, payload = self._get_json(
            "/v2/stocks/trades/latest",
            {"symbols": ",".join(requested), "feed": "iex"},
        )
        if response_failure is not None:
            return {symbol: response_failure for symbol in requested}
        assert payload is not None
        try:
            latest = _LatestPayload.model_validate_json(payload)
        except ValidationError:
            schema_failure = self._failure(
                error_code=ErrorCode.PROVIDER_SCHEMA_DRIFT,
                retryable=False,
                message="premarket response schema drift",
            )
            return {symbol: schema_failure for symbol in requested}

        latest_values: dict[str, _TradePayload] | dict[str, _QuotePayload]
        if latest.trades is not None:
            latest_values = latest.trades
        elif latest.quotes is not None:
            latest_values = latest.quotes
        else:
            raise AssertionError("latest response validation lost its required value")
        if set(latest_values) - set(requested):
            schema_failure = self._failure(
                error_code=ErrorCode.PROVIDER_SCHEMA_DRIFT,
                retryable=False,
                message="premarket response contains an unrequested symbol",
            )
            return {symbol: schema_failure for symbol in requested}

        retrieved_at = self._now()
        result: dict[str, PriceObservation | ProviderFailure] = {}
        for symbol in requested:
            item = latest_values.get(symbol)
            if item is None:
                result[symbol] = self._failure(
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=False,
                    symbol=symbol,
                    message="premarket observation is absent from provider response",
                )
                continue
            try:
                if isinstance(item, _TradePayload):
                    value = _positive_decimal(item.price)
                    observed_at = _utc_timestamp(item.timestamp)
                    raw_payload = item.model_dump(mode="json", by_alias=True)
                else:
                    ask = _positive_decimal(item.ask_price)
                    bid = _positive_decimal(item.bid_price)
                    value = ask if ask > 0 else bid
                    observed_at = _utc_timestamp(item.timestamp)
                    raw_payload = item.model_dump(mode="json", by_alias=True)
                if observed_at > as_of or observed_at > retrieved_at:
                    raise ValueError("premarket observation is after the evidence cutoff")
                result[symbol] = PriceObservation(
                    instrument_id=symbol,
                    value=value,
                    currency="USD",
                    session=Session.PRE_MARKET,
                    provider="alpaca",
                    feed="iex",
                    coverage=Coverage.SINGLE_EXCHANGE,
                    observed_at=observed_at,
                    retrieved_at=retrieved_at,
                    evidence_id=_evidence_id(symbol, raw_payload),
                    quality_flags=("FEED_IEX_SINGLE_EXCHANGE",),
                )
            except (TypeError, ValueError, ValidationError, ArithmeticError):
                result[symbol] = self._failure(
                    error_code=ErrorCode.PROVIDER_SCHEMA_DRIFT,
                    retryable=False,
                    symbol=symbol,
                    message="premarket observation is invalid",
                )
        return result

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("provider clock must return UTC")
        return value

    def _failure(
        self,
        *,
        error_code: ErrorCode,
        retryable: bool,
        symbol: str | None = None,
        message: str = "",
    ) -> ProviderFailure:
        return ProviderFailure(
            provider="alpaca",
            symbol=symbol,
            error_code=error_code,
            retryable=retryable,
            message=message,
        )

    def _get_json(
        self,
        path: str,
        query: Mapping[str, str],
    ) -> tuple[ProviderFailure | None, str | None]:
        if self.readiness().configured is False:
            return (
                self._failure(
                    error_code=ErrorCode.CREDENTIALS_MISSING,
                    retryable=False,
                    message="market-data credentials are not configured",
                ),
                None,
            )
        request = AllowedRequest.for_adapter(
            MARKET_DATA_ADAPTER,
            path,
            host=self._host,
            query=query,
            accepted_content_types=("application/json",),
        )
        try:
            key = self._settings.alpaca_api_key
            secret = self._settings.alpaca_api_secret
            if key is None or secret is None:
                raise RequestRejected("market-data credentials are not configured")
            response = self._http_client.request(
                request,
                deadline=self._now() + timedelta(seconds=60),
                provider_credentials=(key.get_secret_value(), secret.get_secret_value()),
            )
        except RequestRejected:
            return (
                self._failure(
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                    message="market-data transport unavailable",
                ),
                None,
            )
        if response.status_code == 401:
            return (
                self._failure(
                    error_code=ErrorCode.CREDENTIALS_MISSING,
                    retryable=False,
                    message="market-data credentials were rejected",
                ),
                None,
            )
        if response.status_code == 403:
            return (
                self._failure(
                    error_code=ErrorCode.PERMISSION_DENIED,
                    retryable=False,
                    message="market-data permission was denied",
                ),
                None,
            )
        if response.status_code == 429 or response.status_code >= 500:
            return (
                self._failure(
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=False,
                    message="market-data provider is unavailable",
                ),
                None,
            )
        if response.status_code != 200:
            return (
                self._failure(
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=False,
                    message="market-data request failed",
                ),
                None,
            )
        try:
            json.loads(response.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return (
                self._failure(
                    error_code=ErrorCode.PROVIDER_SCHEMA_DRIFT,
                    retryable=False,
                    message="market-data response was not valid JSON",
                ),
                None,
            )
        return None, response.content.decode("utf-8")
