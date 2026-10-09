"""Strict source-scoped evaluation scenario contracts."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from finance_research_agent.domain.enums import (
    Capability,
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    GateStatus,
    ObservationOutcome,
    PlanStatus,
    ValidationCode,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    CapabilityState,
    Identifier,
    Sha256,
    StrictModel,
    Symbol,
)
from finance_research_agent.domain.regime import Regime
from finance_research_agent.domain.setups import SetupType
from finance_research_agent.domain.sizing import SizingStatus
from finance_research_agent.domain.types import FrozenMap

ScenarioErrorCode = ErrorCode | ValidationCode


class ScenarioId(StrEnum):
    S01 = "S01"
    S02 = "S02"
    S03 = "S03"
    S04 = "S04"
    S05 = "S05"
    S06 = "S06"
    S07 = "S07"
    S08 = "S08"
    S09 = "S09"
    S10 = "S10"
    S11 = "S11"
    S12 = "S12"
    S13 = "S13"
    S14 = "S14"
    S15 = "S15"
    S16 = "S16"
    S17 = "S17"
    S18 = "S18"
    S19 = "S19"
    S20 = "S20"
    S21 = "S21"
    S22 = "S22"
    S23 = "S23"
    S24 = "S24"
    S25 = "S25"


SCENARIO_IDS: tuple[ScenarioId, ...] = tuple(ScenarioId)


class FailureInjectionId(StrEnum):
    F01 = "F01"
    F02 = "F02"
    F03 = "F03"
    F04 = "F04"
    F05 = "F05"
    F06 = "F06"
    F07 = "F07"
    F08 = "F08"
    F09 = "F09"
    F10 = "F10"
    F11 = "F11"
    F12 = "F12"
    F13 = "F13"
    F14 = "F14"
    F15 = "F15"
    F16 = "F16"


class FixtureSetId(StrEnum):
    S01_BREAKOUT = "S01_BREAKOUT"
    S02_PULLBACK = "S02_PULLBACK"
    S03_NEUTRAL_THRESHOLD = "S03_NEUTRAL_THRESHOLD"
    S04_DEFENSIVE_REGIME = "S04_DEFENSIVE_REGIME"
    S05_UNKNOWN_REGIME = "S05_UNKNOWN_REGIME"
    S06_NO_ELIGIBLE_NAMES = "S06_NO_ELIGIBLE_NAMES"
    S07_EARNINGS_WINDOW = "S07_EARNINGS_WINDOW"
    S08_MATERIAL_REVISION = "S08_MATERIAL_REVISION"
    S09_SOURCE_CONFLICT = "S09_SOURCE_CONFLICT"
    S10_STALE_PREMARKET_QUOTE = "S10_STALE_PREMARKET_QUOTE"
    S11_IEX_LIMITATION = "S11_IEX_LIMITATION"
    S12_MISSING_MACRO_CALENDAR = "S12_MISSING_MACRO_CALENDAR"
    S13_INSUFFICIENT_HISTORY = "S13_INSUFFICIENT_HISTORY"
    S14_LEVERAGED_ETF = "S14_LEVERAGED_ETF"
    S15_HALTED_OR_UNCERTAIN_IDENTITY = "S15_HALTED_OR_UNCERTAIN_IDENTITY"
    S16_EXCEEDED_ENTRY_ZONE = "S16_EXCEEDED_ENTRY_ZONE"
    S17_INVALID_STOP = "S17_INVALID_STOP"
    S18_MISSING_CAPITAL = "S18_MISSING_CAPITAL"
    S19_MISSING_PORTFOLIO_HEAT = "S19_MISSING_PORTFOLIO_HEAT"
    S20_CORRELATED_CANDIDATES = "S20_CORRELATED_CANDIDATES"
    S21_AMBIGUOUS_DAILY_BAR = "S21_AMBIGUOUS_DAILY_BAR"
    S22_UNSUPPORTED_NUMERIC_CLAIM = "S22_UNSUPPORTED_NUMERIC_CLAIM"
    S23_IRRELEVANT_CITATION = "S23_IRRELEVANT_CITATION"
    S24_UNTRUSTED_EXCERPT = "S24_UNTRUSTED_EXCERPT"
    S25_MISSED_WINDOW = "S25_MISSED_WINDOW"


class DomainAssertionKind(StrEnum):
    REGIME_CALCULATION = "REGIME_CALCULATION"
    INSTRUMENT_ELIGIBILITY = "INSTRUMENT_ELIGIBILITY"
    SETUP_DETECTION = "SETUP_DETECTION"
    CANDIDATE_SCORE = "CANDIDATE_SCORE"
    CANDIDATE_RANKING = "CANDIDATE_RANKING"
    EVENT_RISK = "EVENT_RISK"
    PLAN_BUILD = "PLAN_BUILD"
    PLAN_EXPIRY = "PLAN_EXPIRY"
    POSITION_SIZING = "POSITION_SIZING"
    PRIOR_OBSERVATION = "PRIOR_OBSERVATION"
    RUN_WINDOW = "RUN_WINDOW"


class ScenarioAssertionId(StrEnum):
    CURRENT_SCOPE_MATCHES = "CURRENT_SCOPE_MATCHES"
    DOMAIN_ASSERTIONS_PASS = "DOMAIN_ASSERTIONS_PASS"
    ALL_WATCHLIST_EXCLUSIONS_VISIBLE = "ALL_WATCHLIST_EXCLUSIONS_VISIBLE"
    SOURCE_LIMITATIONS_ADJACENT = "SOURCE_LIMITATIONS_ADJACENT"
    PACKET_HASH_STABLE = "PACKET_HASH_STABLE"
    REVISION_BYTES_IMMUTABLE = "REVISION_BYTES_IMMUTABLE"
    INVALID_DRAFT_NEVER_PUBLISHED = "INVALID_DRAFT_NEVER_PUBLISHED"
    REPAIR_LIMIT_ENFORCED = "REPAIR_LIMIT_ENFORCED"
    UNTRUSTED_EXCERPT_INERT = "UNTRUSTED_EXCERPT_INERT"
    NO_PROVIDER_READ_AFTER_WINDOW = "NO_PROVIDER_READ_AFTER_WINDOW"
    MISSED_RUN_RECORD_DURABLE = "MISSED_RUN_RECORD_DURABLE"


class _ExpectedProjection(StrictModel):
    """A closed projection of actual fields returned by one domain operation."""

    @model_validator(mode="after")
    def _at_least_one_expected_field(self) -> _ExpectedProjection:
        if not self.model_fields_set - {"schema_version"}:
            raise ValueError("domain assertion requires an expected output field")
        return self


class RegimeCalculationExpected(_ExpectedProjection):
    regime: Regime | None = None
    score: Annotated[Decimal, Field(allow_inf_nan=False)] | None = None
    unavailable_reasons: tuple[Identifier, ...] | None = None


class InstrumentEligibilityExpected(_ExpectedProjection):
    gate_statuses: tuple[GateStatus, ...] | None = None
    reason_codes: tuple[Identifier, ...] | None = None


class SetupDetectionExpected(_ExpectedProjection):
    setup_types: tuple[SetupType, ...] | None = None


class CandidateScoreExpected(_ExpectedProjection):
    setup_type: SetupType | None = None
    total_score: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)] | None = None
    plan_status: PlanStatus | None = None


class CandidateRankingExpected(_ExpectedProjection):
    ranked_symbols: tuple[Symbol, ...] | None = None
    selection_reasons: tuple[tuple[Identifier, ...], ...] | None = None
    selected_for_plan: tuple[bool, ...] | None = None
    secondary_alternative: tuple[bool, ...] | None = None
    primary_symbols: tuple[Symbol | None, ...] | None = None


class EventRiskExpected(_ExpectedProjection):
    plan_status: PlanStatus | None = None
    gate_reason_codes: tuple[ErrorCode, ...] | None = None
    quality_flags: tuple[Identifier, ...] | None = None


class PlanBuildExpected(_ExpectedProjection):
    plan_status: PlanStatus | None = None
    data_quality_flags: tuple[Identifier, ...] | None = None


class PlanExpiryExpected(_ExpectedProjection):
    plan_status: PlanStatus | None = None
    expiry_reasons: tuple[Identifier, ...] | None = None


class PositionSizingExpected(_ExpectedProjection):
    status: SizingStatus | None = None
    unavailable_reasons: tuple[Identifier, ...] | None = None
    suggested_units: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)] | None = None


class PriorObservationExpected(_ExpectedProjection):
    outcomes: tuple[ObservationOutcome, ...] | None = None


class RunWindowExpected(_ExpectedProjection):
    should_run: bool | None = None
    delivery_status: DeliveryStatus | None = None
    allow_normal_plan: bool | None = None
    force_review_required: bool | None = None
    publish_missed_report: bool | None = None
    missed_record_only: bool | None = None
    reason_code: Identifier | None = None


class RegimeCalculationAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.REGIME_CALCULATION]
    fixture_id: Literal[
        FixtureSetId.S04_DEFENSIVE_REGIME,
        FixtureSetId.S05_UNKNOWN_REGIME,
    ]
    expected_fields: RegimeCalculationExpected


class InstrumentEligibilityAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.INSTRUMENT_ELIGIBILITY]
    fixture_id: Literal[
        FixtureSetId.S06_NO_ELIGIBLE_NAMES,
        FixtureSetId.S13_INSUFFICIENT_HISTORY,
        FixtureSetId.S14_LEVERAGED_ETF,
        FixtureSetId.S15_HALTED_OR_UNCERTAIN_IDENTITY,
    ]
    expected_fields: InstrumentEligibilityExpected


class SetupDetectionAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.SETUP_DETECTION]
    fixture_id: Literal[FixtureSetId.S02_PULLBACK, FixtureSetId.S11_IEX_LIMITATION]
    expected_fields: SetupDetectionExpected


class CandidateScoreAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.CANDIDATE_SCORE]
    fixture_id: Literal[FixtureSetId.S01_BREAKOUT]
    expected_fields: CandidateScoreExpected


class CandidateRankingAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.CANDIDATE_RANKING]
    fixture_id: Literal[
        FixtureSetId.S03_NEUTRAL_THRESHOLD,
        FixtureSetId.S20_CORRELATED_CANDIDATES,
    ]
    expected_fields: CandidateRankingExpected


class EventRiskAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.EVENT_RISK]
    fixture_id: Literal[
        FixtureSetId.S07_EARNINGS_WINDOW,
        FixtureSetId.S09_SOURCE_CONFLICT,
        FixtureSetId.S12_MISSING_MACRO_CALENDAR,
    ]
    expected_fields: EventRiskExpected


class PlanBuildAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.PLAN_BUILD]
    fixture_id: Literal[FixtureSetId.S19_MISSING_PORTFOLIO_HEAT]
    expected_fields: PlanBuildExpected


class PlanExpiryAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.PLAN_EXPIRY]
    fixture_id: Literal[
        FixtureSetId.S08_MATERIAL_REVISION,
        FixtureSetId.S16_EXCEEDED_ENTRY_ZONE,
    ]
    expected_fields: PlanExpiryExpected


class PositionSizingAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.POSITION_SIZING]
    fixture_id: Literal[
        FixtureSetId.S10_STALE_PREMARKET_QUOTE,
        FixtureSetId.S17_INVALID_STOP,
        FixtureSetId.S18_MISSING_CAPITAL,
    ]
    expected_fields: PositionSizingExpected


class PriorObservationAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.PRIOR_OBSERVATION]
    fixture_id: Literal[FixtureSetId.S21_AMBIGUOUS_DAILY_BAR]
    expected_fields: PriorObservationExpected


class RunWindowAssertion(StrictModel):
    kind: Literal[DomainAssertionKind.RUN_WINDOW]
    fixture_id: Literal[FixtureSetId.S25_MISSED_WINDOW]
    expected_fields: RunWindowExpected


type DomainAssertion = Annotated[
    RegimeCalculationAssertion
    | InstrumentEligibilityAssertion
    | SetupDetectionAssertion
    | CandidateScoreAssertion
    | CandidateRankingAssertion
    | EventRiskAssertion
    | PlanBuildAssertion
    | PlanExpiryAssertion
    | PositionSizingAssertion
    | PriorObservationAssertion
    | RunWindowAssertion,
    Field(discriminator="kind"),
]


class ScenarioOutcomeExpectation(StrictModel):
    """Exact service outcome for one primary or explicitly named subcase."""

    case_id: Identifier
    execution_status: ExecutionStatus
    data_quality_status: DataQualityStatus
    delivery_status: DeliveryStatus
    capabilities: tuple[CapabilityState, ...]
    plan_states: tuple[PlanStatus, ...]
    report_banner: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
    error_codes: tuple[ScenarioErrorCode, ...]
    recoverability: bool

    @model_validator(mode="after")
    def _exact_capabilities(self) -> ScenarioOutcomeExpectation:
        if self.execution_status is ExecutionStatus.SKIPPED:
            if self.capabilities:
                raise ValueError("skipped outcome cannot contain computed capabilities")
            return self
        _require_exact_capabilities(self.capabilities)
        return self


class CurrentScopeExpectation(StrictModel):
    """Current-source operational expectation, separate from Task 18 references."""

    primary: ScenarioOutcomeExpectation
    subcases: tuple[ScenarioOutcomeExpectation, ...]

    @model_validator(mode="after")
    def _unique_case_ids(self) -> CurrentScopeExpectation:
        case_ids = (self.primary.case_id, *(case.case_id for case in self.subcases))
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("current-scope expectation case ids must be unique")
        return self


class EvaluationScenario(StrictModel):
    """One immutable scenario with legacy and current-source expectations."""

    id: ScenarioId
    title: Annotated[str, Field(min_length=1, max_length=160, pattern=r"\S")]
    fixture_set: FixtureSetId
    injected_failures: tuple[FailureInjectionId, ...]
    expected_execution_status: ExecutionStatus
    expected_data_quality_status: DataQualityStatus
    expected_delivery_status: DeliveryStatus
    expected_capabilities: tuple[CapabilityState, ...]
    expected_plan_states: tuple[PlanStatus, ...]
    expected_report_banner: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
    expected_error_codes: tuple[ScenarioErrorCode, ...]
    expected_recoverability: bool
    assertions: tuple[ScenarioAssertionId, ...]
    original_scope_reference: Annotated[str, Field(min_length=1, max_length=160, pattern=r"\S")]
    current_scope_expectation: CurrentScopeExpectation
    domain_assertions: tuple[DomainAssertion, ...]

    @model_validator(mode="after")
    def _scenario_relations(self) -> EvaluationScenario:
        _require_exact_capabilities(self.expected_capabilities)
        if any(
            assertion.fixture_id is not self.fixture_set
            for assertion in self.domain_assertions
        ):
            raise ValueError("domain assertion fixture_id must match scenario fixture_set")
        if len(set(self.injected_failures)) != len(self.injected_failures):
            raise ValueError("injected failure ids must be unique")
        if len(set(self.assertions)) != len(self.assertions):
            raise ValueError("scenario assertion ids must be unique")
        return self


class CurrentScopeServiceObservation(StrictModel):
    """Actual current-scope service output and immutable publication evidence."""

    scenario_id: ScenarioId
    current_scope: ScenarioOutcomeExpectation
    artifact_hashes: FrozenMap[Identifier, Sha256]
    replay_json_matches: bool
    replay_markdown_matches: bool


class DomainAssertionOutcome(StrictModel):
    """Typed result of executing one named assertion against a domain fixture."""

    kind: DomainAssertionKind
    fixture_id: FixtureSetId
    status: Literal["PASS", "FAIL"]
    matched_fields: tuple[Identifier, ...]
    mismatched_fields: tuple[Identifier, ...]

    @model_validator(mode="after")
    def _coherent_field_results(self) -> DomainAssertionOutcome:
        if len(set(self.matched_fields)) != len(self.matched_fields):
            raise ValueError("matched field names must be unique")
        if len(set(self.mismatched_fields)) != len(self.mismatched_fields):
            raise ValueError("mismatched field names must be unique")
        if set(self.matched_fields) & set(self.mismatched_fields):
            raise ValueError("a field cannot both match and mismatch")
        if self.status == "PASS" and (not self.matched_fields or self.mismatched_fields):
            raise ValueError("PASS requires matching fields and no mismatches")
        if self.status == "FAIL" and not self.mismatched_fields:
            raise ValueError("FAIL requires at least one mismatched field")
        return self


def _require_exact_capabilities(capabilities: Sequence[CapabilityState]) -> None:
    actual = tuple(state.capability for state in capabilities)
    expected = tuple(Capability)
    if actual != expected:
        raise ValueError("capabilities must contain the exact ordered capability tuple")
