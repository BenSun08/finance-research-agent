"""Strict serialized results for deterministic indicator calculations."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, ConfigDict, PlainSerializer
from pydantic.dataclasses import dataclass

from finance_research_agent.domain.types import decimal_json


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


MetricDecimal = Annotated[
    Decimal,
    PlainSerializer(decimal_json, return_type=str, when_used="json"),
]


def _immutable_tuple(value: Any) -> Any:
    if not isinstance(value, tuple):
        raise ValueError("metric result collections must be immutable tuples")
    return value


MetricParameters = Annotated[
    tuple[tuple[str, str], ...],
    BeforeValidator(_immutable_tuple),
]
MetricIds = Annotated[tuple[str, ...], BeforeValidator(_immutable_tuple)]


@dataclass(
    frozen=True,
    slots=True,
    config=ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=True,
        validate_default=True,
        revalidate_instances="always",
    ),
)
class MetricResult:
    """One versioned deterministic calculation or structured unavailability."""

    metric_id: str
    name: MetricName
    status: MetricStatus
    value: MetricDecimal | None
    unit: MetricUnit
    direction: MetricDirection
    parameters: MetricParameters
    period_start: date | None
    period_end: date | None
    formula_version: str
    input_snapshot_ids: MetricIds
    calculated_at: datetime
    unavailable_reason: MetricUnavailableReason | None
    quality_flags: MetricIds
    input_evidence_ids: MetricIds
    schema_version: Literal["0.1"] = "0.1"

    def __post_init__(self) -> None:
        if not self.metric_id:
            raise ValueError("metric_id must not be empty")
        if (
            not isinstance(self.parameters, tuple)
            or any(not isinstance(parameter, tuple) for parameter in self.parameters)
            or not isinstance(self.input_snapshot_ids, tuple)
            or not isinstance(self.input_evidence_ids, tuple)
            or not isinstance(self.quality_flags, tuple)
        ):
            raise ValueError("metric result collections must be immutable tuples")
        if self.status is MetricStatus.AVAILABLE:
            if self.value is None or self.unavailable_reason is not None:
                raise ValueError("available metrics require a value and no unavailable reason")
            if not isinstance(self.value, Decimal) or not self.value.is_finite():
                raise ValueError("available metric value must be a finite Decimal")
        elif self.value is not None or self.unavailable_reason is None:
            raise ValueError("unavailable metrics require no value and a typed reason")
        if self.parameters != tuple(sorted(self.parameters)):
            raise ValueError("metric parameters must be sorted")
        if self.input_snapshot_ids != tuple(sorted(self.input_snapshot_ids)):
            raise ValueError("metric input snapshot IDs must be sorted")
        if len(set(self.input_evidence_ids)) != len(self.input_evidence_ids):
            raise ValueError("metric input evidence IDs must be unique")
        if self.calculated_at.tzinfo is None or self.calculated_at.utcoffset() != timedelta(0):
            raise ValueError("calculated_at must be timezone-aware UTC")
        if self.period_start is not None and self.period_end is not None:
            if self.period_start > self.period_end:
                raise ValueError("metric period_start cannot be after period_end")
