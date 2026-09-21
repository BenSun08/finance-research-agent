from finance_research_agent.domain.enums import Capability, DataQualityStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import ProviderFailure, SourceHealth
from finance_research_agent.domain.quality import evaluate_data_quality
from finance_research_agent.market_data.historical import (
    HistoricalBarsRequestFailure,
    HistoricalBarsRequestFailureReason,
)


def test_missing_required_macro_degrades_report_and_blocks_plans() -> None:
    result = evaluate_data_quality(
        source_health=(
            SourceHealth(
                provider="market-calendar",
                available=True,
                required=True,
            ),
            SourceHealth(
                provider="macro-calendar",
                available=False,
                required=True,
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="calendar unavailable",
            ),
        )
    )

    assert result.status is DataQualityStatus.DEGRADED
    assert result.capability(Capability.MARKET_SUMMARY_AVAILABLE).available is True
    assert result.capability(Capability.EVENT_RISK_CHECK_AVAILABLE).available is False
    assert result.capability(Capability.PLAN_DRAFT_AVAILABLE).available is False


def test_market_calendar_failure_is_global_fail_and_disables_every_capability() -> None:
    result = evaluate_data_quality(
        source_health=(
            SourceHealth(
                provider="market-calendar",
                available=False,
                required=True,
                error_code=ErrorCode.MARKET_CALENDAR_UNAVAILABLE,
                message="calendar unavailable",
            ),
        )
    )

    assert result.status is DataQualityStatus.FAIL
    assert all(not state.available for state in result.capabilities)
    assert all("MARKET_CALENDAR_UNAVAILABLE" in state.reason_codes for state in result.capabilities)


def test_global_historical_request_failure_does_not_become_a_symbol_failure() -> None:
    result = evaluate_data_quality(
        source_health=(),
        historical_request_failure=HistoricalBarsRequestFailure(
            reason=HistoricalBarsRequestFailureReason.TRANSPORT_UNAVAILABLE
        ),
    )

    assert result.status is DataQualityStatus.FAIL
    assert not result.symbol_capabilities
    assert result.capability(Capability.WATCHLIST_METRICS_AVAILABLE).available is False


def test_provider_failure_for_one_symbol_keeps_global_capabilities_and_isolates_symbol() -> None:
    result = evaluate_data_quality(
        source_health=(SourceHealth(provider="alpaca", available=True, required=True),),
        provider_failures=(
            ProviderFailure(
                provider="alpaca",
                symbol="MSFT",
                error_code=ErrorCode.PROVIDER_MISSING_SESSION,
                retryable=False,
            ),
        ),
    )

    assert result.status is DataQualityStatus.DEGRADED
    assert result.capability(Capability.WATCHLIST_METRICS_AVAILABLE).available is True
    assert result.symbol_capability("MSFT", Capability.PLAN_DRAFT_AVAILABLE).available is False
    assert result.symbol_capability("MSFT", Capability.PLAN_DRAFT_AVAILABLE).reason_codes == (
        "PROVIDER_MISSING_SESSION",
    )
