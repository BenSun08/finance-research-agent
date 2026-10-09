"""Closed evaluation calls into deterministic domain operations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType

from finance_research_agent.domain.eligibility import evaluate_instrument_eligibility
from finance_research_agent.domain.enums import InvocationType, PlanStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.events import (
    EventAssessment,
    EventEvidenceProjection,
    assess_event_risk,
)
from finance_research_agent.domain.market import RegimeMarketSnapshot
from finance_research_agent.domain.market_calendar import TradingCalendar, resolve_run_window
from finance_research_agent.domain.models import (
    EventRecord,
    GateResult,
    InstrumentIdentity,
    MarketSnapshot,
    PriceObservation,
    SourceHealth,
)
from finance_research_agent.domain.plans import TradePlanDraft, expire_plan
from finance_research_agent.domain.policies import RiskPolicy, SetupPolicy, WatchlistItem
from finance_research_agent.domain.quality import DataQualityResult
from finance_research_agent.domain.regime import (
    Regime,
    RegimePolicy,
    RegimeResult,
    calculate_regime,
)
from finance_research_agent.domain.scoring import (
    CorrelationEvidence,
    SetupCandidate,
    rank_candidates,
    score_candidate,
)
from finance_research_agent.domain.setups import RawSetup, detect_setups
from finance_research_agent.domain.sizing import calculate_position_sizing
from finance_research_agent.domain.types import UtcDatetime
from finance_research_agent.evaluation.models import (
    CandidateRankingAssertion,
    CandidateScoreAssertion,
    DomainAssertion,
    DomainAssertionOutcome,
    EventRiskAssertion,
    FixtureSetId,
    InstrumentEligibilityAssertion,
    PlanExpiryAssertion,
    PositionSizingAssertion,
    RegimeCalculationAssertion,
    RunWindowAssertion,
    SetupDetectionAssertion,
)


@dataclass(frozen=True, slots=True)
class RegimeCalculationFixture:
    """Complete immutable inputs for the approved calculate_regime operation."""

    snapshots: Mapping[str, RegimeMarketSnapshot]
    policy: RegimePolicy
    cutoff_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshots", MappingProxyType(dict(self.snapshots)))


@dataclass(frozen=True, slots=True)
class EventRiskFixture:
    """Complete immutable inputs for the approved assess_event_risk operation."""

    instrument: InstrumentIdentity
    events: tuple[EventRecord, ...]
    plan_expires_at: UtcDatetime
    evidence_cutoff_at: UtcDatetime
    source_health: tuple[SourceHealth, ...]
    event_evidence: tuple[EventEvidenceProjection, ...]
    unverified_material_status: PlanStatus


@dataclass(frozen=True, slots=True)
class RunWindowFixture:
    """Complete inputs for the approved local-calendar run-window operation."""

    now_utc: UtcDatetime
    calendar: TradingCalendar
    requested_market_date: date | None
    invocation: InvocationType


@dataclass(frozen=True, slots=True)
class InstrumentEligibilityFixture:
    """Complete inputs for one binary instrument-eligibility decision."""

    instrument: InstrumentIdentity
    watchlist_item: WatchlistItem
    snapshot: MarketSnapshot
    setup_policy: SetupPolicy
    direction: str
    halted: bool


@dataclass(frozen=True, slots=True)
class SetupDetectionFixture:
    """Complete inputs for deterministic setup detection."""

    snapshot: MarketSnapshot
    benchmark: MarketSnapshot | None
    sector_proxy: MarketSnapshot | None
    setup_policy: SetupPolicy
    evidence_cutoff_at: UtcDatetime
    eligibility_gates: tuple[GateResult, ...]
    event_assessment: EventAssessment
    data_quality: DataQualityResult


@dataclass(frozen=True, slots=True)
class CandidateScoreFixture:
    """Complete inputs for deterministic candidate scoring."""

    setup: RawSetup
    setup_policy: SetupPolicy
    eligibility_gates: tuple[GateResult, ...]
    event_assessment: EventAssessment
    data_quality: DataQualityResult
    regime_policy_version: str


@dataclass(frozen=True, slots=True)
class CandidateRankingFixture:
    """Complete deterministic inputs for candidate ordering and selection."""

    candidates: tuple[SetupCandidate, ...]
    regime: RegimeResult
    regime_policy: RegimePolicy
    correlations: tuple[CorrelationEvidence, ...]


@dataclass(frozen=True, slots=True)
class PlanExpiryFixture:
    """Complete explicit inputs for one plan-expiry decision."""

    plan: TradePlanDraft
    now_utc: UtcDatetime
    current_price: PriceObservation | None
    entry_trigger_satisfied: bool
    invalidation_observed: bool
    new_material_information: bool
    earnings_blackout: bool
    incompatible_regime: bool
    stale_or_conflicting_data: bool
    eligibility_changed: bool


@dataclass(frozen=True, slots=True)
class PositionSizingFixture:
    """Complete inputs for the deterministic, non-executing sizing calculation."""

    plan: TradePlanDraft
    risk_policy: RiskPolicy
    regime: Regime
    current_price: PriceObservation | None
    now_utc: UtcDatetime


@dataclass(frozen=True, slots=True)
class DomainFixtureBank:
    """Closed, caller-supplied typed fixture records; manifest values are never inputs."""

    records: Mapping[FixtureSetId, object]

    def __post_init__(self) -> None:
        copied = dict(self.records)
        if any(not isinstance(fixture_id, FixtureSetId) for fixture_id in copied):
            raise TypeError("domain fixture ids must use the closed FixtureSetId enum")
        object.__setattr__(self, "records", MappingProxyType(copied))

    def get(self, fixture_id: FixtureSetId) -> object:
        try:
            return self.records[fixture_id]
        except KeyError as error:
            raise ValueError(f"domain fixture {fixture_id.value} is missing") from error


def execute_domain_assertion(
    assertion: DomainAssertion,
    fixtures: DomainFixtureBank,
) -> DomainAssertionOutcome:
    """Execute one closed domain operation and compare its declared typed projection."""

    if isinstance(assertion, RegimeCalculationAssertion):
        fixture = fixtures.get(assertion.fixture_id)
        if type(fixture) is not RegimeCalculationFixture:
            raise TypeError("REGIME_CALCULATION requires RegimeCalculationFixture")
        regime_result = calculate_regime(
            fixture.snapshots, fixture.policy, fixture.cutoff_at
        )
        return _compare(
            assertion,
            {
                "regime": regime_result.regime,
                "score": regime_result.score,
                "unavailable_reasons": regime_result.unavailable_reasons,
            },
        )
    if isinstance(assertion, EventRiskAssertion):
        fixture = fixtures.get(assertion.fixture_id)
        if type(fixture) is not EventRiskFixture:
            raise TypeError("EVENT_RISK requires EventRiskFixture")
        event_result = assess_event_risk(
            instrument=fixture.instrument,
            events=fixture.events,
            plan_expires_at=fixture.plan_expires_at,
            evidence_cutoff_at=fixture.evidence_cutoff_at,
            source_health=fixture.source_health,
            event_evidence=fixture.event_evidence,
            unverified_material_status=fixture.unverified_material_status,
        )
        return _compare(
            assertion,
            {
                "plan_status": event_result.plan_status,
                "gate_reason_codes": tuple(
                    ErrorCode(gate.reason_code) for gate in event_result.gates
                ),
                "quality_flags": event_result.quality_flags,
            },
        )
    if isinstance(assertion, RunWindowAssertion):
        fixture = fixtures.get(assertion.fixture_id)
        if type(fixture) is not RunWindowFixture:
            raise TypeError("RUN_WINDOW requires RunWindowFixture")
        decision = resolve_run_window(
            fixture.now_utc,
            fixture.calendar,
            fixture.requested_market_date,
            fixture.invocation,
        )
        return _compare(
            assertion,
            {
                "should_run": decision.should_run,
                "delivery_status": decision.delivery_status,
                "allow_normal_plan": decision.allow_normal_plan,
                "force_review_required": decision.force_review_required,
                "publish_missed_report": decision.publish_missed_report,
                "missed_record_only": decision.missed_record_only,
                "reason_code": decision.reason_code,
            },
        )
    if isinstance(assertion, InstrumentEligibilityAssertion):
        fixture = fixtures.get(assertion.fixture_id)
        if type(fixture) is not InstrumentEligibilityFixture:
            raise TypeError("INSTRUMENT_ELIGIBILITY requires InstrumentEligibilityFixture")
        gates = evaluate_instrument_eligibility(
            instrument=fixture.instrument,
            watchlist_item=fixture.watchlist_item,
            snapshot=fixture.snapshot,
            setup_policy=fixture.setup_policy,
            direction=fixture.direction,
            halted=fixture.halted,
        )
        return _compare(
            assertion,
            {
                "gate_statuses": tuple(gate.status for gate in gates),
                "reason_codes": tuple(gate.reason_code for gate in gates),
            },
        )
    if isinstance(assertion, SetupDetectionAssertion):
        fixture = fixtures.get(assertion.fixture_id)
        if type(fixture) is not SetupDetectionFixture:
            raise TypeError("SETUP_DETECTION requires SetupDetectionFixture")
        setups = detect_setups(
            snapshot=fixture.snapshot,
            benchmark=fixture.benchmark,
            sector_proxy=fixture.sector_proxy,
            setup_policy=fixture.setup_policy,
            evidence_cutoff_at=fixture.evidence_cutoff_at,
            eligibility_gates=fixture.eligibility_gates,
            event_assessment=fixture.event_assessment,
            data_quality=fixture.data_quality,
        )
        return _compare(
            assertion,
            {"setup_types": tuple(setup.setup_type for setup in setups)},
        )
    if isinstance(assertion, CandidateScoreAssertion):
        fixture = fixtures.get(assertion.fixture_id)
        if type(fixture) is not CandidateScoreFixture:
            raise TypeError("CANDIDATE_SCORE requires CandidateScoreFixture")
        candidate = score_candidate(
            fixture.setup,
            setup_policy=fixture.setup_policy,
            eligibility_gates=fixture.eligibility_gates,
            event_assessment=fixture.event_assessment,
            data_quality=fixture.data_quality,
            regime_policy_version=fixture.regime_policy_version,
        )
        return _compare(
            assertion,
            {
                "setup_type": candidate.setup_type,
                "total_score": candidate.total_score,
                "plan_status": candidate.plan_status,
            },
        )
    if isinstance(assertion, CandidateRankingAssertion):
        fixture = fixtures.get(assertion.fixture_id)
        if type(fixture) is not CandidateRankingFixture:
            raise TypeError("CANDIDATE_RANKING requires CandidateRankingFixture")
        ranked = rank_candidates(
            fixture.candidates,
            fixture.regime,
            fixture.regime_policy,
            correlations=fixture.correlations,
        )
        return _compare(
            assertion,
            {
                "ranked_symbols": tuple(candidate.symbol for candidate in ranked),
                "selection_reasons": tuple(
                    candidate.selection_reasons for candidate in ranked
                ),
                "selected_for_plan": tuple(
                    candidate.selected_for_plan for candidate in ranked
                ),
                "secondary_alternative": tuple(
                    candidate.secondary_alternative for candidate in ranked
                ),
                "primary_symbols": tuple(candidate.primary_symbol for candidate in ranked),
            },
        )
    if isinstance(assertion, PlanExpiryAssertion):
        fixture = fixtures.get(assertion.fixture_id)
        if type(fixture) is not PlanExpiryFixture:
            raise TypeError("PLAN_EXPIRY requires PlanExpiryFixture")
        expired = expire_plan(
            fixture.plan,
            now_utc=fixture.now_utc,
            current_price=fixture.current_price,
            entry_trigger_satisfied=fixture.entry_trigger_satisfied,
            invalidation_observed=fixture.invalidation_observed,
            new_material_information=fixture.new_material_information,
            earnings_blackout=fixture.earnings_blackout,
            incompatible_regime=fixture.incompatible_regime,
            stale_or_conflicting_data=fixture.stale_or_conflicting_data,
            eligibility_changed=fixture.eligibility_changed,
        )
        return _compare(
            assertion,
            {
                "plan_status": expired.plan_status,
                "expiry_reasons": expired.expiry_reasons,
            },
        )
    if isinstance(assertion, PositionSizingAssertion):
        fixture = fixtures.get(assertion.fixture_id)
        if type(fixture) is not PositionSizingFixture:
            raise TypeError("POSITION_SIZING requires PositionSizingFixture")
        sizing = calculate_position_sizing(
            fixture.plan,
            fixture.risk_policy,
            fixture.regime,
            fixture.current_price,
            fixture.now_utc,
        )
        return _compare(
            assertion,
            {
                "status": sizing.status,
                "unavailable_reasons": sizing.unavailable_reasons,
                "suggested_units": sizing.suggested_units,
            },
        )
    raise NotImplementedError(
        f"domain assertion operation {assertion.kind.value} is not registered"
    )


def _compare(
    assertion: DomainAssertion,
    observed_values: Mapping[str, object],
) -> DomainAssertionOutcome:
    expected = assertion.expected_fields
    fields = tuple(
        name
        for name in type(expected).model_fields
        if name != "schema_version" and name in expected.model_fields_set
    )
    matched: list[str] = []
    mismatched: list[str] = []
    for name in fields:
        if name not in observed_values:
            raise ValueError(f"domain output projection does not define field {name!r}")
        if observed_values[name] == getattr(expected, name):
            matched.append(name)
        else:
            mismatched.append(name)
    return DomainAssertionOutcome(
        kind=assertion.kind,
        fixture_id=assertion.fixture_id,
        status="FAIL" if mismatched else "PASS",
        matched_fields=tuple(matched),
        mismatched_fields=tuple(mismatched),
    )
