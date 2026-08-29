"""Immutable completed-bar market data contracts."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from re import fullmatch


class InvalidMarketDataError(ValueError):
    """Raised when market data violates a structural input invariant."""


class MarketDataSource(StrEnum):
    """Market-data sources permitted by the current bounded release."""

    SYNTHETIC = "synthetic"
    NORMALIZED_PROVIDER = "normalized_provider"


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One completed daily OHLCV observation."""

    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None

    def __post_init__(self) -> None:
        prices = (self.open, self.high, self.low, self.close)
        if any(not price.is_finite() or price <= 0 for price in prices):
            raise InvalidMarketDataError("daily-bar prices must be positive and finite")
        if not self.low <= self.open <= self.high:
            raise InvalidMarketDataError("daily-bar open must be within low and high")
        if not self.low <= self.close <= self.high:
            raise InvalidMarketDataError("daily-bar close must be within low and high")
        if self.volume is not None and self.volume < 0:
            raise InvalidMarketDataError("daily-bar volume must be non-negative when present")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Validated immutable completed history for one symbol."""

    schema_version: str
    snapshot_id: str
    symbol: str
    as_of: datetime
    currency: str
    source: MarketDataSource
    completed_daily_bars: tuple[DailyBar, ...]
    quality_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.completed_daily_bars, tuple) or not isinstance(
            self.quality_flags, tuple
        ):
            raise InvalidMarketDataError("snapshot bars and quality flags must be immutable tuples")
        if self.schema_version != "market-snapshot-v1":
            raise InvalidMarketDataError("unsupported market snapshot schema version")
        if (
            fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", self.snapshot_id) is None
            or not self.snapshot_id.isascii()
        ):
            raise InvalidMarketDataError("snapshot_id must use safe normalized ASCII characters")
        if fullmatch(r"[A-Z][A-Z0-9.-]*", self.symbol) is None or not self.symbol.isascii():
            raise InvalidMarketDataError("symbol must be normalized uppercase ASCII")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() != timedelta(0):
            raise InvalidMarketDataError("as_of must be timezone-aware UTC")
        if self.currency != "USD":
            raise InvalidMarketDataError("currency must be USD in market-snapshot-v1")
        if not isinstance(self.source, MarketDataSource) or self.source not in (
            MarketDataSource.SYNTHETIC,
            MarketDataSource.NORMALIZED_PROVIDER,
        ):
            raise InvalidMarketDataError("unsupported market data source")

        dates = tuple(bar.session_date for bar in self.completed_daily_bars)
        if any(current >= following for current, following in zip(dates, dates[1:])):
            raise InvalidMarketDataError("daily bars must have unique, increasing session dates")
        if dates and dates[-1] > self.as_of.date():
            raise InvalidMarketDataError("completed daily bars cannot be later than as_of")
