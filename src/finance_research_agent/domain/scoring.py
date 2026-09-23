"""Deterministic Product A setup scoring, penalties, and candidate ranking."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Context, Decimal, localcontext
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from finance_research_agent.domain.enums import Capability, GateStatus, PlanStatus
from finance_research_agent.domain.events import EventAssessment
from finance_research_agent.domain.models import (
    EvidenceIds,
    GateResult,
    Identifier,
    StrictModel,
    Symbol,
)
from finance_research_agent.domain.policies import SetupPolicy
from finance_research_agent.domain.quality import DataQualityResult
from finance_research_agent.domain.regime import Regime, RegimePolicy, RegimeResult
from finance_research_agent.domain.setups import (
    CandidateExclusion,
    PlanLevels,
    RawSetup,
    SetupType,
    required_gate_failures,
    validate_setup_policy,
)
from finance_research_agent.domain.types import UtcDatetime

FORMULA_VERSION = "r6-scoring-1"
NUMERIC_CONTEXT = Context(prec=28)
PLAN_CAP = 5
HIGHLIGHT_CAP = 3
CORRELATION_THRESHOLD = Decimal("0.90")


class ScoreComponentName(StrEnum):
    SETUP_QUALITY = "SETUP_QUALITY"
    TREND_QUALITY = "TREND_QUALITY"
    RELATIVE_STRENGTH = "RELATIVE_STRENGTH"
    REWARD_RISK_QUALITY = "REWARD_RISK_QUALITY"
    LIQUIDITY_QUALITY = "LIQUIDITY_QUALITY"
    CATALYST_EVIDENCE_QUALITY = "CATALYST_EVIDENCE_QUALITY"


class PenaltyName(StrEnum):
    EXTENSION_PENALTY = "EXTENSION_PENALTY"
    EVENT_UNCERTAINTY_PENALTY = "EVENT_UNCERTAINTY_PENALTY"
    CORRELATION_CONCENTRATION_PENALTY = "CORRELATION_CONCENTRATION_PENALTY"
    DATA_QUALITY_PENALTY = "DATA_QUALITY_PENALTY"


class ScoreComponent(StrictModel):
    name: ScoreComponentName
    quality: Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
    calculation: Annotated[str, Field(min_length=1, max_length=500)]
    metric_ids: tuple[Identifier, ...]
    evidence_ids: EvidenceIds
    evidence_cutoff_at: UtcDatetime
    weight: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)] = Decimal(0)
    points: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)] = Decimal(0)
    formula_version: str = FORMULA_VERSION


class ScorePenalty(StrictModel):
    name: PenaltyName
    points: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    evidence_ids: EvidenceIds
    evidence_cutoff_at: UtcDatetime
    formula_version: str = FORMULA_VERSION


class CorrelationEvidence(StrictModel):
    left_symbol: Symbol
    right_symbol: Symbol
    coefficient: Annotated[Decimal, Field(ge=-1, le=1, allow_inf_nan=False)]
    evidence_ids: EvidenceIds
    observed_at: UtcDatetime
    method_version: Identifier
    exposure_description: Annotated[str, Field(min_length=1, max_length=500)]


class SetupCandidate(StrictModel):
    candidate_id: Identifier
    symbol: Symbol
    setup_type: SetupType
    policy_version: str
    policy_hash: str
    regime_policy_version: str
    formula_version: str = FORMULA_VERSION
    evidence_cutoff_at: UtcDatetime
    levels: PlanLevels
    entry_condition: str
    invalidation_condition: str
    event_assessment: EventAssessment
    data_quality: DataQualityResult
    components: tuple[ScoreComponent, ...] = Field(min_length=6, max_length=6)
    penalties: tuple[ScorePenalty, ...] = Field(min_length=4, max_length=4)
    positive_score: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
    total_score: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
    plan_status: PlanStatus
    data_quality_rank: Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
    liquidity_rank: Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
    selected_for_plan: bool = False
    executive_highlight: bool = False
    secondary_alternative: bool = False
    primary_symbol: Symbol | None = None
    selection_reasons: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def coherent_score(self) -> Self:
        with localcontext(NUMERIC_CONTEXT):
            if tuple(component.name for component in self.components) != tuple(ScoreComponentName):
                raise ValueError("score components must use canonical order")
            if tuple(penalty.name for penalty in self.penalties) != tuple(PenaltyName):
                raise ValueError("score penalties must use canonical order")
            positive = sum((component.points for component in self.components), Decimal(0))
            penalty_total = sum((penalty.points for penalty in self.penalties), Decimal(0))
            if self.positive_score != positive:
                raise ValueError("positive score must equal component points")
            if self.total_score != max(Decimal(0), positive - penalty_total):
                raise ValueError("total score must equal positive score less visible penalties")
            if self.secondary_alternative and self.primary_symbol is None:
                raise ValueError("secondary alternatives require a primary symbol")
            if self.selected_for_plan and self.selection_reasons:
                raise ValueError("selected candidates must not carry rejection reasons")
        return self


class BlockedBeforeScoring(Exception):
    """Raised when an authoritative pre-score gate blocks component evaluation."""

    def __init__(self, exclusion: CandidateExclusion) -> None:
        super().__init__(f"{exclusion.symbol} blocked before scoring")
        self.exclusion = exclusion


ComponentEvaluator = Callable[
    [RawSetup, EventAssessment, DataQualityResult],
    tuple[ScoreComponent, ...],
]


def _clamp_quality(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("score quality must be finite")
    return max(Decimal(0), min(Decimal(1), value))


def _metric_ids(setup: RawSetup, indexes: Sequence[int]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(setup.metrics[index].metric_id for index in indexes))


def _evidence_ids(setup: RawSetup, indexes: Sequence[int] | None = None) -> tuple[str, ...]:
    metrics = setup.metrics if indexes is None else tuple(setup.metrics[index] for index in indexes)
    return tuple(dict.fromkeys(eid for metric in metrics for eid in metric.input_evidence_ids))


def evaluate_components(
    setup: RawSetup,
    event_assessment: EventAssessment,
    data_quality: DataQualityResult,
) -> tuple[ScoreComponent, ...]:
    """Calculate the six positive score components without inventing catalyst evidence."""

    del event_assessment, data_quality
    with localcontext(NUMERIC_CONTEXT):
        best_reward_to_risk = max(target.reward_to_risk for target in setup.target_scenarios)
        reward_denom = best_reward_to_risk + Decimal(1)
        qualities = {
            ScoreComponentName.SETUP_QUALITY: _clamp_quality(Decimal(1) - setup.extension),
            ScoreComponentName.TREND_QUALITY: _clamp_quality(
                (setup.metrics[2].value or Decimal(0)) * Decimal(25)
            ),
            ScoreComponentName.RELATIVE_STRENGTH: _clamp_quality(
                setup.relative_strength * Decimal(25)
            ),
            ScoreComponentName.REWARD_RISK_QUALITY: _clamp_quality(
                best_reward_to_risk / reward_denom
            ),
            ScoreComponentName.LIQUIDITY_QUALITY: _clamp_quality(
                setup.median_dollar_volume / (setup.policy.minimum_median_dollar_volume * 5)
            ),
            ScoreComponentName.CATALYST_EVIDENCE_QUALITY: Decimal(0),
        }
        metric_map = {
            ScoreComponentName.SETUP_QUALITY: (0, 2, 6),
            ScoreComponentName.TREND_QUALITY: (0, 1, 2, 3),
            ScoreComponentName.RELATIVE_STRENGTH: (4, 5),
            ScoreComponentName.REWARD_RISK_QUALITY: (6,),
            ScoreComponentName.LIQUIDITY_QUALITY: (0,),
            ScoreComponentName.CATALYST_EVIDENCE_QUALITY: (),
        }
        calculations = {
            ScoreComponentName.SETUP_QUALITY: "Extension within setup-policy support window",
            ScoreComponentName.TREND_QUALITY: "Positive intermediate and primary trend metrics",
            ScoreComponentName.RELATIVE_STRENGTH: "Benchmark and sector relative strength",
            ScoreComponentName.REWARD_RISK_QUALITY: (
                f"best scenario {best_reward_to_risk} reward/risk "
                f"normalized against denominator {reward_denom}"
            ),
            ScoreComponentName.LIQUIDITY_QUALITY: "Median dollar volume versus setup policy",
            ScoreComponentName.CATALYST_EVIDENCE_QUALITY: (
                "No verified catalyst evidence supplied to R6 scoring"
            ),
        }
        return tuple(
            ScoreComponent(
                name=name,
                quality=qualities[name],
                calculation=calculations[name],
                metric_ids=_metric_ids(setup, metric_map[name]),
                evidence_ids=_evidence_ids(setup, metric_map[name])
                if metric_map[name]
                else setup.supporting_evidence_ids,
                evidence_cutoff_at=setup.evidence_cutoff_at,
            )
            for name in ScoreComponentName
        )


def _weighted_components(
    components: tuple[ScoreComponent, ...], policy: SetupPolicy
) -> tuple[ScoreComponent, ...]:
    if tuple(component.name for component in components) != tuple(ScoreComponentName):
        raise ValueError("component evaluator must return every component in canonical order")
    result: list[ScoreComponent] = []
    for component, weight in zip(components, policy.score_weights, strict=True):
        if not component.quality.is_finite():
            raise ValueError("component quality must be finite")
        result.append(
            component.model_copy(
                update={
                    "weight": weight,
                    "points": component.quality * weight,
                }
            )
        )
    return tuple(result)


def _penalty(
    name: PenaltyName,
    points: Decimal,
    reason: str,
    setup: RawSetup,
    evidence_ids: tuple[str, ...] = (),
) -> ScorePenalty:
    return ScorePenalty(
        name=name,
        points=points,
        reason=reason,
        evidence_ids=evidence_ids,
        evidence_cutoff_at=setup.evidence_cutoff_at,
    )


def _visible_penalties(
    setup: RawSetup,
    event_assessment: EventAssessment,
    data_quality: DataQualityResult,
) -> tuple[ScorePenalty, ...]:
    event_warning = any(gate.status is GateStatus.WARNING for gate in event_assessment.gates)
    setup_quality_restricted = any(
        not data_quality.symbol_capability(setup.symbol, capability).available
        for capability in (
            Capability.WATCHLIST_METRICS_AVAILABLE,
            Capability.EVENT_RISK_CHECK_AVAILABLE,
            Capability.SETUP_DETECTION_AVAILABLE,
            Capability.PLAN_DRAFT_AVAILABLE,
        )
    )
    return (
        _penalty(
            PenaltyName.EXTENSION_PENALTY,
            Decimal(0),
            "Extension passed the setup-policy hard gate",
            setup,
        ),
        _penalty(
            PenaltyName.EVENT_UNCERTAINTY_PENALTY,
            Decimal(5) if event_warning else Decimal(0),
            "Visible event warning" if event_warning else "No event warning penalty",
            setup,
            tuple(
                dict.fromkeys(
                    eid
                    for gate in event_assessment.gates
                    if gate.status is GateStatus.WARNING
                    for eid in gate.evidence_ids
                )
            ),
        ),
        _penalty(
            PenaltyName.CORRELATION_CONCENTRATION_PENALTY,
            Decimal(0),
            "Duplicate-exposure review has not applied a penalty",
            setup,
        ),
        _penalty(
            PenaltyName.DATA_QUALITY_PENALTY,
            Decimal(5) if setup_quality_restricted else Decimal(0),
            "Visible degraded data quality"
            if setup_quality_restricted
            else "No data quality penalty",
            setup,
        ),
    )


def _plan_status(
    event_assessment: EventAssessment, data_quality: DataQualityResult, symbol: str
) -> PlanStatus:
    if event_assessment.plan_status is PlanStatus.REVIEW_REQUIRED:
        return PlanStatus.REVIEW_REQUIRED
    symbol_status = data_quality.symbol_plan_status(symbol)
    if symbol_status is PlanStatus.REVIEW_REQUIRED:
        return PlanStatus.REVIEW_REQUIRED
    return PlanStatus.DRAFT


def score_candidate(
    setup: RawSetup,
    *,
    setup_policy: SetupPolicy,
    eligibility_gates: tuple[GateResult, ...],
    event_assessment: EventAssessment,
    data_quality: DataQualityResult,
    regime_policy_version: str,
    component_evaluator: ComponentEvaluator = evaluate_components,
) -> SetupCandidate:
    """Score one setup only after unchanged R5 gates have passed."""

    validate_setup_policy(setup.policy)
    if setup_policy != setup.policy:
        raise ValueError("score_candidate requires the setup policy frozen into the setup")
    if event_assessment != setup.event_assessment:
        raise ValueError("score_candidate requires the event assessment frozen into the setup")
    if data_quality != setup.data_quality:
        raise ValueError("score_candidate requires the data quality frozen into the setup")
    effective_data_quality = setup.data_quality
    effective_event_assessment = setup.event_assessment
    if not regime_policy_version:
        raise ValueError("regime policy version must not be empty")
    gates = required_gate_failures(
        setup.symbol,
        tuple(dict.fromkeys((*setup.eligibility_gates, *eligibility_gates))),
        effective_event_assessment,
        effective_data_quality,
    )
    if gates:
        raise BlockedBeforeScoring(
            CandidateExclusion(
                symbol=setup.symbol,
                setup_type=setup.setup_type,
                reason_codes=tuple(dict.fromkeys(gate.reason_code for gate in gates)),
                gates=gates,
            )
        )
    with localcontext(NUMERIC_CONTEXT):
        components = _weighted_components(
            component_evaluator(setup, effective_event_assessment, effective_data_quality),
            setup.policy,
        )
        penalties = _visible_penalties(setup, effective_event_assessment, effective_data_quality)
        positive = sum((component.points for component in components), Decimal(0))
        total = max(
            Decimal(0), positive - sum((penalty.points for penalty in penalties), Decimal(0))
        )
        return SetupCandidate(
            candidate_id=f"{setup.symbol}-{setup.setup_type.value.lower()}",
            symbol=setup.symbol,
            setup_type=setup.setup_type,
            policy_version=setup.policy.version,
            policy_hash=setup.policy_hash,
            regime_policy_version=regime_policy_version,
            evidence_cutoff_at=setup.evidence_cutoff_at,
            levels=setup.levels,
            entry_condition=setup.entry_condition,
            invalidation_condition=setup.invalidation_condition,
            event_assessment=effective_event_assessment,
            data_quality=effective_data_quality,
            components=components,
            penalties=penalties,
            positive_score=positive,
            total_score=total,
            plan_status=_plan_status(
                effective_event_assessment, effective_data_quality, setup.symbol
            ),
            data_quality_rank=Decimal(1),
            liquidity_rank=_clamp_quality(
                setup.median_dollar_volume / (setup.policy.minimum_median_dollar_volume * 10)
            ),
        )


def _component(candidate: SetupCandidate, name: ScoreComponentName) -> ScoreComponent:
    return next(component for component in candidate.components if component.name is name)


def _candidate_positive(candidate: SetupCandidate) -> Decimal:
    return sum(
        (component.quality * component.weight for component in candidate.components), Decimal(0)
    )


def _candidate_total(candidate: SetupCandidate) -> Decimal:
    return max(
        Decimal(0),
        _candidate_positive(candidate)
        - sum((penalty.points for penalty in candidate.penalties), Decimal(0)),
    )


def _sort_key(candidate: SetupCandidate) -> tuple[Decimal, ...] | tuple[object, ...]:
    return (
        -_candidate_total(candidate),
        -_component(candidate, ScoreComponentName.SETUP_QUALITY).quality,
        -_component(candidate, ScoreComponentName.RELATIVE_STRENGTH).quality,
        -_component(candidate, ScoreComponentName.REWARD_RISK_QUALITY).quality,
        -candidate.data_quality_rank,
        -candidate.liquidity_rank,
        candidate.symbol,
        candidate.candidate_id,
    )


def _sorted(candidates: tuple[SetupCandidate, ...]) -> tuple[SetupCandidate, ...]:
    return tuple(sorted(candidates, key=_sort_key))


def _without_correlation_penalty(candidate: SetupCandidate) -> SetupCandidate:
    """Normalize a prior ranking result before recomputing correlation exposure."""
    penalty = candidate.penalties[2].model_copy(
        update={
            "points": Decimal(0),
            "reason": "Duplicate-exposure review has not applied a penalty",
            "evidence_ids": (),
        }
    )
    penalties = (*candidate.penalties[:2], penalty, *candidate.penalties[3:])
    positive = _candidate_positive(candidate)
    total = max(Decimal(0), positive - sum((item.points for item in penalties), Decimal(0)))
    return candidate.model_copy(
        update={
            "penalties": penalties,
            "positive_score": positive,
            "total_score": total,
            "selected_for_plan": False,
            "executive_highlight": False,
            "secondary_alternative": False,
            "primary_symbol": None,
            "selection_reasons": (),
        }
    )


def _with_selection(
    candidate: SetupCandidate,
    *,
    selected: bool,
    highlighted: bool,
    secondary: bool = False,
    primary_symbol: str | None = None,
    reasons: tuple[str, ...] = (),
    penalties: tuple[ScorePenalty, ...] | None = None,
) -> SetupCandidate:
    penalties = candidate.penalties if penalties is None else penalties
    positive = _candidate_positive(candidate)
    total = max(Decimal(0), positive - sum((penalty.points for penalty in penalties), Decimal(0)))
    components = tuple(
        component.model_copy(
            update={
                "points": component.quality * component.weight,
            }
        )
        for component in candidate.components
    )
    return candidate.model_copy(
        update={
            "components": components,
            "penalties": penalties,
            "positive_score": positive,
            "total_score": total,
            "selected_for_plan": selected,
            "executive_highlight": highlighted,
            "secondary_alternative": secondary,
            "primary_symbol": primary_symbol,
            "selection_reasons": reasons,
        }
    )


def _threshold(regime: Regime) -> Decimal | None:
    if regime is Regime.PERMISSIVE:
        return Decimal(70)
    if regime is Regime.NEUTRAL:
        return Decimal(80)
    return None


def _correlation_pair_key(left: str, right: str) -> tuple[str, str]:
    first, second = sorted((left, right))
    return first, second


def _correlation_lookup(
    correlations: tuple[CorrelationEvidence, ...],
) -> dict[tuple[str, str], CorrelationEvidence]:
    result: dict[tuple[str, str], CorrelationEvidence] = {}
    for correlation in correlations:
        if correlation.left_symbol == correlation.right_symbol:
            raise ValueError("correlation pair must contain distinct symbols")
        pair_key = _correlation_pair_key(correlation.left_symbol, correlation.right_symbol)
        if pair_key in result:
            raise ValueError("duplicate unordered correlation pair")
        result[pair_key] = correlation
    return result


def _correlation_group_assignments(
    candidates: tuple[SetupCandidate, ...],
    correlations: dict[tuple[str, str], CorrelationEvidence],
) -> tuple[dict[str, str], dict[str, CorrelationEvidence]]:
    """Resolve correlated symbols into groups rooted at the pre-penalty leader."""

    symbol_rank: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        symbol_rank.setdefault(candidate.symbol, index)
    adjacency: dict[str, list[tuple[str, CorrelationEvidence]]] = {
        symbol: [] for symbol in symbol_rank
    }
    for (left_symbol, right_symbol), correlation in correlations.items():
        if (
            left_symbol not in adjacency
            or right_symbol not in adjacency
            or abs(correlation.coefficient) < CORRELATION_THRESHOLD
        ):
            continue
        adjacency[left_symbol].append((right_symbol, correlation))
        adjacency[right_symbol].append((left_symbol, correlation))

    primary_by_symbol: dict[str, str] = {}
    connecting_evidence: dict[str, CorrelationEvidence] = {}
    for candidate in candidates:
        primary_symbol = candidate.symbol
        if primary_symbol in primary_by_symbol:
            continue
        primary_by_symbol[primary_symbol] = primary_symbol
        pending = [primary_symbol]
        while pending:
            current_symbol = pending.pop(0)
            for connected_symbol, correlation in sorted(
                adjacency[current_symbol],
                key=lambda item: (symbol_rank[item[0]], item[0]),
            ):
                if connected_symbol in primary_by_symbol:
                    continue
                primary_by_symbol[connected_symbol] = primary_symbol
                connecting_evidence[connected_symbol] = correlation
                pending.append(connected_symbol)
    return primary_by_symbol, connecting_evidence


def _correlation_penalties(
    candidate: SetupCandidate, correlation: CorrelationEvidence | None
) -> tuple[ScorePenalty, ...]:
    penalties = list(candidate.penalties)
    evidence_ids = () if correlation is None else correlation.evidence_ids
    reason = (
        "Duplicate symbol exposure" if correlation is None else correlation.exposure_description
    )
    penalties[2] = penalties[2].model_copy(
        update={
            "points": Decimal(10),
            "reason": reason,
            "evidence_ids": evidence_ids,
        }
    )
    return tuple(penalties)


def _rank_candidates(
    candidates: Sequence[SetupCandidate],
    regime: RegimeResult,
    regime_policy: RegimePolicy,
    *,
    correlations: Sequence[CorrelationEvidence] = (),
) -> tuple[SetupCandidate, ...]:
    """Return every scored candidate, ordered deterministically with selection caps."""

    if regime.policy_version != regime_policy.version:
        raise ValueError("regime result and policy versions must match")
    if any(candidate.regime_policy_version != regime_policy.version for candidate in candidates):
        raise ValueError("candidate regime policy versions must match the regime policy")
    correlation_values = tuple(correlations)
    for correlation in correlation_values:
        if any(
            candidate.symbol in {correlation.left_symbol, correlation.right_symbol}
            and correlation.observed_at > candidate.evidence_cutoff_at
            for candidate in candidates
        ):
            raise ValueError("correlation evidence must not be after candidate cutoff")
    sorted_candidates = _sorted(
        tuple(_without_correlation_penalty(candidate) for candidate in candidates)
    )
    threshold = _threshold(regime.regime)
    correlation_lookup = _correlation_lookup(correlation_values)
    primary_by_symbol, connecting_evidence = _correlation_group_assignments(
        sorted_candidates, correlation_lookup
    )
    prepared: list[tuple[SetupCandidate, bool, str | None]] = []
    seen_group_primaries: set[str] = set()
    for candidate in sorted_candidates:
        group_primary = primary_by_symbol[candidate.symbol]
        if candidate.symbol == group_primary and group_primary not in seen_group_primaries:
            seen_group_primaries.add(group_primary)
            prepared.append((candidate, False, None))
        else:
            duplicate_correlation = connecting_evidence.get(candidate.symbol)
            penalized = _correlation_penalties(candidate, duplicate_correlation)
            prepared.append(
                (
                    candidate.model_copy(
                        update={
                            "penalties": penalized,
                            "total_score": _candidate_total(
                                candidate.model_copy(update={"penalties": penalized})
                            ),
                        }
                    ),
                    True,
                    group_primary,
                )
            )

    selected_count = 0
    highlight_count = 0
    output: list[SetupCandidate] = []
    for candidate, secondary, primary_symbol in sorted(
        prepared, key=lambda item: _sort_key(item[0])
    ):
        reasons: list[str] = []
        if threshold is None:
            reasons.append("REGIME_BLOCK")
        elif _candidate_total(candidate) < threshold:
            reasons.append("SCORE_BELOW_THRESHOLD")
        if secondary:
            reasons.append("DUPLICATE_EXPOSURE")
        selected = False
        highlighted = False
        if not reasons:
            if selected_count < PLAN_CAP:
                selected = True
                selected_count += 1
                if highlight_count < HIGHLIGHT_CAP:
                    highlighted = True
                    highlight_count += 1
            else:
                reasons.append("PLAN_CAP")
        output.append(
            _with_selection(
                candidate,
                selected=selected,
                highlighted=highlighted,
                secondary=secondary,
                primary_symbol=primary_symbol,
                reasons=tuple(dict.fromkeys(reasons)),
            )
        )
    return tuple(output)


def rank_candidates(
    candidates: Sequence[SetupCandidate],
    regime: RegimeResult,
    regime_policy: RegimePolicy,
    *,
    correlations: Sequence[CorrelationEvidence] = (),
) -> tuple[SetupCandidate, ...]:
    """Rank candidates using the versioned numeric context, independent of callers."""

    with localcontext(NUMERIC_CONTEXT):
        return _rank_candidates(
            candidates,
            regime,
            regime_policy,
            correlations=correlations,
        )
