from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from finance_research_agent.domain.enums import Capability, DataQualityStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import CapabilityState, ProviderFailure, SourceHealth
from finance_research_agent.domain.policies import RiskPolicy
from finance_research_agent.domain.quality import DataQualityResult, evaluate_data_quality
from finance_research_agent.domain.regime import Regime
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.market_data.historical import (
    FAILURE_SCHEMA_VERSION,
    BarAdjustment,
    HistoricalBarsFailure,
    HistoricalBarsProvenance,
    HistoricalBarsRequestFailure,
    HistoricalBarsRequestFailureReason,
    HistoricalBarsUnavailableReason,
    MarketDataCoverage,
    MarketDataFeed,
)


def _complete_risk_policy(*, sizing_enabled: bool = True) -> RiskPolicy:
    return RiskPolicy(
        version="1",
        sizing_enabled=sizing_enabled,
        planning_capital_usd="10000" if sizing_enabled else None,
        max_risk_per_trade_pct="0.01" if sizing_enabled else None,
        max_position_pct="0.10" if sizing_enabled else None,
        minimum_reward_risk_ratio="2" if sizing_enabled else None,
        max_total_portfolio_heat_pct="0.05" if sizing_enabled else None,
        existing_portfolio_heat_pct="0.01" if sizing_enabled else None,
        quantity_increment="1",
        regime_risk_multipliers={regime.name: Decimal("1") for regime in Regime},
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


def test_optional_discovery_global_failure_degrades_without_disabling_technical_research() -> None:
    result = evaluate_data_quality(
        source_health=(
            SourceHealth(
                provider="alpaca_news",
                available=False,
                required=False,
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="discovery unavailable",
            ),
        ),
        provider_failures=(
            ProviderFailure(
                provider="alpaca_news",
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                retryable=False,
            ),
        ),
        risk_policy=_complete_risk_policy(),
    )

    assert result.status is DataQualityStatus.DEGRADED
    assert result.capability(Capability.WATCHLIST_METRICS_AVAILABLE).available is True
    assert result.capability(Capability.SETUP_DETECTION_AVAILABLE).available is True


@pytest.mark.parametrize("error_code", (ErrorCode.PROVIDER_NO_DATA, ErrorCode.STALE_DATA))
def test_symbol_current_price_failure_preserves_history_and_caps_plan_review(
    error_code: ErrorCode,
) -> None:
    result = evaluate_data_quality(
        source_health=(SourceHealth(provider="alpaca", available=True, required=True),),
        current_price_failures=(
            ProviderFailure(
                provider="alpaca",
                symbol="MSFT",
                error_code=error_code,
                retryable=False,
            ),
        ),
        risk_policy=_complete_risk_policy(),
    )

    assert result.capability(Capability.WATCHLIST_METRICS_AVAILABLE).available is True
    assert result.symbol_capability("MSFT", Capability.PLAN_DRAFT_AVAILABLE).available is True
    assert result.symbol_capability("MSFT", Capability.POSITION_SIZING_AVAILABLE).available is False
    assert result.symbol_plan_status("MSFT").value == "REVIEW_REQUIRED"


def test_disabled_risk_policy_disables_only_sizing_and_portfolio_heat() -> None:
    result = evaluate_data_quality(
        source_health=(),
        risk_policy=_complete_risk_policy(sizing_enabled=False),
    )

    assert result.status is DataQualityStatus.DEGRADED
    assert result.capability(Capability.PLAN_DRAFT_AVAILABLE).available is True
    assert result.capability(Capability.POSITION_SIZING_AVAILABLE).available is False
    assert result.capability(Capability.PORTFOLIO_HEAT_CHECK_AVAILABLE).available is False


def test_complete_risk_policy_allows_sizing_and_portfolio_heat() -> None:
    result = evaluate_data_quality(source_health=(), risk_policy=_complete_risk_policy())

    assert result.status is DataQualityStatus.PASS
    assert all(capability.available for capability in result.capabilities)


def test_global_diagnostics_retain_multiple_provider_reasons() -> None:
    result = evaluate_data_quality(
        source_health=(),
        provider_failures=(
            ProviderFailure(
                provider="alpaca",
                error_code=ErrorCode.CREDENTIALS_MISSING,
                retryable=False,
            ),
            ProviderFailure(
                provider="alpaca",
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                retryable=False,
            ),
        ),
        risk_policy=_complete_risk_policy(),
    )

    assert result.status is DataQualityStatus.FAIL
    assert result.global_reason_codes == (
        ErrorCode.CREDENTIALS_MISSING,
        ErrorCode.PROVIDER_UNAVAILABLE,
    )


def test_quality_result_rejects_duplicate_or_missing_capability_states() -> None:
    state = CapabilityState(
        capability=Capability.MARKET_SUMMARY_AVAILABLE,
        available=True,
        reason_codes=(),
        evidence_ids=(),
    )

    with pytest.raises(ValueError, match="exactly one state"):
        DataQualityResult(
            status=DataQualityStatus.PASS,
            capabilities=(state,) * len(Capability),
            symbol_capabilities=FrozenMap({}),
        )


def test_sec_edgar_official_verification_failure_keeps_technical_research() -> None:
    result = evaluate_data_quality(
        source_health=(
            SourceHealth(
                provider="sec_edgar",
                available=False,
                required=True,
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="official filings unavailable",
            ),
        ),
        risk_policy=_complete_risk_policy(),
    )

    assert result.status is DataQualityStatus.DEGRADED
    assert result.capability(Capability.SETUP_DETECTION_AVAILABLE).available is True
    assert result.capability(Capability.PLAN_DRAFT_AVAILABLE).available is False


def _historical_failure(reason: HistoricalBarsUnavailableReason) -> HistoricalBarsFailure:
    return HistoricalBarsFailure(
        schema_version=FAILURE_SCHEMA_VERSION,
        symbol="MSFT",
        reason=reason,
        provenance=HistoricalBarsProvenance(
            provider="fixture",
            feed=MarketDataFeed.IEX,
            coverage=MarketDataCoverage.SINGLE_EXCHANGE,
            adjustment=BarAdjustment.SPLIT,
            requested_start_at=datetime(2026, 8, 31, tzinfo=UTC),
            requested_end_at=datetime(2026, 9, 1, tzinfo=UTC),
            retrieved_at=datetime(2026, 9, 2, tzinfo=UTC),
            evidence_cutoff_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
            completed_through_session=date(2026, 9, 1),
            adapter_version="fixture-v1",
        ),
        missing_sessions=(),
        quality_flags=(),
    )


@pytest.mark.parametrize(
    ("reason", "error_code"),
    (
        (HistoricalBarsUnavailableReason.NO_DATA, ErrorCode.PROVIDER_NO_DATA),
        (
            HistoricalBarsUnavailableReason.MISSING_EXPECTED_SESSION,
            ErrorCode.PROVIDER_MISSING_SESSION,
        ),
        (
            HistoricalBarsUnavailableReason.DUPLICATE_CONFLICT,
            ErrorCode.PROVIDER_DUPLICATE_CONFLICT,
        ),
        (HistoricalBarsUnavailableReason.MALFORMED_BAR, ErrorCode.PROVIDER_MALFORMED_BAR),
        (HistoricalBarsUnavailableReason.STALE, ErrorCode.PROVIDER_STALE),
        (
            HistoricalBarsUnavailableReason.FUTURE_OR_INCOMPLETE_BAR,
            ErrorCode.PROVIDER_FUTURE_OR_INCOMPLETE_BAR,
        ),
    ),
)
def test_each_historical_failure_reason_blocks_only_its_symbol(
    reason: HistoricalBarsUnavailableReason, error_code: ErrorCode
) -> None:
    result = evaluate_data_quality(
        source_health=(),
        historical_failures=(_historical_failure(reason),),
        risk_policy=_complete_risk_policy(),
    )

    state = result.symbol_capability("MSFT", Capability.PLAN_DRAFT_AVAILABLE)
    assert state.available is False
    assert state.reason_codes == (error_code,)
