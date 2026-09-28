from datetime import UTC, datetime

import pytest

from finance_research_agent.application.market_collection import (
    MarketDataCollection,
    SymbolMarketCollection,
)
from finance_research_agent.domain.enums import Capability, DataQualityStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    InstrumentIdentity,
    ProviderFailure,
    SourceHealth,
)

_AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _collection(
    *,
    instrument: InstrumentIdentity | ProviderFailure,
    daily_bars: tuple | ProviderFailure = (),
    premarket_observation: ProviderFailure | None = None,
) -> MarketDataCollection:
    if premarket_observation is None:
        premarket_observation = ProviderFailure(
            provider="alpaca",
            symbol="AAPL",
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            retryable=True,
        )
    return MarketDataCollection(
        symbols=(
            SymbolMarketCollection(
                symbol="AAPL",
                instrument=instrument,
                daily_bars=daily_bars,
                premarket_observation=premarket_observation,
            ),
        ),
        completed_at=_AT,
    )


def _identity() -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id="NASDAQ:AAPL",
        symbol="AAPL",
        name="Apple Inc.",
        instrument_type="COMMON_STOCK",
        primary_exchange="NASDAQ",
        listing_country="US",
        currency="USD",
        is_active=True,
        is_leveraged=False,
        is_inverse=False,
        is_otc=False,
    )


def _source_health() -> tuple[SourceHealth, ...]:
    return (
        SourceHealth(provider="alpaca", available=True, required=True),
        SourceHealth(provider="market-calendar", available=True, required=True),
        SourceHealth(provider="macro", available=True, required=True),
        SourceHealth(provider="sec_edgar", available=True, required=True),
    )


def test_collection_history_failure_is_symbol_scoped_quality() -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    collection = _collection(
        instrument=_identity(),
        daily_bars=ProviderFailure(
            provider="alpaca",
            symbol="AAPL",
            error_code=ErrorCode.PROVIDER_MISSING_SESSION,
            retryable=False,
        ),
    )

    quality = evaluate_collected_market_data_quality(
        collection,
        source_health=_source_health(),
        risk_policy=None,
    )

    assert quality.status is DataQualityStatus.DEGRADED
    assert quality.symbol_capabilities["AAPL"]
    assert (
        quality.symbol_capability("AAPL", Capability.PLAN_DRAFT_AVAILABLE).available is False
    )
    assert quality.symbol_plan_status("AAPL").value == "BLOCKED"


def test_collection_premarket_failure_only_restricts_symbol_sizing() -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    collection = _collection(instrument=_identity(), daily_bars=())

    quality = evaluate_collected_market_data_quality(
        collection,
        source_health=_source_health(),
        risk_policy=None,
    )

    assert quality.status is DataQualityStatus.DEGRADED
    assert quality.symbol_capability("AAPL", Capability.PLAN_DRAFT_AVAILABLE).available is True
    assert (
        quality.symbol_capability("AAPL", Capability.POSITION_SIZING_AVAILABLE).available
        is False
    )
    assert quality.symbol_plan_status("AAPL").value == "REVIEW_REQUIRED"


def test_collection_global_provider_failure_is_global_quality_failure() -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    collection = _collection(
        instrument=ProviderFailure(
            provider="alpaca",
            symbol=None,
            error_code=ErrorCode.CREDENTIALS_MISSING,
            retryable=False,
        ),
        premarket_observation=ProviderFailure(
            provider="alpaca",
            symbol="AAPL",
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            retryable=True,
        ),
    )

    quality = evaluate_collected_market_data_quality(
        collection,
        source_health=_source_health(),
        risk_policy=None,
    )

    assert quality.status is DataQualityStatus.FAIL
    assert quality.global_reason_codes == (ErrorCode.CREDENTIALS_MISSING,)
    assert quality.capability(Capability.PLAN_DRAFT_AVAILABLE).available is False


def test_global_premarket_failure_is_evaluated_as_global_provider_failure() -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    collection = _collection(
        instrument=_identity(),
        daily_bars=(),
        premarket_observation=ProviderFailure(
            provider="alpaca",
            symbol=None,
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            retryable=True,
        ),
    )

    quality = evaluate_collected_market_data_quality(
        collection,
        source_health=_source_health(),
        risk_policy=None,
    )

    assert quality.status is DataQualityStatus.FAIL
    assert quality.global_reason_codes == (ErrorCode.PROVIDER_UNAVAILABLE,)
    assert quality.capability(Capability.PLAN_DRAFT_AVAILABLE).available is False


def test_duplicate_collection_symbols_are_rejected() -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    symbol_result = SymbolMarketCollection(
        symbol="AAPL",
        instrument=_identity(),
        daily_bars=(),
        premarket_observation=ProviderFailure(
            provider="alpaca",
            symbol="AAPL",
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            retryable=True,
        ),
    )
    collection = MarketDataCollection(
        symbols=(symbol_result, symbol_result),
        completed_at=_AT,
    )

    with pytest.raises(ValueError, match="symbols must be unique"):
        evaluate_collected_market_data_quality(
            collection,
            source_health=_source_health(),
            risk_policy=None,
        )


def test_failure_symbol_must_match_collection_symbol() -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    collection = _collection(
        instrument=ProviderFailure(
            provider="alpaca",
            symbol="MSFT",
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            retryable=True,
        ),
        premarket_observation=_identity(),
    )

    with pytest.raises(ValueError, match="differs from collection symbol"):
        evaluate_collected_market_data_quality(
            collection,
            source_health=_source_health(),
            risk_policy=None,
        )


def test_premarket_failure_symbol_must_match_collection_symbol() -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    collection = _collection(
        instrument=_identity(),
        daily_bars=(),
        premarket_observation=ProviderFailure(
            provider="alpaca",
            symbol="MSFT",
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            retryable=True,
        ),
    )

    with pytest.raises(ValueError, match="differs from collection symbol"):
        evaluate_collected_market_data_quality(
            collection,
            source_health=_source_health(),
            risk_policy=None,
        )


def test_collection_argument_must_have_market_collection_type() -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    with pytest.raises(TypeError, match="collection must be MarketDataCollection"):
        evaluate_collected_market_data_quality(
            None,  # type: ignore[arg-type]
            source_health=_source_health(),
            risk_policy=None,
        )
