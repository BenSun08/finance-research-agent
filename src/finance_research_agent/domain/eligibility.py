"""Binary pre-scoring Product A instrument and watchlist eligibility gates."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from finance_research_agent.domain.enums import Capability, GateStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import GateResult, InstrumentIdentity, MarketSnapshot
from finance_research_agent.domain.policies import SetupPolicy, WatchlistItem

_RULE_VERSION = "r5-eligibility-1"


def _gate(reason: ErrorCode, message: str, evidence_ids: Sequence[str] = ()) -> GateResult:
    return GateResult(
        gate_id=f"eligibility-{reason.value.lower()}",
        status=GateStatus.BLOCK,
        reason_code=reason.value,
        message=message,
        evidence_ids=tuple(evidence_ids),
        capability=Capability.PLAN_DRAFT_AVAILABLE,
        rule_version=_RULE_VERSION,
    )


def evaluate_instrument_eligibility(
    *,
    instrument: InstrumentIdentity,
    watchlist_item: WatchlistItem,
    snapshot: MarketSnapshot,
    setup_policy: SetupPolicy,
    direction: str = "LONG",
    halted: bool = False,
) -> tuple[GateResult, ...]:
    """Return immutable binary blocking gates before setup detection or scoring."""

    gates: list[GateResult] = []
    evidence_ids = tuple(bar.evidence_id for bar in snapshot.completed_daily_bars)
    if instrument.symbol != watchlist_item.symbol or snapshot.instrument != instrument:
        gates.append(_gate(ErrorCode.CONFIGURATION_INVALID, "instrument context is inconsistent"))
    if (
        instrument.primary_exchange is None
        or instrument.listing_country != "US"
        or instrument.is_otc is not False
    ):
        gates.append(
            _gate(
                ErrorCode.UNSUPPORTED_INSTRUMENT,
                "instrument identity must be exchange-listed, U.S., and non-OTC",
            )
        )
    if instrument.instrument_type not in {"COMMON_STOCK", "ETF"}:
        gates.append(_gate(ErrorCode.UNSUPPORTED_INSTRUMENT, "instrument type is not eligible"))
    if instrument.instrument_type == "ETF" and (
        instrument.is_leveraged is not False or instrument.is_inverse is not False
    ):
        gates.append(
            _gate(ErrorCode.UNSUPPORTED_INSTRUMENT, "leveraged or inverse ETF is not eligible")
        )
    if instrument.is_active is not True or halted:
        gates.append(
            _gate(
                ErrorCode.UNSUPPORTED_INSTRUMENT, "instrument is halted or identity is not reliable"
            )
        )
    if direction != "LONG":
        gates.append(
            _gate(ErrorCode.UNSUPPORTED_INSTRUMENT, "only long research plans are eligible")
        )
    if len(snapshot.completed_daily_bars) < setup_policy.required_historical_sessions:
        gates.append(
            _gate(
                ErrorCode.PROVIDER_MISSING_SESSION,
                "insufficient valid completed history",
                evidence_ids,
            )
        )
    else:
        latest = snapshot.completed_daily_bars[-1]
        if latest.close < setup_policy.minimum_price:
            gates.append(
                _gate(
                    ErrorCode.UNSUPPORTED_INSTRUMENT,
                    "price is below setup policy minimum",
                    evidence_ids,
                )
            )
        volumes = tuple(bar.volume for bar in snapshot.completed_daily_bars)
        if any(volume is None for volume in volumes):
            gates.append(
                _gate(ErrorCode.PROVIDER_NO_DATA, "history lacks liquidity data", evidence_ids)
            )
        else:
            present_volumes = tuple(volume for volume in volumes if volume is not None)
            dollars = sorted(
                Decimal(bar.close) * Decimal(volume)
                for bar, volume in zip(snapshot.completed_daily_bars, present_volumes, strict=True)
            )
            median = dollars[len(dollars) // 2]
            if median < setup_policy.minimum_median_dollar_volume:
                gates.append(
                    _gate(
                        ErrorCode.UNSUPPORTED_INSTRUMENT,
                        "liquidity is below setup policy minimum",
                        evidence_ids,
                    )
                )
    if watchlist_item.role != "SATELLITE_ELIGIBLE":
        gates.append(_gate(ErrorCode.CONFIGURATION_INVALID, "watchlist role cannot create a plan"))
    elif watchlist_item.benchmark_symbol is None or watchlist_item.sector_proxy_symbol is None:
        gates.append(
            _gate(
                ErrorCode.CONFIGURATION_INVALID,
                "satellite plan requires benchmark and sector proxy",
            )
        )
    return tuple(gates)
