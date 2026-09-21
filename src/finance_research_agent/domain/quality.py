"""Deterministic Product A data-quality and scoped capability evaluation."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from finance_research_agent.domain.enums import Capability, DataQualityStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    CapabilityState,
    ProviderFailure,
    SourceHealth,
    StrictModel,
)
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.market_data.historical import (
    HistoricalBarsFailure,
    HistoricalBarsRequestFailure,
)

_CAPABILITIES = tuple(Capability)
_PLAN_DEPENDENCIES = (
    Capability.EVENT_RISK_CHECK_AVAILABLE,
    Capability.SETUP_DETECTION_AVAILABLE,
    Capability.PLAN_DRAFT_AVAILABLE,
    Capability.POSITION_SIZING_AVAILABLE,
    Capability.PORTFOLIO_HEAT_CHECK_AVAILABLE,
)


class DataQualityResult(StrictModel):
    """Global availability plus explicitly isolated per-symbol availability."""

    status: DataQualityStatus
    capabilities: tuple[CapabilityState, ...] = Field(min_length=8, max_length=8)
    symbol_capabilities: FrozenMap[str, tuple[CapabilityState, ...]] = FrozenMap({})

    def capability(self, capability: Capability) -> CapabilityState:
        return next(state for state in self.capabilities if state.capability is capability)

    def symbol_capability(self, symbol: str, capability: Capability) -> CapabilityState:
        states = self.symbol_capabilities[symbol]
        return next(state for state in states if state.capability is capability)


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


def _is_macro(health: SourceHealth) -> bool:
    return "macro" in health.provider or health.provider in {"fed", "bls", "bea"}


def _is_market_calendar(health: SourceHealth) -> bool:
    return (
        health.provider == "market-calendar"
        or health.error_code is ErrorCode.MARKET_CALENDAR_UNAVAILABLE
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


def evaluate_capabilities(
    *,
    source_health: Sequence[SourceHealth],
    provider_failures: Sequence[ProviderFailure] = (),
    historical_request_failure: HistoricalBarsRequestFailure | None = None,
) -> tuple[CapabilityState, ...]:
    """Evaluate only global capabilities; symbol failures deliberately stay outside it."""

    disabled: dict[Capability, tuple[ErrorCode, ...]] = {}
    global_reasons: list[ErrorCode] = []
    if historical_request_failure is not None:
        global_reasons.append(_request_failure_code(historical_request_failure))
    global_reasons.extend(
        failure.error_code for failure in provider_failures if failure.symbol is None
    )
    for health in source_health:
        if health.available:
            continue
        assert health.error_code is not None
        if _is_market_calendar(health):
            _disable(disabled, _CAPABILITIES, ErrorCode.MARKET_CALENDAR_UNAVAILABLE)
        elif _is_macro(health) and health.required:
            _disable(
                disabled,
                (Capability.EVENT_RISK_CHECK_AVAILABLE, *_PLAN_DEPENDENCIES[1:]),
                health.error_code,
            )
        elif health.required:
            _disable(disabled, _PLAN_DEPENDENCIES, health.error_code)
    if global_reasons:
        _disable(disabled, _CAPABILITIES, global_reasons[0])
    return _states(disabled)


def evaluate_data_quality(
    *,
    source_health: Sequence[SourceHealth],
    provider_failures: Sequence[ProviderFailure] = (),
    historical_failures: Sequence[HistoricalBarsFailure] = (),
    historical_request_failure: HistoricalBarsRequestFailure | None = None,
) -> DataQualityResult:
    """Aggregate source health without flattening global and per-symbol failures."""

    capabilities = evaluate_capabilities(
        source_health=source_health,
        provider_failures=provider_failures,
        historical_request_failure=historical_request_failure,
    )
    symbol_reasons: dict[str, list[ErrorCode]] = {}
    for failure in provider_failures:
        if failure.symbol is not None:
            symbol_reasons.setdefault(failure.symbol, []).append(failure.error_code)
    for historical_failure in historical_failures:
        symbol_reasons.setdefault(historical_failure.symbol, []).append(
            _historical_failure_code(historical_failure)
        )
    symbols = FrozenMap(
        {
            symbol: _states(
                {capability: tuple(dict.fromkeys(reasons)) for capability in _PLAN_DEPENDENCIES}
            )
            for symbol, reasons in symbol_reasons.items()
        }
    )
    any_global_disabled = any(not state.available for state in capabilities)
    global_failure = (
        historical_request_failure is not None
        or any(failure.symbol is None for failure in provider_failures)
        or any(_is_market_calendar(health) and not health.available for health in source_health)
    )
    status = (
        DataQualityStatus.FAIL
        if global_failure
        else DataQualityStatus.DEGRADED
        if any_global_disabled or symbol_reasons
        else DataQualityStatus.PASS
    )
    return DataQualityResult(
        status=status,
        capabilities=capabilities,
        symbol_capabilities=symbols,
    )
