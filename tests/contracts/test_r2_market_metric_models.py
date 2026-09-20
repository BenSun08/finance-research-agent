from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from finance_research_agent.domain.enums import Coverage, Session
from finance_research_agent.domain.metrics import (
    MetricDirection,
    MetricName,
    MetricResult,
    MetricStatus,
    MetricUnit,
)
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    InstrumentIdentity,
    MarketSnapshot,
    SourceObservation,
)

NOW = datetime(2026, 8, 26, 12, 45, tzinfo=UTC)


def _instrument() -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id="SPY",
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        instrument_type="ETF",
        primary_exchange="NYSE_ARCA",
        listing_country="US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )


def _source() -> SourceObservation:
    return SourceObservation(
        observation_id="history-0123456789abcdef01234567",
        provider="fixture",
        source_url=None,
        source_hash_sha256="a" * 64,
        observed_at=NOW,
        retrieved_at=NOW,
        content_type="application/vnd.finance-research-agent.historical-bars+json",
        excerpt="",
        persistence_allowed=True,
        quality_flags=(),
    )


def _bar() -> CompletedDailyBar:
    return CompletedDailyBar(
        instrument_id="SPY",
        session_date=date(2026, 8, 25),
        source_timestamp=NOW,
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("104"),
        volume=1_000_000,
        session=Session.COMPLETED_SESSION,
        provider="fixture",
        feed="iex",
        coverage=Coverage.SINGLE_EXCHANGE,
        adjustment="split",
        retrieved_at=NOW,
        evidence_cutoff_at=NOW,
        evidence_id="ev-history-0123456789abcdef01234567",
        quality_flags=(),
    )


def test_canonical_market_snapshot_is_strict_frozen_and_schema_versioned() -> None:
    snapshot = MarketSnapshot(
        instrument=_instrument(),
        latest_price=None,
        completed_daily_bars=(_bar(),),
        current_session_bars=(),
        source_observations=(_source(),),
        quality_flags=(),
    )

    assert snapshot.schema_version == "0.1"
    assert snapshot.completed_daily_bars[0].evidence_id.startswith("ev-history-")
    with pytest.raises(ValidationError):
        MarketSnapshot.model_validate(
            {**snapshot.model_dump(), "unknown": True}, strict=True
        )


def test_metric_result_is_strict_serializable_and_preserves_dataclass_replace() -> None:
    metric = MetricResult(
        metric_id="metric-0123456789abcdef01234567",
        name=MetricName.SMA,
        status=MetricStatus.AVAILABLE,
        value=Decimal("103.00"),
        unit=MetricUnit.PRICE,
        direction=MetricDirection.NOT_APPLICABLE,
        parameters=(("window", "2"),),
        period_start=date(2026, 8, 24),
        period_end=date(2026, 8, 25),
        formula_version="indicator-formula-v1",
        input_snapshot_ids=("normalized-0123456789abcdef01234567",),
        calculated_at=NOW,
        unavailable_reason=None,
        quality_flags=(),
        input_evidence_ids=("ev-history-0123456789abcdef01234567",),
    )

    payload = TypeAdapter(MetricResult).dump_python(metric, mode="json")

    assert payload["schema_version"] == "0.1"
    assert payload["value"] == "103.00"
    assert payload["input_evidence_ids"] == [
        "ev-history-0123456789abcdef01234567"
    ]
    assert replace(metric, value=Decimal("104")).value == Decimal("104")


def test_metric_result_rejects_duplicate_evidence_references_without_reordering() -> None:
    with pytest.raises(ValueError, match="evidence IDs must be unique"):
        MetricResult(
            metric_id="metric-0123456789abcdef01234567",
            name=MetricName.SMA,
            status=MetricStatus.AVAILABLE,
            value=Decimal("103"),
            unit=MetricUnit.PRICE,
            direction=MetricDirection.NOT_APPLICABLE,
            parameters=(("window", "2"),),
            period_start=date(2026, 8, 24),
            period_end=date(2026, 8, 25),
            formula_version="indicator-formula-v1",
            input_snapshot_ids=("normalized-0123456789abcdef01234567",),
            calculated_at=NOW,
            unavailable_reason=None,
            quality_flags=(),
            input_evidence_ids=("ev-b", "ev-a", "ev-b"),
        )


def test_market_snapshot_rejects_bar_for_another_instrument() -> None:
    with pytest.raises(ValidationError, match="completed bars must match instrument"):
        MarketSnapshot(
            instrument=_instrument(),
            latest_price=None,
            completed_daily_bars=(
                _bar().model_copy(update={"instrument_id": "QQQ"}),
            ),
            current_session_bars=(),
            source_observations=(_source(),),
            quality_flags=(),
        )
