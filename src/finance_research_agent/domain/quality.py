"""Deterministic Product A data-quality and scoped capability evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self

from pydantic import Field, model_validator

from finance_research_agent.domain.enums import Capability, DataQualityStatus, PlanStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    CapabilityState,
    ProviderFailure,
    ReasonCodes,
    SourceHealth,
    StrictModel,
)
from finance_research_agent.domain.policies import RiskPolicy
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.market_data.historical import (
    HistoricalBarsFailure,
    HistoricalBarsRequestFailure,
)

_CAPABILITIES = tuple(Capability)
_MARKET_PREREQUISITES = {"alpaca", "market-calendar"}
_MACRO_PROVIDERS = {"macro", "macro-calendar", "federal_reserve", "fed", "bls", "bea"}
_OPTIONAL_DISCOVERY_PROVIDERS = {"alpaca_news", "news"}
_OFFICIAL_VERIFICATION_PROVIDERS = {"company_ir", "sec"}
_SIZING_INPUTS = (
    "planning_capital_usd",
    "max_risk_per_trade_pct",
    "max_position_pct",
    "minimum_reward_risk_ratio",
)


class DataQualityResult(StrictModel):
    """Global availability, diagnostics, and explicitly scoped symbol outcomes."""

    status: DataQualityStatus
    capabilities: tuple[CapabilityState, ...] = Field(min_length=8, max_length=8)
    global_reason_codes: ReasonCodes = ()
    symbol_capabilities: FrozenMap[str, tuple[CapabilityState, ...]] = FrozenMap({})
    symbol_plan_statuses: FrozenMap[str, PlanStatus] = FrozenMap({})

    @model_validator(mode="after")
    def _exact_capability_states(self) -> Self:
        expected = set(_CAPABILITIES)
        if len(self.capabilities) != len(expected) or {
            state.capability for state in self.capabilities
        } != expected:
            raise ValueError("capabilities require exactly one state for each exact capability")
        for states in self.symbol_capabilities.values():
            if len(states) != len(expected) or {state.capability for state in states} != expected:
                raise ValueError(
                    "symbol capabilities require exactly one state for each exact capability"
                )
        if set(self.symbol_plan_statuses) - set(self.symbol_capabilities):
            raise ValueError("symbol plan status requires symbol capabilities")
        return self

    def capability(self, capability: Capability) -> CapabilityState:
        return next(state for state in self.capabilities if state.capability is capability)

    def symbol_capability(self, symbol: str, capability: Capability) -> CapabilityState:
        return next(
            state for state in self.symbol_capabilities[symbol] if state.capability is capability
        )

    def symbol_plan_status(self, symbol: str) -> PlanStatus:
        return self.symbol_plan_statuses[symbol]


def _state(capability: Capability, reasons: tuple[ErrorCode, ...] = ()) -> CapabilityState:
    return CapabilityState(
        capability=capability,
        available=not reasons,
        reason_codes=reasons,
        evidence_ids=(),
    )


def _states(disabled: dict[Capability, tuple[ErrorCode, ...]]) -> tuple[CapabilityState, ...]:
    return tuple(_state(capability, disabled.get(capability, ())) for capability in _CAPABILITIES)


def _disable(
    disabled: dict[Capability, tuple[ErrorCode, ...]],
    capabilities: Sequence[Capability],
    reason: ErrorCode,
) -> None:
    for capability in capabilities:
        disabled[capability] = tuple(dict.fromkeys((*disabled.get(capability, ()), reason)))


def _dependencies(provider: str, *, required: bool) -> tuple[Capability, ...]:
    if provider in _MARKET_PREREQUISITES:
        return _CAPABILITIES
    if provider in _MACRO_PROVIDERS:
        return (
            Capability.EVENT_RISK_CHECK_AVAILABLE,
            Capability.SETUP_DETECTION_AVAILABLE,
            Capability.PLAN_DRAFT_AVAILABLE,
            Capability.POSITION_SIZING_AVAILABLE,
            Capability.PORTFOLIO_HEAT_CHECK_AVAILABLE,
        )
    if provider in _OFFICIAL_VERIFICATION_PROVIDERS:
        return (Capability.PLAN_DRAFT_AVAILABLE,)
    if provider in _OPTIONAL_DISCOVERY_PROVIDERS or not required:
        return ()
    return (
        Capability.SETUP_DETECTION_AVAILABLE,
        Capability.PLAN_DRAFT_AVAILABLE,
        Capability.POSITION_SIZING_AVAILABLE,
        Capability.PORTFOLIO_HEAT_CHECK_AVAILABLE,
    )


def _request_failure_code(failure: HistoricalBarsRequestFailure) -> ErrorCode:
    return {
        "authentication": ErrorCode.CREDENTIALS_MISSING,
        "permission_denied": ErrorCode.PERMISSION_DENIED,
        "rate_limited": ErrorCode.PROVIDER_UNAVAILABLE,
        "transport_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
        "provider_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
        "invalid_request": ErrorCode.INVALID_REQUEST,
        "invalid_response": ErrorCode.INVALID_RESPONSE,
    }[failure.reason.value]


def _historical_failure_code(failure: HistoricalBarsFailure) -> ErrorCode:
    return {
        "no_data": ErrorCode.PROVIDER_NO_DATA,
        "missing_expected_session": ErrorCode.PROVIDER_MISSING_SESSION,
        "duplicate_conflict": ErrorCode.PROVIDER_DUPLICATE_CONFLICT,
        "malformed_bar": ErrorCode.PROVIDER_MALFORMED_BAR,
        "stale": ErrorCode.PROVIDER_STALE,
        "future_or_incomplete_bar": ErrorCode.PROVIDER_FUTURE_OR_INCOMPLETE_BAR,
    }[failure.reason.value]


def _risk_dependencies(risk_policy: RiskPolicy | None) -> dict[Capability, tuple[ErrorCode, ...]]:
    if risk_policy is None:
        return {
            Capability.POSITION_SIZING_AVAILABLE: (ErrorCode.CONFIGURATION_INVALID,),
            Capability.PORTFOLIO_HEAT_CHECK_AVAILABLE: (ErrorCode.CONFIGURATION_INVALID,),
        }
    disabled: dict[Capability, tuple[ErrorCode, ...]] = {}
    if not risk_policy.sizing_enabled or any(
        getattr(risk_policy, field) is None for field in _SIZING_INPUTS
    ):
        _disable(disabled, (Capability.POSITION_SIZING_AVAILABLE,), ErrorCode.SIZING_UNAVAILABLE)
    if (
        not risk_policy.sizing_enabled
        or risk_policy.max_total_portfolio_heat_pct is None
        or risk_policy.existing_portfolio_heat_pct is None
    ):
        _disable(
            disabled,
            (Capability.PORTFOLIO_HEAT_CHECK_AVAILABLE,),
            ErrorCode.PORTFOLIO_HEAT_UNAVAILABLE,
        )
    return disabled


def evaluate_capabilities(
    *,
    source_health: Sequence[SourceHealth],
    provider_failures: Sequence[ProviderFailure] = (),
    historical_request_failure: HistoricalBarsRequestFailure | None = None,
    risk_policy: RiskPolicy | None = None,
) -> tuple[CapabilityState, ...]:
    """Evaluate global dependencies; per-symbol failures remain outside this result."""

    disabled = _risk_dependencies(risk_policy)
    for health in source_health:
        if not health.available:
            assert health.error_code is not None
            _disable(
                disabled,
                _dependencies(health.provider, required=health.required),
                health.error_code,
            )
    if historical_request_failure is not None:
        _disable(disabled, _CAPABILITIES, _request_failure_code(historical_request_failure))
    for failure in provider_failures:
        if failure.symbol is None:
            _disable(
                disabled,
                _dependencies(failure.provider, required=False),
                failure.error_code,
            )
    return _states(disabled)


def evaluate_data_quality(
    *,
    source_health: Sequence[SourceHealth],
    provider_failures: Sequence[ProviderFailure] = (),
    historical_failures: Sequence[HistoricalBarsFailure] = (),
    historical_request_failure: HistoricalBarsRequestFailure | None = None,
    current_price_failures: Sequence[ProviderFailure] = (),
    risk_policy: RiskPolicy | None = None,
) -> DataQualityResult:
    """Aggregate failure-matrix inputs while retaining global, source, and symbol scope."""

    capabilities = evaluate_capabilities(
        source_health=source_health,
        provider_failures=provider_failures,
        historical_request_failure=historical_request_failure,
        risk_policy=risk_policy,
    )
    global_reasons = [failure.error_code for failure in provider_failures if failure.symbol is None]
    global_reasons.extend(
        health.error_code for health in source_health if not health.available and health.error_code
    )
    if historical_request_failure is not None:
        global_reasons.append(_request_failure_code(historical_request_failure))
    symbol_disabled: dict[str, dict[Capability, tuple[ErrorCode, ...]]] = {}
    symbol_status: dict[str, PlanStatus] = {}
    full_symbol_capabilities = (
        Capability.WATCHLIST_METRICS_AVAILABLE,
        Capability.SETUP_DETECTION_AVAILABLE,
        Capability.PLAN_DRAFT_AVAILABLE,
        Capability.POSITION_SIZING_AVAILABLE,
    )
    for failure in provider_failures:
        if failure.symbol is not None:
            disabled = symbol_disabled.setdefault(failure.symbol, {})
            _disable(disabled, full_symbol_capabilities, failure.error_code)
            symbol_status[failure.symbol] = PlanStatus.BLOCKED
    for historical_failure in historical_failures:
        disabled = symbol_disabled.setdefault(historical_failure.symbol, {})
        _disable(
            disabled,
            full_symbol_capabilities,
            _historical_failure_code(historical_failure),
        )
        symbol_status[historical_failure.symbol] = PlanStatus.BLOCKED
    for failure in current_price_failures:
        if failure.symbol is None:
            raise ValueError("current price failures require symbol scope")
        disabled = symbol_disabled.setdefault(failure.symbol, {})
        _disable(disabled, (Capability.POSITION_SIZING_AVAILABLE,), failure.error_code)
        if symbol_status.get(failure.symbol) is not PlanStatus.BLOCKED:
            symbol_status[failure.symbol] = PlanStatus.REVIEW_REQUIRED
    symbol_capabilities = FrozenMap(
        {symbol: _states(disabled) for symbol, disabled in symbol_disabled.items()}
    )
    hard_global = historical_request_failure is not None or any(
        failure.symbol is None and failure.provider in _MARKET_PREREQUISITES
        for failure in provider_failures
    ) or any(
        not health.available and health.provider in _MARKET_PREREQUISITES
        for health in source_health
    )
    status = (
        DataQualityStatus.FAIL
        if hard_global
        else DataQualityStatus.DEGRADED
        if global_reasons or symbol_disabled or any(not state.available for state in capabilities)
        else DataQualityStatus.PASS
    )
    return DataQualityResult(
        status=status,
        capabilities=capabilities,
        global_reason_codes=tuple(dict.fromkeys(global_reasons)),
        symbol_capabilities=symbol_capabilities,
        symbol_plan_statuses=FrozenMap(symbol_status),
    )
