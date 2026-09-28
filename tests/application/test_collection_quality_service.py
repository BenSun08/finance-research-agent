from datetime import UTC, datetime
from pathlib import Path

import pytest

from finance_research_agent.adapters.yaml_config import load_configuration
from finance_research_agent.application.config_service import _tuplify
from finance_research_agent.application.market_collection import (
    MarketDataCollection,
    SymbolMarketCollection,
)
from finance_research_agent.domain.enums import Capability, DataQualityStatus, SourceRole
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    InstrumentIdentity,
    ProviderFailure,
    SourceHealth,
)
from finance_research_agent.domain.policies import SourcePolicy

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
    )



def _source_policy() -> SourcePolicy:
    examples = Path(__file__).parents[2] / "config" / "examples"
    return load_configuration(examples).source

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
        source_policy=_source_policy(),
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
        source_policy=_source_policy(),
        risk_policy=None,
    )

    assert quality.status is DataQualityStatus.DEGRADED
    assert quality.symbol_capability("AAPL", Capability.PLAN_DRAFT_AVAILABLE).reason_codes == (
        ErrorCode("SOURCE_NOT_CONFIGURED"),
    )
    assert (
        quality.symbol_capability("AAPL", Capability.POSITION_SIZING_AVAILABLE).available
        is False
    )
    assert quality.symbol_plan_status("AAPL").value == "BLOCKED"


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
        source_policy=_source_policy(),
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
        source_policy=_source_policy(),
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
            source_policy=_source_policy(),
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
            source_policy=_source_policy(),
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
            source_policy=_source_policy(),
            risk_policy=None,
        )


def test_collection_quality_uses_frozen_source_roles(valid_packet) -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    frozen_policy = _source_policy()
    snapshot = valid_packet.run.configuration_snapshot.model_copy(
        update={
            "policies": {
                "source": frozen_policy.model_dump(mode="json"),
            },
        }
    )
    run = valid_packet.run.model_copy(update={"configuration_snapshot": snapshot})
    frozen_packet = valid_packet.model_copy(update={"run": run})
    frozen_policy = SourcePolicy.model_validate(
        _tuplify(frozen_packet.run.configuration_snapshot.policies["source"])
    )
    current_policy_data = frozen_policy.model_dump()
    current_policy_data["quality_source_roles"] = (
        SourceRole.MARKET_DATA,
        SourceRole.MARKET_CALENDAR,
        SourceRole.MACRO_CALENDAR,
    )
    current_policy = SourcePolicy.model_validate(current_policy_data)
    assert frozen_policy.quality_source_roles == (
        SourceRole.MARKET_DATA,
        SourceRole.MARKET_CALENDAR,
    )
    assert current_policy.quality_source_roles != frozen_policy.quality_source_roles

    quality = evaluate_collected_market_data_quality(
        _collection(instrument=_identity(), daily_bars=()),
        source_health=_source_health(),
        source_policy=frozen_policy,
        risk_policy=None,
    )

    assert quality.capability(Capability.EVENT_RISK_CHECK_AVAILABLE).reason_codes == (
        ErrorCode.SOURCE_NOT_CONFIGURED,
    )
    assert ErrorCode.CONFIGURATION_INVALID not in quality.capability(
        Capability.EVENT_RISK_CHECK_AVAILABLE
    ).reason_codes


def test_collection_argument_must_have_market_collection_type() -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    with pytest.raises(TypeError, match="collection must be MarketDataCollection"):
        evaluate_collected_market_data_quality(
            None,  # type: ignore[arg-type]
            source_health=_source_health(),
            source_policy=_source_policy(),
            risk_policy=None,
        )


def test_collection_quality_policy_argument_must_have_source_policy_type() -> None:
    from finance_research_agent.application.collection_quality import (
        evaluate_collected_market_data_quality,
    )

    with pytest.raises(TypeError, match="source_policy must be SourcePolicy"):
        evaluate_collected_market_data_quality(
            _collection(instrument=_identity(), daily_bars=()),
            source_health=_source_health(),
            source_policy=None,  # type: ignore[arg-type]
            risk_policy=None,
        )
