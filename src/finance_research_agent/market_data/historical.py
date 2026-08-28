"""Provider-independent completed historical market-data contracts."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from json import dumps
from re import fullmatch
from zoneinfo import ZoneInfo

from finance_research_agent.domain.market import (
    DailyBar,
    InvalidMarketDataError,
    MarketDataSource,
    MarketSnapshot,
)

__all__ = [
    "FAILURE_SCHEMA_VERSION",
    "HISTORY_SCHEMA_VERSION",
    "NORMALIZER_VERSION",
    "BarAdjustment",
    "DailyBarObservation",
    "HistoricalBarsFailure",
    "HistoricalBarsOutcome",
    "HistoricalBarsProvenance",
    "HistoricalBarsUnavailableReason",
    "HistoricalDailyBars",
    "HistoricalDailyBarsRequest",
    "InvalidMarketDataError",
    "MarketDataCoverage",
    "MarketDataFeed",
    "coverage_for_feed",
    "create_daily_bar_observation",
    "to_market_snapshot",
]

HISTORY_SCHEMA_VERSION = "historical-daily-bars-v1"
FAILURE_SCHEMA_VERSION = "historical-bars-failure-v1"
NORMALIZER_VERSION = "historical-normalizer-v1"
NEW_YORK = ZoneInfo("America/New_York")


class MarketDataFeed(StrEnum):
    """Permitted U.S. equity feeds for completed historical bars."""

    IEX = "iex"
    SIP = "sip"


class MarketDataCoverage(StrEnum):
    """Coverage represented by a normalized historical feed."""

    SINGLE_EXCHANGE = "single_exchange"
    CONSOLIDATED_US = "consolidated_us"


class BarAdjustment(StrEnum):
    """Explicit provider-applied corporate-action adjustment basis."""

    RAW = "raw"
    SPLIT = "split"
    DIVIDEND = "dividend"
    ALL = "all"


class HistoricalBarsUnavailableReason(StrEnum):
    """Typed reason a requested symbol cannot produce a current snapshot."""

    NO_DATA = "no_data"
    MISSING_EXPECTED_SESSION = "missing_expected_session"
    DUPLICATE_CONFLICT = "duplicate_conflict"
    MALFORMED_BAR = "malformed_bar"
    STALE = "stale"
    FUTURE_OR_INCOMPLETE_BAR = "future_or_incomplete_bar"


def _require_utc(value: datetime, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise InvalidMarketDataError(f"{field_name} must be timezone-aware UTC")


def _require_symbol(symbol: str) -> None:
    if (
        not isinstance(symbol, str)
        or fullmatch(r"[A-Z][A-Z0-9.-]*", symbol) is None
        or not symbol.isascii()
    ):
        raise InvalidMarketDataError("symbols must be normalized uppercase ASCII")


def _require_quality_flags(flags: tuple[str, ...]) -> None:
    if not isinstance(flags, tuple):
        raise InvalidMarketDataError("historical-data collections must be immutable tuples")
    if flags != tuple(sorted(set(flags))):
        raise InvalidMarketDataError("quality flags must be sorted and unique")
    if any(
        not isinstance(flag, str)
        or fullmatch(r"[A-Z][A-Z0-9_]*", flag) is None
        or not flag.isascii()
        for flag in flags
    ):
        raise InvalidMarketDataError("quality flags must use normalized uppercase ASCII")


def coverage_for_feed(feed: MarketDataFeed) -> MarketDataCoverage:
    """Return the only valid coverage classification for a supported feed."""

    if feed is MarketDataFeed.IEX:
        return MarketDataCoverage.SINGLE_EXCHANGE
    if feed is MarketDataFeed.SIP:
        return MarketDataCoverage.CONSOLIDATED_US
    raise InvalidMarketDataError("unsupported historical market-data feed")


@dataclass(frozen=True, slots=True)
class HistoricalDailyBarsRequest:
    """Provider-neutral request and point-in-time completion context."""

    symbols: tuple[str, ...]
    start_at: datetime
    end_at: datetime
    expected_sessions: tuple[date, ...]
    completed_through_session: date
    feed: MarketDataFeed
    adjustment: BarAdjustment
    evidence_cutoff_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.symbols, tuple) or not isinstance(
            self.expected_sessions, tuple
        ):
            raise InvalidMarketDataError(
                "historical-data request collections must be immutable tuples"
            )
        if not self.symbols or self.symbols != tuple(sorted(set(self.symbols))):
            raise InvalidMarketDataError("request symbols must be unique and canonical")
        for symbol in self.symbols:
            _require_symbol(symbol)
        if not self.expected_sessions:
            raise InvalidMarketDataError("expected sessions must not be empty")
        if any(
            not isinstance(session, date) or isinstance(session, datetime)
            for session in self.expected_sessions
        ):
            raise InvalidMarketDataError("expected sessions must contain dates")
        if self.expected_sessions != tuple(sorted(set(self.expected_sessions))):
            raise InvalidMarketDataError("expected sessions must be unique and increasing")
        if not isinstance(self.completed_through_session, date) or isinstance(
            self.completed_through_session, datetime
        ):
            raise InvalidMarketDataError("completed-through session must be a date")
        if self.completed_through_session != self.expected_sessions[-1]:
            raise InvalidMarketDataError(
                "completed-through session must equal the final expected session"
            )
        if not isinstance(self.feed, MarketDataFeed):
            raise InvalidMarketDataError("unsupported historical market-data feed")
        if not isinstance(self.adjustment, BarAdjustment):
            raise InvalidMarketDataError("bar adjustment must be explicit and supported")
        _require_utc(self.start_at, "start_at")
        _require_utc(self.end_at, "end_at")
        _require_utc(self.evidence_cutoff_at, "evidence_cutoff_at")
        if self.start_at > self.end_at:
            raise InvalidMarketDataError("request start_at cannot be after end_at")
        if self.end_at > self.evidence_cutoff_at:
            raise InvalidMarketDataError("request end_at cannot be after evidence cutoff")
        if self.completed_through_session >= self.evidence_cutoff_at.astimezone(NEW_YORK).date():
            raise InvalidMarketDataError(
                "completed-through session must be earlier than the cutoff market date "
                "for the premarket slice"
            )


@dataclass(frozen=True, slots=True)
class DailyBarObservation:
    """One normalized completed bar plus its provider timestamp."""

    source_timestamp: datetime
    bar: DailyBar

    def __post_init__(self) -> None:
        _require_utc(self.source_timestamp, "source_timestamp")
        if not isinstance(self.bar, DailyBar):
            raise InvalidMarketDataError("bar observation must contain a DailyBar")
        if self.source_timestamp.astimezone(NEW_YORK).date() != self.bar.session_date:
            raise InvalidMarketDataError(
                "source timestamp and New York session date must agree"
            )


@dataclass(frozen=True, slots=True)
class HistoricalBarsProvenance:
    """Provider-neutral provenance for one normalized historical response."""

    provider: str
    feed: MarketDataFeed
    coverage: MarketDataCoverage
    adjustment: BarAdjustment
    requested_start_at: datetime
    requested_end_at: datetime
    retrieved_at: datetime
    evidence_cutoff_at: datetime
    completed_through_session: date
    adapter_version: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider, str)
            or fullmatch(r"[a-z][a-z0-9_-]*", self.provider) is None
            or not self.provider.isascii()
        ):
            raise InvalidMarketDataError("provider must use normalized lowercase ASCII")
        if not isinstance(self.feed, MarketDataFeed):
            raise InvalidMarketDataError("unsupported historical market-data feed")
        if not isinstance(self.coverage, MarketDataCoverage):
            raise InvalidMarketDataError("unsupported market-data coverage")
        if self.coverage is not coverage_for_feed(self.feed):
            raise InvalidMarketDataError("feed and coverage must agree")
        if not isinstance(self.adjustment, BarAdjustment):
            raise InvalidMarketDataError("bar adjustment must be explicit and supported")
        for value, field_name in (
            (self.requested_start_at, "requested_start_at"),
            (self.requested_end_at, "requested_end_at"),
            (self.retrieved_at, "retrieved_at"),
            (self.evidence_cutoff_at, "evidence_cutoff_at"),
        ):
            _require_utc(value, field_name)
        if self.requested_start_at > self.requested_end_at:
            raise InvalidMarketDataError(
                "requested_start_at cannot be after requested_end_at"
            )
        if self.requested_end_at > self.evidence_cutoff_at:
            raise InvalidMarketDataError(
                "requested_end_at cannot be after evidence cutoff"
            )
        if self.requested_end_at > self.retrieved_at:
            raise InvalidMarketDataError(
                "requested_end_at cannot be after retrieved_at"
            )
        if self.retrieved_at > self.evidence_cutoff_at:
            raise InvalidMarketDataError("retrieved_at cannot be after evidence cutoff")
        if not isinstance(self.completed_through_session, date) or isinstance(
            self.completed_through_session, datetime
        ):
            raise InvalidMarketDataError("completed-through session must be a date")
        if self.completed_through_session >= self.evidence_cutoff_at.astimezone(NEW_YORK).date():
            raise InvalidMarketDataError(
                "completed-through session must be earlier than the cutoff market date "
                "for the premarket slice"
            )
        if (
            not isinstance(self.adapter_version, str)
            or fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", self.adapter_version) is None
            or not self.adapter_version.isascii()
        ):
            raise InvalidMarketDataError("adapter_version must use safe normalized ASCII")


def create_daily_bar_observation(
    *,
    source_timestamp: datetime,
    open_price: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: int,
) -> DailyBarObservation:
    """Construct a validated U.S. daily-bar observation from normalized values."""

    _require_utc(source_timestamp, "source_timestamp")
    bar = DailyBar(
        session_date=source_timestamp.astimezone(NEW_YORK).date(),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
    return DailyBarObservation(source_timestamp=source_timestamp, bar=bar)


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _history_id(
    *,
    symbol: str,
    currency: str,
    observations: tuple[DailyBarObservation, ...],
    provenance: HistoricalBarsProvenance,
    quality_flags: tuple[str, ...],
) -> str:
    payload = {
        "adapter_version": provenance.adapter_version,
        "adjustment": provenance.adjustment.value,
        "completed_through_session": provenance.completed_through_session.isoformat(),
        "coverage": provenance.coverage.value,
        "currency": currency,
        "evidence_cutoff_at": provenance.evidence_cutoff_at.isoformat(),
        "feed": provenance.feed.value,
        "normalizer_version": NORMALIZER_VERSION,
        "observations": [
            {
                "close": _decimal_text(observation.bar.close),
                "high": _decimal_text(observation.bar.high),
                "low": _decimal_text(observation.bar.low),
                "open": _decimal_text(observation.bar.open),
                "session_date": observation.bar.session_date.isoformat(),
                "source_timestamp": observation.source_timestamp.isoformat(),
                "volume": observation.bar.volume,
            }
            for observation in observations
        ],
        "provider": provenance.provider,
        "quality_flags": quality_flags,
        "requested_end_at": provenance.requested_end_at.isoformat(),
        "requested_start_at": provenance.requested_start_at.isoformat(),
        "retrieved_at": provenance.retrieved_at.isoformat(),
        "schema_version": HISTORY_SCHEMA_VERSION,
        "symbol": symbol,
    }
    digest = sha256(
        dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"history-{digest[:24]}"


@dataclass(frozen=True, slots=True)
class HistoricalDailyBars:
    """Current, complete, provider-neutral daily history for one symbol."""

    schema_version: str
    history_id: str
    symbol: str
    currency: str
    observations: tuple[DailyBarObservation, ...]
    provenance: HistoricalBarsProvenance
    quality_flags: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        observations: tuple[DailyBarObservation, ...],
        provenance: HistoricalBarsProvenance,
        quality_flags: tuple[str, ...],
    ) -> "HistoricalDailyBars":
        """Create a history with a canonical content-and-provenance identity."""

        history_id = _history_id(
            symbol=symbol,
            currency="USD",
            observations=observations,
            provenance=provenance,
            quality_flags=quality_flags,
        )
        return cls(
            schema_version=HISTORY_SCHEMA_VERSION,
            history_id=history_id,
            symbol=symbol,
            currency="USD",
            observations=observations,
            provenance=provenance,
            quality_flags=quality_flags,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.observations, tuple) or not isinstance(
            self.quality_flags, tuple
        ):
            raise InvalidMarketDataError(
                "historical-data collections must be immutable tuples"
            )
        if self.schema_version != HISTORY_SCHEMA_VERSION:
            raise InvalidMarketDataError("unsupported historical daily-bars schema")
        if fullmatch(r"history-[0-9a-f]{24}", self.history_id) is None:
            raise InvalidMarketDataError("history_id must be a canonical SHA-256 identifier")
        _require_symbol(self.symbol)
        if self.currency != "USD":
            raise InvalidMarketDataError("historical daily bars must use USD")
        if not self.observations:
            raise InvalidMarketDataError("available historical daily bars cannot be empty")
        if any(
            not isinstance(observation, DailyBarObservation)
            for observation in self.observations
        ):
            raise InvalidMarketDataError(
                "historical observations must contain DailyBarObservation values"
            )
        dates = tuple(observation.bar.session_date for observation in self.observations)
        if any(current >= following for current, following in zip(dates, dates[1:])):
            raise InvalidMarketDataError(
                "historical observations must have unique increasing session dates"
            )
        if dates[-1] != self.provenance.completed_through_session:
            raise InvalidMarketDataError(
                "available history must end at the completed-through session"
            )
        if any(
            observation.source_timestamp > self.provenance.retrieved_at
            or observation.source_timestamp > self.provenance.evidence_cutoff_at
            for observation in self.observations
        ):
            raise InvalidMarketDataError(
                "source timestamps cannot be after retrieval or evidence cutoff"
            )
        if any(
            observation.source_timestamp < self.provenance.requested_start_at
            or observation.source_timestamp > self.provenance.requested_end_at
            for observation in self.observations
        ):
            raise InvalidMarketDataError(
                "source timestamps must remain inside the request bounds"
            )
        _require_quality_flags(self.quality_flags)
        expected_id = _history_id(
            symbol=self.symbol,
            currency=self.currency,
            observations=self.observations,
            provenance=self.provenance,
            quality_flags=self.quality_flags,
        )
        if self.history_id != expected_id:
            raise InvalidMarketDataError("history_id does not match canonical history content")


@dataclass(frozen=True, slots=True)
class HistoricalBarsFailure:
    """Structured per-symbol failure that cannot enter the numeric core."""

    schema_version: str
    symbol: str
    reason: HistoricalBarsUnavailableReason
    provenance: HistoricalBarsProvenance
    missing_sessions: tuple[date, ...]
    quality_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.missing_sessions, tuple) or not isinstance(
            self.quality_flags, tuple
        ):
            raise InvalidMarketDataError(
                "historical-data collections must be immutable tuples"
            )
        if self.schema_version != FAILURE_SCHEMA_VERSION:
            raise InvalidMarketDataError("unsupported historical-bars failure schema")
        _require_symbol(self.symbol)
        if not isinstance(self.reason, HistoricalBarsUnavailableReason):
            raise InvalidMarketDataError("historical-bars failure requires a typed reason")
        if self.missing_sessions != tuple(sorted(set(self.missing_sessions))):
            raise InvalidMarketDataError("missing sessions must be sorted and unique")
        if any(
            not isinstance(session, date) or isinstance(session, datetime)
            for session in self.missing_sessions
        ):
            raise InvalidMarketDataError("missing sessions must contain dates")
        _require_quality_flags(self.quality_flags)


type HistoricalBarsOutcome = HistoricalDailyBars | HistoricalBarsFailure


def to_market_snapshot(history: HistoricalDailyBars) -> MarketSnapshot:
    """Project complete provider-neutral history into the unchanged numeric core."""

    if not isinstance(history, HistoricalDailyBars):
        raise InvalidMarketDataError("only available historical bars can form a snapshot")
    digest = history.history_id.removeprefix("history-")
    return MarketSnapshot(
        schema_version="market-snapshot-v1",
        snapshot_id=f"normalized-{digest}",
        symbol=history.symbol,
        as_of=history.provenance.evidence_cutoff_at,
        currency=history.currency,
        source=MarketDataSource.NORMALIZED_PROVIDER,
        completed_daily_bars=tuple(
            observation.bar for observation in history.observations
        ),
        quality_flags=history.quality_flags,
    )
