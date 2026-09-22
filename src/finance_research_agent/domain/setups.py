"""Gate-first detection and evidence-bearing levels for the two Product A setups."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Context, Decimal, localcontext
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from finance_research_agent.domain.enums import (
    Capability,
    Coverage,
    DataQualityStatus,
    GateStatus,
    PlanStatus,
)
from finance_research_agent.domain.events import EventAssessment
from finance_research_agent.domain.indicators import atr_percent, relative_return, sma, sma_slope
from finance_research_agent.domain.market import DailyBar, MarketDataSource, RegimeMarketSnapshot
from finance_research_agent.domain.metrics import MetricResult, MetricStatus
from finance_research_agent.domain.models import (
    EvidenceIds,
    GateResult,
    Identifier,
    MarketSnapshot,
    PriceObservation,
    StrictModel,
    Symbol,
)
from finance_research_agent.domain.policies import SetupPolicy, canonical_model_hash
from finance_research_agent.domain.quality import DataQualityResult
from finance_research_agent.domain.types import PositiveDecimal, UtcDatetime

FORMULA_VERSION = "r6-setups-1"
NUMERIC_CONTEXT = Context(prec=28)


class SetupType(StrEnum):
    BREAKOUT_CONTINUATION = "BREAKOUT_CONTINUATION"
    TREND_PULLBACK = "TREND_PULLBACK"


class CalculatedPrice(PriceObservation):
    """A derived scenario value, with the observation anchor and every input reference."""

    input_evidence_ids: EvidenceIds
    metric_ids: tuple[str, ...]
    policy_version: str
    formula_version: str = FORMULA_VERSION
    evidence_cutoff_at: UtcDatetime


class EntryZone(StrictModel):
    lower: CalculatedPrice
    upper: CalculatedPrice

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower.value > self.upper.value:
            raise ValueError("entry zone must be ordered")
        return self


class TargetScenario(StrictModel):
    price: CalculatedPrice
    reward_to_risk: PositiveDecimal


class PlanLevels(StrictModel):
    entry_zone: EntryZone
    candidate_stop: CalculatedPrice
    support_reference: CalculatedPrice
    target_scenarios: tuple[TargetScenario, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def long_levels(self) -> Self:
        if not self.candidate_stop.value < self.entry_zone.lower.value:
            raise ValueError("long stop must be below the entry zone")
        if any(t.price.value <= self.entry_zone.upper.value for t in self.target_scenarios):
            raise ValueError("long targets must be above the entry zone")
        return self


class CandidateExclusion(StrictModel):
    symbol: Symbol
    setup_type: SetupType | None = None
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    gates: tuple[GateResult, ...] = Field(min_length=1)


class RawSetup(StrictModel):
    symbol: Symbol
    setup_type: SetupType
    policy: SetupPolicy
    policy_hash: str
    formula_version: str = FORMULA_VERSION
    evidence_cutoff_at: UtcDatetime
    levels: PlanLevels
    entry_condition: str
    invalidation_condition: str
    volume_confirmation_threshold: PositiveDecimal
    metrics: tuple[MetricResult, ...]
    input_snapshots: tuple[MarketSnapshot, MarketSnapshot, MarketSnapshot]
    eligibility_gates: tuple[GateResult, ...]
    event_assessment: EventAssessment
    data_quality: DataQualityResult
    supporting_evidence_ids: EvidenceIds
    extension: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
    relative_strength: Decimal
    median_dollar_volume: PositiveDecimal

    @field_validator("metrics", mode="before")
    @classmethod
    def json_metric_arrays(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "json" and isinstance(value, list):
            converted = []
            for metric in value:
                if isinstance(metric, dict):
                    metric = dict(metric)
                    for name in (
                        "parameters",
                        "input_snapshot_ids",
                        "quality_flags",
                        "input_evidence_ids",
                    ):
                        if isinstance(metric.get(name), list):
                            metric[name] = tuple(
                                tuple(v) if isinstance(v, list) else v for v in metric[name]
                            )
                    if isinstance(metric.get("value"), str):
                        metric["value"] = Decimal(metric["value"])
                    for name in ("period_start", "period_end"):
                        if isinstance(metric.get(name), str):
                            metric[name] = date.fromisoformat(metric[name])
                    if isinstance(metric.get("calculated_at"), str):
                        metric["calculated_at"] = datetime.fromisoformat(metric["calculated_at"])
                converted.append(metric)
            return tuple(converted)
        return value

    @model_validator(mode="after")
    def coherent_inputs(self) -> Self:
        if self.symbol != self.input_snapshots[0].instrument.symbol:
            raise ValueError("setup symbol must match its source snapshot")
        if self.policy_hash != canonical_model_hash(self.policy):
            raise ValueError("setup policy hash mismatch")
        if any(metric.status is not MetricStatus.AVAILABLE for metric in self.metrics):
            raise ValueError("raw setup requires available metrics")
        return self

    @property
    def entry_zone(self) -> EntryZone:
        return self.levels.entry_zone

    @property
    def candidate_stop(self) -> CalculatedPrice:
        return self.levels.candidate_stop

    @property
    def support_reference(self) -> CalculatedPrice:
        return self.levels.support_reference

    @property
    def target_scenarios(self) -> tuple[TargetScenario, ...]:
        return self.levels.target_scenarios


class SetupDetectionResult(StrictModel):
    setups: tuple[RawSetup, ...]
    exclusions: tuple[CandidateExclusion, ...]


def setup_gate(reason: str, message: str, evidence_ids: tuple[str, ...] = ()) -> GateResult:
    return GateResult(
        gate_id=f"setup-{reason.lower()}",
        status=GateStatus.BLOCK,
        reason_code=reason,
        message=message,
        evidence_ids=evidence_ids,
        capability=Capability.SETUP_DETECTION_AVAILABLE,
        rule_version=FORMULA_VERSION,
    )


def required_gate_failures(
    symbol: str,
    eligibility_gates: tuple[GateResult, ...],
    event_assessment: EventAssessment,
    data_quality: DataQualityResult,
) -> tuple[GateResult, ...]:
    """R5 decisions are authoritative; only optional warnings may reach scoring."""
    failed = [g for g in eligibility_gates if g.status is not GateStatus.PASS]
    failed.extend(g for g in event_assessment.gates if g.status is GateStatus.BLOCK)
    if data_quality.status is DataQualityStatus.FAIL:
        failed.append(setup_gate("DATA_QUALITY_FAILED", "R5 data quality failed"))
    for capability in (
        Capability.WATCHLIST_METRICS_AVAILABLE,
        Capability.EVENT_RISK_CHECK_AVAILABLE,
        Capability.SETUP_DETECTION_AVAILABLE,
        Capability.PLAN_DRAFT_AVAILABLE,
    ):
        state = data_quality.symbol_capability(symbol, capability)
        if not state.available:
            failed.append(
                setup_gate(
                    "REQUIRED_DATA_MISSING",
                    f"R5 disabled {capability.value}: "
                    + ", ".join(code.value for code in state.reason_codes),
                    state.evidence_ids,
                )
            )
    if event_assessment.plan_status is PlanStatus.BLOCKED and not failed:
        failed.append(setup_gate("EVENT_BLOCKED", "R5 event assessment blocks the candidate"))
    return tuple(failed)


def _exclusion(symbol: str, gates: tuple[GateResult, ...]) -> SetupDetectionResult:
    return SetupDetectionResult(
        setups=(),
        exclusions=(
            CandidateExclusion(
                symbol=symbol,
                reason_codes=tuple(dict.fromkeys(g.reason_code for g in gates)),
                gates=gates,
            ),
        ),
    )


def validate_setup_policy(policy: SetupPolicy) -> None:
    """Assign explicit, versioned semantics to R2's variable-length policy collections."""
    if (
        len(policy.moving_average_windows) != 3
        or tuple(sorted(set(policy.moving_average_windows))) != policy.moving_average_windows
        or any(n <= 0 for n in policy.moving_average_windows)
        or len(policy.trend_slope_windows) != 2
        or any(n <= 0 for n in policy.trend_slope_windows)
        or len(policy.entry_zone_atr_buffers) != 2
        or not 0 < policy.entry_zone_atr_buffers[0] <= policy.entry_zone_atr_buffers[1]
        or len(policy.extension_limits) != 2
        or not 0 < policy.extension_limits[0] <= policy.extension_limits[1]
        or len(policy.pullback_support_tolerances) != 2
        or not 0
        < policy.pullback_support_tolerances[0]
        <= policy.pullback_support_tolerances[1]
        < 1
        or not policy.restrengthening_conditions
        or len(set(policy.restrengthening_conditions)) != len(policy.restrengthening_conditions)
        or not set(policy.restrengthening_conditions)
        <= {
            "above_support",
            "positive_close",
            "positive_relative_strength",
        }
        or policy.score_weights != tuple(map(Decimal, (25, 20, 20, 15, 10, 10)))
    ):
        raise ValueError("unsupported R6 setup policy configuration")


def _project(snapshot: MarketSnapshot, count: int) -> RegimeMarketSnapshot:
    """Small domain-only projection; never imports the application bridge or provider shapes."""
    bars = snapshot.completed_daily_bars[-count:]
    return RegimeMarketSnapshot(
        schema_version="market-snapshot-v1",
        snapshot_id="setup-" + sha256(snapshot.model_dump_json().encode()).hexdigest(),
        symbol=snapshot.instrument.symbol,
        as_of=max(b.evidence_cutoff_at for b in bars),
        currency=snapshot.instrument.currency,
        source=MarketDataSource.NORMALIZED_PROVIDER,
        completed_daily_bars=tuple(
            DailyBar(
                session_date=b.session_date,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
            for b in bars
        ),
        quality_flags=snapshot.quality_flags,
        input_evidence_ids=tuple(dict.fromkeys(b.evidence_id for b in bars)),
    )


def _metrics(
    snapshot: MarketSnapshot,
    benchmark: MarketSnapshot,
    sector: MarketSnapshot,
    policy: SetupPolicy,
    cutoff: datetime,
) -> tuple[MetricResult, ...]:
    short, intermediate, primary = policy.moving_average_windows
    slope, primary_slope = policy.trend_slope_windows
    return (
        sma(_project(snapshot, intermediate), window=intermediate, cutoff_at=cutoff),
        sma(_project(snapshot, primary), window=primary, cutoff_at=cutoff),
        sma_slope(
            _project(snapshot, intermediate + slope),
            window=intermediate,
            lookback=slope,
            cutoff_at=cutoff,
        ),
        sma_slope(
            _project(snapshot, primary + primary_slope),
            window=primary,
            lookback=primary_slope,
            cutoff_at=cutoff,
        ),
        relative_return(
            _project(snapshot, intermediate + 1),
            _project(benchmark, intermediate + 1),
            window=intermediate,
            cutoff_at=cutoff,
        ),
        relative_return(
            _project(snapshot, short + 1),
            _project(sector, short + 1),
            window=short,
            cutoff_at=cutoff,
        ),
        atr_percent(
            _project(snapshot, policy.atr_window + 1), window=policy.atr_window, cutoff_at=cutoff
        ),
    )


def _value(metric: MetricResult) -> Decimal:
    assert metric.status is MetricStatus.AVAILABLE and metric.value is not None
    return metric.value


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _levels(
    kind: SetupType,
    snapshot: MarketSnapshot,
    policy: SetupPolicy,
    metrics: tuple[MetricResult, ...],
    cutoff: datetime,
) -> PlanLevels:
    bars = snapshot.completed_daily_bars
    anchor = bars[-1]
    support = _value(metrics[0])
    atr = _value(metrics[-1]) * anchor.close
    lower_buffer, upper_buffer = policy.entry_zone_atr_buffers
    resistance = max(b.high for b in bars[-policy.breakout_lookback - 1 : -1])
    trigger = resistance if kind is SetupType.BREAKOUT_CONTINUATION else max(support, anchor.high)
    lower = trigger + lower_buffer * atr
    upper = trigger + upper_buffer * atr
    stop = support - lower_buffer * atr
    if kind is SetupType.TREND_PULLBACK:
        stop = (
            min(support, min(b.low for b in bars[-policy.breakout_lookback :])) - lower_buffer * atr
        )

    def price(value: Decimal) -> CalculatedPrice:
        return CalculatedPrice(
            instrument_id=anchor.instrument_id,
            value=value,
            currency=snapshot.instrument.currency,
            session=anchor.session,
            provider=anchor.provider,
            feed=anchor.feed,
            coverage=anchor.coverage,
            observed_at=anchor.source_timestamp,
            retrieved_at=anchor.retrieved_at,
            evidence_id=anchor.evidence_id,
            quality_flags=tuple(sorted(set(snapshot.quality_flags + anchor.quality_flags))),
            input_evidence_ids=tuple(dict.fromkeys(b.evidence_id for b in bars)),
            metric_ids=tuple(m.metric_id for m in metrics),
            policy_version=policy.version,
            evidence_cutoff_at=cutoff,
        )

    return PlanLevels(
        entry_zone=EntryZone(lower=price(lower), upper=price(upper)),
        candidate_stop=price(stop),
        support_reference=price(support),
        target_scenarios=tuple(
            TargetScenario(
                price=price(upper + multiple * (upper - stop)),
                reward_to_risk=multiple,
            )
            for multiple in (policy.minimum_reward_to_risk, policy.minimum_reward_to_risk + 1)
        ),
    )


def calculate_plan_levels(
    setup: RawSetup, snapshot: MarketSnapshot, policy: SetupPolicy
) -> PlanLevels:
    """Recalculate scenario levels only against the exact frozen setup inputs."""
    if snapshot != setup.input_snapshots[0] or policy != setup.policy:
        raise ValueError("plan levels require the setup's frozen snapshot and policy")
    with localcontext(NUMERIC_CONTEXT):
        return _levels(setup.setup_type, snapshot, policy, setup.metrics, setup.evidence_cutoff_at)


def assess_setups(
    *,
    snapshot: MarketSnapshot,
    benchmark: MarketSnapshot | None,
    sector_proxy: MarketSnapshot | None,
    setup_policy: SetupPolicy,
    evidence_cutoff_at: datetime,
    eligibility_gates: tuple[GateResult, ...],
    event_assessment: EventAssessment,
    data_quality: DataQualityResult,
) -> SetupDetectionResult:
    """Return eligible setups or explicit exclusions; required inputs never become scores."""
    symbol = snapshot.instrument.symbol
    blocked = required_gate_failures(symbol, eligibility_gates, event_assessment, data_quality)
    if blocked:
        return _exclusion(symbol, blocked)
    validate_setup_policy(setup_policy)
    if evidence_cutoff_at.tzinfo is None or evidence_cutoff_at.utcoffset() != timedelta(0):
        raise ValueError("evidence cutoff must be UTC")
    if benchmark is None or sector_proxy is None:
        return _exclusion(
            symbol, (setup_gate("REQUIRED_DATA_MISSING", "benchmark or sector missing"),)
        )
    policy = setup_policy
    snapshots = (snapshot, benchmark, sector_proxy)
    required = max(
        policy.required_historical_sessions,
        policy.moving_average_windows[-1] + policy.trend_slope_windows[-1],
        policy.moving_average_windows[1] + policy.trend_slope_windows[0],
        policy.atr_window + 1,
        policy.breakout_lookback + 1,
    )
    if (
        any(not s.completed_daily_bars for s in snapshots)
        or len(snapshot.completed_daily_bars) < required
    ):
        return _exclusion(
            symbol, (setup_gate("REQUIRED_DATA_MISSING", "insufficient completed history"),)
        )
    for source in snapshots:
        if (
            source.instrument.currency != "USD"
            or any(
                b.source_timestamp > evidence_cutoff_at
                or b.evidence_cutoff_at > evidence_cutoff_at
                or b.session_date >= evidence_cutoff_at.date()
                for b in source.completed_daily_bars
            )
            or (
                source.latest_price is not None
                and source.latest_price.observed_at > evidence_cutoff_at
            )
            or any(b.end_at > evidence_cutoff_at for b in source.current_session_bars)
        ):
            return _exclusion(
                symbol, (setup_gate("INVALID_EVIDENCE", "invalid currency or future evidence"),)
            )
    bars = snapshot.completed_daily_bars
    if any(b.volume is None or b.coverage is not Coverage.CONSOLIDATED for b in bars):
        return _exclusion(
            symbol,
            (setup_gate("REQUIRED_DATA_MISSING", "consolidated liquidity history required"),),
        )
    with localcontext(NUMERIC_CONTEXT):
        metrics = _metrics(snapshot, benchmark, sector_proxy, policy, evidence_cutoff_at)
        if any(m.status is not MetricStatus.AVAILABLE for m in metrics):
            return _exclusion(
                symbol,
                (setup_gate("REQUIRED_DATA_MISSING", "required metric unavailable or misaligned"),),
            )
        intermediate, primary, slope, primary_slope, relative, sector_relative, atr_pct = map(
            _value, metrics
        )
        close = bars[-1].close
        volume = _median(tuple(b.close * b.volume for b in bars if b.volume is not None))
        if close < policy.minimum_price or volume < policy.minimum_median_dollar_volume:
            return _exclusion(
                symbol,
                (setup_gate("LIQUIDITY_OR_PRICE_FAILED", "price or liquidity below policy"),),
            )
        if close <= primary or primary_slope <= 0:
            return _exclusion(
                symbol, (setup_gate("PRIMARY_TREND_FAILED", "positive primary trend required"),)
            )
        if relative <= 0:
            return _exclusion(
                symbol,
                (
                    setup_gate(
                        "RELATIVE_STRENGTH_FAILED", "positive benchmark relative strength required"
                    ),
                ),
            )
        current = snapshot.latest_price.value if snapshot.latest_price is not None else close
        extension = max(Decimal(0), current / intermediate - 1)
        resistance = max(b.high for b in bars[-policy.breakout_lookback - 1 : -1])
        atr = atr_pct * close
        breakout = (
            close > intermediate
            and slope > 0
            and sector_relative >= 0
            and close >= resistance - policy.entry_zone_atr_buffers[0] * atr
        )
        near_support = abs(close / intermediate - 1) <= policy.pullback_support_tolerances[0]
        retracement = (resistance - close) / resistance
        structure = min(b.low for b in bars[-policy.breakout_lookback :]) >= primary
        pullback = (
            near_support and 0 < retracement <= policy.pullback_support_tolerances[1] and structure
        )
        kind = (
            SetupType.BREAKOUT_CONTINUATION
            if breakout
            else SetupType.TREND_PULLBACK
            if pullback
            else None
        )
        if kind is None:
            return _exclusion(
                symbol, (setup_gate("SETUP_NOT_CONFIRMED", "neither approved setup meets policy"),)
            )
        limit = policy.extension_limits[0 if kind is SetupType.BREAKOUT_CONTINUATION else 1]
        if extension > limit:
            return _exclusion(
                symbol, (setup_gate("EXTENSION_LIMIT", "extension exceeds setup policy"),)
            )
        levels = _levels(kind, snapshot, policy, metrics, evidence_cutoff_at)
        condition = (
            "If price clears the entry zone with consolidated volume confirmation above "
            "the completed-base median volume"
            if kind is SetupType.BREAKOUT_CONTINUATION
            else "If re-strengthening clears the entry zone and "
            + " and ".join(policy.restrengthening_conditions)
        )
        setup = RawSetup(
            symbol=symbol,
            setup_type=kind,
            policy=policy,
            policy_hash=canonical_model_hash(policy),
            evidence_cutoff_at=evidence_cutoff_at,
            levels=levels,
            entry_condition=condition,
            invalidation_condition="Invalid if price falls below the candidate stop",
            volume_confirmation_threshold=_median(
                tuple(
                    Decimal(b.volume)
                    for b in bars[-policy.breakout_lookback :]
                    if b.volume is not None
                )
            ),
            metrics=metrics,
            input_snapshots=snapshots,
            eligibility_gates=eligibility_gates,
            event_assessment=event_assessment,
            data_quality=data_quality,
            supporting_evidence_ids=tuple(
                dict.fromkeys(b.evidence_id for b in snapshot.current_session_bars)
            ),
            extension=extension,
            relative_strength=relative,
            median_dollar_volume=volume,
        )
        return SetupDetectionResult(setups=(setup,), exclusions=())


def detect_setups(
    *,
    snapshot: MarketSnapshot,
    benchmark: MarketSnapshot | None,
    sector_proxy: MarketSnapshot | None,
    setup_policy: SetupPolicy,
    evidence_cutoff_at: datetime,
    eligibility_gates: tuple[GateResult, ...],
    event_assessment: EventAssessment,
    data_quality: DataQualityResult,
) -> tuple[RawSetup, ...]:
    """Tuple convenience API; use assess_setups to retain all exclusion diagnostics."""
    return assess_setups(
        snapshot=snapshot,
        benchmark=benchmark,
        sector_proxy=sector_proxy,
        setup_policy=setup_policy,
        evidence_cutoff_at=evidence_cutoff_at,
        eligibility_gates=eligibility_gates,
        event_assessment=event_assessment,
        data_quality=data_quality,
    ).setups
