"""Structured results for deterministic indicator calculations."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class MetricName(StrEnum):
    """Indicator names produced by the bounded deterministic core."""

    SMA = "sma"
    SMA_SLOPE = "sma_slope"
    RELATIVE_RETURN = "relative_return"
    ATR_PERCENT = "atr_percent"
    ATR_PERCENTILE = "atr_percentile"
    REALIZED_VOLATILITY = "realized_volatility"
    REALIZED_VOLATILITY_PERCENTILE = "realized_volatility_percentile"
    PERCENTILE_RANK = "percentile_rank"
    EQUAL_WEIGHT_RELATIVE_RETURN = "equal_weight_relative_return"


class MetricStatus(StrEnum):
    """Availability of a deterministic metric."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class MetricUnit(StrEnum):
    """Units used by deterministic metrics."""

    PRICE = "price"
    DECIMAL = "decimal"
    PERCENTILE_0_TO_100 = "percentile_0_to_100"


class MetricDirection(StrEnum):
    """Direction implied by a metric value when applicable."""

    UP = "up"
    FLAT = "flat"
    DOWN = "down"
    NOT_APPLICABLE = "not_applicable"


class MetricUnavailableReason(StrEnum):
    """Typed reasons why an indicator could not be calculated."""

    INSUFFICIENT_HISTORY = "insufficient_history"
    MISSING_INPUT = "missing_input"
    MISALIGNED_DATES = "misaligned_dates"


@dataclass(frozen=True, slots=True)
class MetricResult:
    """One versioned deterministic calculation or structured unavailability."""

    metric_id: str
    name: MetricName
    status: MetricStatus
    value: Decimal | None
    unit: MetricUnit
    direction: MetricDirection
    parameters: tuple[tuple[str, str], ...]
    period_start: date | None
    period_end: date | None
    formula_version: str
    input_snapshot_ids: tuple[str, ...]
    calculated_at: datetime
    unavailable_reason: MetricUnavailableReason | None
    quality_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.metric_id:
            raise ValueError("metric_id must not be empty")
        if self.status is MetricStatus.AVAILABLE:
            if self.value is None or self.unavailable_reason is not None:
                raise ValueError("available metrics require a value and no unavailable reason")
        elif self.value is not None or self.unavailable_reason is None:
            raise ValueError("unavailable metrics require no value and a typed reason")
        if self.parameters != tuple(sorted(self.parameters)):
            raise ValueError("metric parameters must be sorted")
        if self.input_snapshot_ids != tuple(sorted(self.input_snapshot_ids)):
            raise ValueError("metric input snapshot IDs must be sorted")
        if self.calculated_at.tzinfo is None or self.calculated_at.utcoffset() != timedelta(0):
            raise ValueError("calculated_at must be timezone-aware UTC")
        if self.period_start is not None and self.period_end is not None:
            if self.period_start > self.period_end:
                raise ValueError("metric period_start cannot be after period_end")
