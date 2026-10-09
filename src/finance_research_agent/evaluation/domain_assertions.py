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
    SourceHealth,
)
from finance_research_agent.domain.policies import SetupPolicy, WatchlistItem
from finance_research_agent.domain.quality import DataQualityResult
from finance_research_agent.domain.regime import RegimePolicy, calculate_regime
from finance_research_agent.domain.scoring import score_candidate
from finance_research_agent.domain.setups import RawSetup, detect_setups
from finance_research_agent.domain.types import UtcDatetime
from finance_research_agent.evaluation.models import (
    CandidateScoreAssertion,
    DomainAssertion,
    DomainAssertionOutcome,
    EventRiskAssertion,
    FixtureSetId,
    InstrumentEligibilityAssertion,
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
