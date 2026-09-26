"""Structured Product A synthesis drafts and deterministic validation."""

import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Annotated, Self

from pydantic import Field, model_validator

from finance_research_agent.domain.enums import (
    BriefOrigin,
    Capability,
    ClaimType,
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    PlanStatus,
    ReportSection,
    ValidationCode,
    ValidationSeverity,
)
from finance_research_agent.domain.metrics import MetricResult
from finance_research_agent.domain.models import (
    EvidenceItem,
    Identifier,
    Sha256,
    StrictModel,
    Symbol,
)
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.plans import TradePlanDraft
from finance_research_agent.domain.types import FrozenMap, UtcDatetime

_EXECUTIVE_SECTIONS = (
    ReportSection.RUN_STATUS,
    ReportSection.MARKET_POSTURE,
    ReportSection.WHAT_CHANGED,
    ReportSection.TODAY_EVENT_CLOCK,
    ReportSection.CORE_MARKET_RISKS,
    ReportSection.WATCHLIST_PRIORITIES,
    ReportSection.EXECUTIVE_TRADE_PLAN_DRAFTS,
    ReportSection.DATA_WARNINGS,
)
_DETAILED_SECTIONS = (
    ReportSection.MARKET_REGIME,
    ReportSection.MACRO_EVENT_CALENDAR,
    ReportSection.BROAD_MARKET_RADAR,
    ReportSection.SECTOR_ROTATION,
    ReportSection.CROSS_ASSET_RISK_SIGNALS,
    ReportSection.CORE_MONITOR,
    ReportSection.WATCHLIST_DASHBOARD,
    ReportSection.ELIGIBLE_SETUPS,
    ReportSection.DETAILED_TRADE_PLAN_DRAFTS,
    ReportSection.BLOCKED_EXCLUDED_CANDIDATES,
    ReportSection.CHANGES_SINCE_PRIOR_RUN,
    ReportSection.DATA_QUALITY_LIMITATIONS,
    ReportSection.EVIDENCE_INDEX,
    ReportSection.METHODOLOGY_RISK_NOTICE,
)
_IMPERATIVE = re.compile(
    r"\b(?:buy|sell|short|execute|place\s+(?:an?\s+)?order|enter\s+now|"
    r"exit\s+now|guaranteed\s+upside|safe\s+trade|approved|executed)\b",
    re.IGNORECASE,
)
_NUMERIC_TEXT = re.compile(r"(?<![A-Za-z])\$?-?\d+(?:\.\d+)?%?(?![A-Za-z])")
_URL_TEXT = re.compile(r"https?://\S+", re.IGNORECASE)
_FULL_MARKET_CLAIM = re.compile(
    r"\b(?:full[- ]market|consolidated\s+(?:market|volume|activity)|"
    r"all\s+exchanges|entire\s+market)\b",
    re.IGNORECASE,
)
_ACTIONABLE = re.compile(
    r"\b(?:actionable|ready\s+to\s+trade|eligible\s+to\s+trade)\b", re.IGNORECASE
)


class Claim(StrictModel):
    """One node in the structured, evidence-reachable brief claim graph."""

    claim_id: Identifier
    claim_type: ClaimType
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    subject_symbol: Symbol | None = None
    field: Identifier | None = None
    time_start: UtcDatetime | None = None
    time_end: UtcDatetime | None = None
    evidence_ids: tuple[Identifier, ...] = ()
    metric_ids: tuple[Identifier, ...] = ()
    counter_evidence_ids: tuple[Identifier, ...] = ()
    supports_claim_ids: tuple[Identifier, ...] = ()
    numeric_value: Decimal | None = None
    unit: Identifier | None = None
    invalidation: Annotated[str, Field(max_length=1000)] | None = None
    expires_at: UtcDatetime | None = None
    plan_id: Identifier | None = None
    plan_status: PlanStatus | None = None

    @model_validator(mode="after")
    def _unique_relations(self) -> Self:
        for values in (
            self.evidence_ids,
            self.metric_ids,
            self.counter_evidence_ids,
            self.supports_claim_ids,
        ):
            if len(set(values)) != len(values):
                raise ValueError("claim references must be unique")
        if self.time_start and self.time_end and self.time_start > self.time_end:
            raise ValueError("claim time_start must not follow time_end")
        return self


class ReportSectionClaims(StrictModel):
    """A required report section and its ordered claim references."""

    section: ReportSection
    claim_ids: tuple[Identifier, ...] = Field(max_length=500)


class PlanNarrative(StrictModel):
    """Narrative claims associated with one deterministic plan identifier."""

    plan_id: Identifier
    text: Annotated[str, Field(min_length=1, max_length=3000)]
    claim_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100)


class CapabilityExplanation(StrictModel):
    """Required user-facing reason for a disabled deterministic capability."""

    capability: Capability
    reason_codes: tuple[Identifier, ...] = Field(min_length=1, max_length=64)


class ResearchBriefDraft(StrictModel):
    """Structured synthesis output; it carries no runtime or provider provenance."""

    run_id: Identifier
    origin: BriefOrigin
    execution_status: ExecutionStatus
    data_quality_status: DataQualityStatus
    delivery_status: DeliveryStatus
    executive_sections: tuple[ReportSectionClaims, ...] = Field(max_length=8)
    detailed_sections: tuple[ReportSectionClaims, ...] = Field(max_length=14)
    claims: tuple[Claim, ...] = Field(max_length=500)
    plan_narratives: tuple[PlanNarrative, ...] = Field(max_length=32)
    disabled_capability_explanations: tuple[CapabilityExplanation, ...] = Field(max_length=32)
    data_warnings: tuple[Annotated[str, Field(max_length=1000)], ...] = Field(max_length=128)


class ValidationIssue(StrictModel):
    """Closed deterministic validator finding with bounded diagnostic content."""

    issue_id: Identifier
    code: ValidationCode
    severity: ValidationSeverity
    json_pointer: Annotated[str, Field(min_length=1, max_length=512)]
    message: Annotated[str, Field(min_length=1, max_length=1000)]
    expected_value: Annotated[str, Field(max_length=1000)] | None = None
    actual_value: Annotated[str, Field(max_length=1000)] | None = None
    related_evidence_ids: tuple[Identifier, ...] = Field(max_length=128)


class ValidationReport(StrictModel):
    """Attempt-specific result bound to the exact frozen packet identity and hash."""

    run_id: Identifier
    packet_id: Identifier
    packet_sha256: Sha256
    is_valid: bool
    repairable: bool
    validation_attempt: Annotated[int, Field(ge=1, le=3)]
    repair_attempts_used: Annotated[int, Field(ge=0, le=2)]
    issues: tuple[ValidationIssue, ...] = Field(max_length=2000)

    @model_validator(mode="after")
    def _consistent_result(self) -> Self:
        expected_valid = not any(
            issue.severity is ValidationSeverity.ERROR for issue in self.issues
        )
        if self.is_valid != expected_valid:
            raise ValueError("is_valid must match the absence of ERROR issues")
        if self.repair_attempts_used != self.validation_attempt - 1:
            raise ValueError("repair_attempts_used must equal validation_attempt minus one")
        if self.repairable != (not self.is_valid and self.validation_attempt < 3):
            raise ValueError("repairable must match validity and the bounded repair limit")
        return self


class RepairContext(StrictModel):
    """Core-owned bounded repair identity; no evidence or packet mutation is possible."""

    packet_id: Identifier
    packet_sha256: Sha256
    validation_attempt: Annotated[int, Field(ge=2, le=3)]


def create_repair_context(packet: ResearchPacket, report: ValidationReport) -> RepairContext:
    """Permit a next repair only for the identical invalid frozen packet, at most twice."""
    if report.packet_id != packet.packet_id or report.packet_sha256 != packet.canonical_sha256:
        raise ValueError("validation report does not match the frozen packet")
    if report.is_valid:
        raise ValueError("a valid draft does not require repair")
    if not report.repairable or report.validation_attempt >= 3:
        raise ValueError("at most two repair attempts are permitted")
    return RepairContext(
        packet_id=packet.packet_id,
        packet_sha256=packet.canonical_sha256,
        validation_attempt=report.validation_attempt + 1,
    )


def _issue(
    code: ValidationCode,
    pointer: str,
    message: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
    evidence_ids: tuple[str, ...] = (),
    severity: ValidationSeverity = ValidationSeverity.ERROR,
) -> ValidationIssue:
    identity = json.dumps(
        [code.value, pointer, message, expected, actual, evidence_ids],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ValidationIssue(
        issue_id=f"validation-{sha256(identity).hexdigest()[:24]}",
        code=code,
        severity=severity,
        json_pointer=pointer,
        message=message,
        expected_value=expected,
        actual_value=actual,
        related_evidence_ids=evidence_ids,
    )


def _claim_graph(claims: tuple[Claim, ...], issues: list[ValidationIssue]) -> dict[str, Claim]:
    by_id: dict[str, Claim] = {}
    for index, claim in enumerate(claims):
        if claim.claim_id in by_id:
            issues.append(
                _issue(
                    ValidationCode.CLAIM_REACHABILITY,
                    f"/claims/{index}/claim_id",
                    "duplicate claim id",
                )
            )
        else:
            by_id[claim.claim_id] = claim
    for index, claim in enumerate(claims):
        missing = tuple(ref for ref in claim.supports_claim_ids if ref not in by_id)
        if missing:
            issues.append(
                _issue(
                    ValidationCode.CLAIM_REACHABILITY,
                    f"/claims/{index}/supports_claim_ids",
                    "claim graph references missing claims",
                    actual=", ".join(missing),
                )
            )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(claim_id: str) -> None:
        if claim_id in visiting:
            issues.append(
                _issue(
                    ValidationCode.CLAIM_REACHABILITY,
                    f"/claims/{claim_id}/supports_claim_ids",
                    "claim graph contains a cycle",
                )
            )
            return
        if claim_id in visited or claim_id not in by_id:
            return
        visiting.add(claim_id)
        for support in by_id[claim_id].supports_claim_ids:
            visit(support)
        visiting.remove(claim_id)
        visited.add(claim_id)

    for claim_id in by_id:
        visit(claim_id)
    return by_id


def _reachable_sources(claim: Claim, by_id: dict[str, Claim]) -> set[str]:
    found = set(claim.evidence_ids)
    pending = list(claim.supports_claim_ids)
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen or current not in by_id:
            continue
        seen.add(current)
        supporting = by_id[current]
        found.update(supporting.evidence_ids)
        pending.extend(supporting.supports_claim_ids)
    return found


def _field_supported(claim: Claim, evidence: EvidenceItem) -> bool:
    if claim.field is None:
        return False
    fields = evidence.structured_fields
    return fields.get("field") == claim.field or claim.field in fields


def _evidence_relevant(claim: Claim, evidence: EvidenceItem) -> bool:
    if claim.subject_symbol is not None:
        subject = evidence.instrument_id or evidence.structured_fields.get("subject")
        if subject != claim.subject_symbol:
            return False
    if not _field_supported(claim, evidence):
        return False
    times = tuple(
        value for value in (evidence.event_time, evidence.published_time) if value is not None
    )
    if (claim.time_start or claim.time_end) and not times:
        return False
    if claim.time_start and all(value < claim.time_start for value in times):
        return False
    if claim.time_end and all(value > claim.time_end for value in times):
        return False
    return True


def _decimal_values(value: object) -> set[Decimal]:
    result: set[Decimal] = set()
    if isinstance(value, bool):
        return result
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
            if number.is_finite():
                result.add(number)
        except InvalidOperation:
            pass
    elif isinstance(value, str):
        for match in _NUMERIC_TEXT.finditer(value):
            try:
                result.add(Decimal(match.group().replace("$", "").removesuffix("%")))
            except InvalidOperation:
                continue
    elif isinstance(value, (tuple, list)):
        for item in value:
            result.update(_decimal_values(item))
    elif isinstance(value, dict) or isinstance(value, FrozenMap):
        for item in value.values():
            result.update(_decimal_values(item))
    return result


def _same_quantized_decimal(left: Decimal, right: Decimal) -> bool:
    return left == right and left.as_tuple().exponent == right.as_tuple().exponent


def _plan_numeric_binding(
    claim: Claim, plan: TradePlanDraft
) -> tuple[Decimal, str] | None:
    field = claim.field
    if field in {"position_size", "suggested_units"}:
        value = plan.position_sizing.suggested_units
        return (value, "shares") if value is not None else None
    if field == "candidate_score":
        return plan.candidate_score, "score"
    if field == "risk_per_unit":
        return plan.risk_per_unit, "price"
    if field in {"entry_zone_lower", "entry_zone.lower.value"}:
        return plan.entry_zone.lower.value, plan.entry_zone.lower.currency
    if field in {"entry_zone_upper", "entry_zone.upper.value"}:
        return plan.entry_zone.upper.value, plan.entry_zone.upper.currency
    if field in {"candidate_stop", "candidate_stop.value"}:
        return plan.candidate_stop.value, plan.candidate_stop.currency
    return None


def _numeric_bindings(
    claim: Claim,
    packet: ResearchPacket,
    metric_map: Mapping[str, MetricResult],
    plan_map: dict[str, TradePlanDraft],
    claim_map: dict[str, Claim],
    visited: set[str] | None = None,
    root_field: str | None = None,
    root_unit: str | None = None,
) -> set[tuple[Decimal, str]]:
    if visited is None:
        visited = set()
        root_field = claim.field
        root_unit = claim.unit
    if claim.claim_id in visited:
        return set()
    visited.add(claim.claim_id)
    if claim.field != root_field or claim.unit != root_unit:
        return set()

    bindings: set[tuple[Decimal, str]] = set()
    for evidence_id in _reachable_sources(claim, claim_map):
        item = next(
            (evidence for evidence in packet.evidence if evidence.evidence_id == evidence_id),
            None,
        )
        if item is None or claim.field is None:
            continue
        fields = item.structured_fields
        if fields.get("field") == claim.field:
            raw_value = fields.get("value")
            raw_unit = fields.get("unit")
        else:
            raw_value = fields.get(claim.field)
            raw_unit = fields.get(f"{claim.field}_unit")
        if raw_unit is None:
            continue
        for value in _decimal_values(raw_value):
            bindings.add((value, str(raw_unit)))
    for metric_id in claim.metric_ids:
        metric = metric_map.get(metric_id)
        if (
            metric is not None
            and metric.value is not None
            and claim.field == metric.name.value
        ):
            bindings.add((metric.value, metric.unit.value))
    if claim.plan_id in plan_map:
        binding = _plan_numeric_binding(claim, plan_map[claim.plan_id])
        if binding is not None:
            bindings.add(binding)
    for supporting_id in claim.supports_claim_ids:
        supporting = claim_map.get(supporting_id)
        if supporting is not None:
            bindings.update(
                _numeric_bindings(
                    supporting,
                    packet,
                    metric_map,
                    plan_map,
                    claim_map,
                    visited,
                    root_field,
                    root_unit,
                )
            )
    return bindings


def _validate_claims(
    packet: ResearchPacket, draft: ResearchBriefDraft, issues: list[ValidationIssue]
) -> None:
    evidence_map = {item.evidence_id: item for item in packet.evidence}
    metric_map = {metric.metric_id: metric for metric in packet.metrics}
    plan_map = {plan.plan_id: plan for plan in packet.deterministic_plan_inputs}
    claim_map = _claim_graph(draft.claims, issues)
    referenced: set[str] = set()
    for section in (*draft.executive_sections, *draft.detailed_sections):
        referenced.update(section.claim_ids)
    for narrative in draft.plan_narratives:
        referenced.update(narrative.claim_ids)
        supporting_claims = tuple(claim_map.get(item) for item in narrative.claim_ids)
        if any(claim is None for claim in supporting_claims):
            issues.append(
                _issue(
                    ValidationCode.CLAIM_REACHABILITY,
                    "/plan_narratives",
                    "plan narrative references a missing claim",
                    actual=narrative.plan_id,
                )
            )
        else:
            concrete_claims = tuple(claim for claim in supporting_claims if claim is not None)
            expected_text = " ".join(claim.text for claim in concrete_claims)
            if narrative.text != expected_text:
                issues.append(
                    _issue(
                        ValidationCode.UNSUPPORTED_CLAIM,
                        "/plan_narratives",
                        "plan narrative text must exactly repeat its validated claim text",
                        expected=expected_text,
                        actual=narrative.text,
                    )
                )
            if any(claim.plan_id != narrative.plan_id for claim in concrete_claims):
                issues.append(
                    _issue(
                        ValidationCode.UNSUPPORTED_CLAIM,
                        "/plan_narratives",
                        "plan narrative claims must refer to the same deterministic plan",
                        actual=narrative.plan_id,
                    )
                )
        if narrative.plan_id not in plan_map:
            issues.append(
                _issue(
                    ValidationCode.UNSUPPORTED_CLAIM,
                    "/plan_narratives",
                    "plan narrative refers to a plan outside the packet",
                    actual=narrative.plan_id,
                )
            )
        if _IMPERATIVE.search(narrative.text):
            issues.append(
                _issue(
                    ValidationCode.IMPERATIVE_TRADING_LANGUAGE,
                    "/plan_narratives/text",
                    "plan narrative contains prohibited imperative or execution language",
                )
            )
    packet_warnings = {
        gate.message for gate in packet.gates if gate.status.value in {"WARNING", "BLOCK"}
    }
    packet_warnings.update(claim.text for claim in draft.claims)
    for index, warning in enumerate(draft.data_warnings):
        if warning not in packet_warnings:
            issues.append(
                _issue(
                    ValidationCode.UNSUPPORTED_CLAIM,
                    f"/data_warnings/{index}",
                    "data warning must repeat a packet gate message or validated claim",
                    actual=warning,
                )
            )
        if _IMPERATIVE.search(warning):
            issues.append(
                _issue(
                    ValidationCode.IMPERATIVE_TRADING_LANGUAGE,
                    f"/data_warnings/{index}",
                    "data warning contains prohibited imperative or execution language",
                )
            )
    for claim in draft.claims:
        if claim.claim_id not in referenced:
            issues.append(
                _issue(
                    ValidationCode.CLAIM_REACHABILITY,
                    f"/claims/{claim.claim_id}",
                    "claim is unreachable from a report section",
                )
            )
        unknown_evidence = tuple(
            item
            for item in claim.evidence_ids + claim.counter_evidence_ids
            if item not in evidence_map
        )
        if unknown_evidence:
            issues.append(
                _issue(
                    ValidationCode.UNKNOWN_EVIDENCE,
                    f"/claims/{claim.claim_id}/evidence_ids",
                    "claim cites evidence outside the packet",
                    actual=", ".join(unknown_evidence),
                )
            )
        unknown_metrics = tuple(item for item in claim.metric_ids if item not in metric_map)
        if unknown_metrics:
            issues.append(
                _issue(
                    ValidationCode.UNKNOWN_METRIC,
                    f"/claims/{claim.claim_id}/metric_ids",
                    "claim cites metrics outside the packet",
                    actual=", ".join(unknown_metrics),
                )
            )
        if claim.claim_type is ClaimType.FACT and not claim.evidence_ids:
            issues.append(
                _issue(
                    ValidationCode.UNSUPPORTED_CLAIM,
                    f"/claims/{claim.claim_id}",
                    "factual claim requires evidence",
                )
            )
        if claim.claim_type is ClaimType.CALCULATION and not claim.metric_ids:
            issues.append(
                _issue(
                    ValidationCode.UNSUPPORTED_CLAIM,
                    f"/claims/{claim.claim_id}",
                    "calculated claim requires metric references",
                )
            )
        if claim.claim_type in {ClaimType.INFERENCE, ClaimType.HYPOTHESIS} and not (
            claim.supports_claim_ids or claim.evidence_ids or claim.metric_ids
        ):
            issues.append(
                _issue(
                    ValidationCode.UNSUPPORTED_CLAIM,
                    f"/claims/{claim.claim_id}",
                    "inference or hypothesis has no packet-supported basis",
                )
            )
        if claim.claim_type is ClaimType.HYPOTHESIS and (
            not claim.counter_evidence_ids or not claim.invalidation or not claim.expires_at
        ):
            issues.append(
                _issue(
                    ValidationCode.COUNTER_EVIDENCE_OMITTED,
                    f"/claims/{claim.claim_id}",
                    "hypothesis requires counter-evidence, invalidation, and expiry",
                )
            )
        if claim.plan_id and claim.plan_id not in plan_map:
            issues.append(
                _issue(
                    ValidationCode.UNSUPPORTED_CLAIM,
                    f"/claims/{claim.claim_id}/plan_id",
                    "claim refers to a plan outside the packet",
                )
            )
        plan = plan_map.get(claim.plan_id) if claim.plan_id else None
        if plan is not None:
            if claim.plan_status is not plan.plan_status:
                issues.append(
                    _issue(
                        ValidationCode.INVALID_PLAN_STATE,
                        f"/claims/{claim.claim_id}/plan_status",
                        "plan-linked claim must preserve the deterministic plan status",
                        expected=plan.plan_status.value,
                        actual=getattr(claim.plan_status, "value", str(claim.plan_status)),
                    )
                )
            if claim.counter_evidence_ids != plan.counter_evidence:
                issues.append(
                    _issue(
                        ValidationCode.COUNTER_EVIDENCE_OMITTED,
                        f"/claims/{claim.claim_id}/counter_evidence_ids",
                        "plan-linked claim must preserve the plan's counter-evidence IDs",
                        expected=", ".join(plan.counter_evidence),
                        actual=", ".join(claim.counter_evidence_ids),
                        evidence_ids=plan.counter_evidence,
                    )
                )
            expected_expiry = plan.effective_expiry_at or plan.expires_at
            if (
                claim.invalidation != plan.invalidation_condition
                or claim.expires_at != expected_expiry
            ):
                issues.append(
                    _issue(
                        ValidationCode.EXPIRY_INVALIDATION_MISSING,
                        f"/claims/{claim.claim_id}",
                        "plan-linked claim must preserve deterministic invalidation and expiry",
                        expected=f"{plan.invalidation_condition}; {expected_expiry.isoformat()}",
                        actual=f"{claim.invalidation}; {claim.expires_at}",
                    )
                )
            if claim.field and "siz" in claim.field.lower():
                units = plan.position_sizing.suggested_units
                if units is None or claim.numeric_value != units:
                    issues.append(
                        _issue(
                            ValidationCode.UNSUPPORTED_SIZING_VALUE,
                            f"/claims/{claim.claim_id}/numeric_value",
                            "draft sizing must exactly match available deterministic plan sizing",
                        )
                    )
        plan_status = plan.plan_status if plan is not None else claim.plan_status
        if plan_status in {PlanStatus.BLOCKED, PlanStatus.EXPIRED} and _ACTIONABLE.search(
            claim.text
        ):
            issues.append(
                _issue(
                    ValidationCode.BLOCKED_PLAN_OVERCLAIM,
                    f"/claims/{claim.claim_id}/text",
                    "blocked or expired plans cannot be described as actionable",
                )
            )
        if claim.claim_type is ClaimType.CALCULATION:
            required_inputs = {
                evidence_id
                for metric_id in claim.metric_ids
                if metric_id in metric_map
                for evidence_id in metric_map[metric_id].input_evidence_ids
            }
            if not required_inputs.issubset(_reachable_sources(claim, claim_map)):
                issues.append(
                    _issue(
                        ValidationCode.CLAIM_REACHABILITY,
                        f"/claims/{claim.claim_id}/evidence_ids",
                        "calculated claim omits metric input evidence",
                        actual=", ".join(sorted(required_inputs)),
                    )
                )
            for metric_id in claim.metric_ids:
                metric = metric_map.get(metric_id)
                if metric is not None and (
                    metric.value is None
                    or claim.numeric_value is None
                    or not _same_quantized_decimal(metric.value, claim.numeric_value)
                    or metric.unit.value != claim.unit
                ):
                    issues.append(
                        _issue(
                            ValidationCode.DETERMINISTIC_VALUE_MISMATCH,
                            f"/claims/{claim.claim_id}/numeric_value",
                            "calculated value or unit differs from the deterministic metric",
                            expected=f"{metric.value} {metric.unit.value}",
                            actual=f"{claim.numeric_value} {claim.unit}",
                        )
                    )
        for evidence_id in claim.evidence_ids:
            item = evidence_map.get(evidence_id)
            if (
                item is not None
                and claim.claim_type is not ClaimType.CALCULATION
                and not _evidence_relevant(claim, item)
            ):
                issues.append(
                    _issue(
                        ValidationCode.IRRELEVANT_CITATION,
                        f"/claims/{claim.claim_id}/evidence_ids",
                        "cited evidence does not match the claim subject, field, and time",
                        evidence_ids=(evidence_id,),
                    )
                )
            elif item is not None and claim.claim_type is not ClaimType.CALCULATION:
                relevant_packet_evidence = tuple(
                    candidate
                    for candidate in packet.evidence
                    if _evidence_relevant(claim, candidate)
                )
                if relevant_packet_evidence and item.authority_tier > min(
                    candidate.authority_tier for candidate in relevant_packet_evidence
                ):
                    issues.append(
                        _issue(
                            ValidationCode.IRRELEVANT_CITATION,
                            f"/claims/{claim.claim_id}/evidence_ids",
                            "claim cites lower-authority evidence while stronger "
                            "evidence is available",
                            expected=str(
                                min(
                                    candidate.authority_tier
                                    for candidate in relevant_packet_evidence
                                )
                            ),
                            actual=str(item.authority_tier),
                            evidence_ids=(evidence_id,),
                        )
                    )
        if claim.counter_evidence_ids:
            for evidence_id in claim.counter_evidence_ids:
                item = evidence_map.get(evidence_id)
                if (
                    item is not None
                    and claim.subject_symbol
                    and item.instrument_id not in {None, claim.subject_symbol}
                ):
                    issues.append(
                        _issue(
                            ValidationCode.IRRELEVANT_CITATION,
                            f"/claims/{claim.claim_id}/counter_evidence_ids",
                            "counter-evidence has a different subject",
                            evidence_ids=(evidence_id,),
                        )
                    )
        if _IMPERATIVE.search(claim.text):
            issues.append(
                _issue(
                    ValidationCode.IMPERATIVE_TRADING_LANGUAGE,
                    f"/claims/{claim.claim_id}/text",
                    "draft contains prohibited imperative or execution language",
                )
            )
        if _FULL_MARKET_CLAIM.search(claim.text) and (
            any(
                item.structured_fields.get("coverage") == "single_exchange"
                for item in packet.evidence
            )
            or any(
                snapshot.latest_price and snapshot.latest_price.coverage.value == "single_exchange"
                for snapshot in packet.market.values()
            )
        ):
            issues.append(
                _issue(
                    ValidationCode.IEX_COVERAGE_OVERCLAIM,
                    f"/claims/{claim.claim_id}/text",
                    "single-exchange evidence cannot be described as full-market coverage",
                )
            )
        cited_urls = {
            evidence_map[item].source.source_url
            for item in claim.evidence_ids
            if item in evidence_map
        }
        if any(url not in cited_urls for url in _URL_TEXT.findall(claim.text)):
            issues.append(
                _issue(
                    ValidationCode.UNSUPPORTED_CLAIM,
                    f"/claims/{claim.claim_id}/text",
                    "draft introduces a URL not present in its cited packet evidence",
                )
            )
        numeric_tokens = _NUMERIC_TEXT.findall(claim.text)
        numeric_bindings = _numeric_bindings(claim, packet, metric_map, plan_map, claim_map)
        numeric_issue_code = (
            ValidationCode.UNSUPPORTED_SIZING_VALUE
            if claim.field and "siz" in claim.field.lower()
            else ValidationCode.DETERMINISTIC_VALUE_MISMATCH
        )
        if numeric_tokens or claim.numeric_value is not None:
            claimed_binding = (
                (claim.numeric_value, claim.unit)
                if claim.numeric_value is not None and claim.unit is not None
                else None
            )
            if claimed_binding is None or claimed_binding not in numeric_bindings:
                issues.append(
                    _issue(
                        numeric_issue_code,
                        f"/claims/{claim.claim_id}/numeric_value",
                        "numeric claim value, field, or unit differs from its deterministic source",
                    )
                )
            if claim.unit is None or claim.unit.casefold() not in claim.text.casefold():
                issues.append(
                    _issue(
                        numeric_issue_code,
                        f"/claims/{claim.claim_id}/unit",
                        "numeric narrative must state the validated unit",
                        expected=claim.unit,
                    )
                )
        for token in numeric_tokens:
            try:
                number = Decimal(token.replace("$", "").removesuffix("%"))
            except InvalidOperation:
                continue
            if claim.numeric_value is None or not _same_quantized_decimal(
                number, claim.numeric_value
            ):
                issues.append(
                    _issue(
                        numeric_issue_code,
                        f"/claims/{claim.claim_id}/text",
                        "narrative number must exactly match the claim's field-bound value",
                        expected=(
                            format(claim.numeric_value, "f")
                            if claim.numeric_value is not None
                            else None
                        ),
                        actual=token,
                    )
                )
        if len(claim.text) > 2000:
            issues.append(
                _issue(
                    ValidationCode.TEXT_LIMIT_EXCEEDED,
                    f"/claims/{claim.claim_id}/text",
                    "claim text exceeds the validator limit",
                )
            )


def validate_research_brief(
    packet: ResearchPacket,
    draft: ResearchBriefDraft,
    validation_attempt: int,
) -> ValidationReport:
    """Validate a structured draft without mutating or rebuilding its frozen packet."""
    if type(validation_attempt) is not int or validation_attempt not in {1, 2, 3}:
        raise ValueError("validation_attempt must be 1, 2, or at most two repair attempts")
    issues: list[ValidationIssue] = []
    if draft.run_id != packet.run.run_id:
        issues.append(
            _issue(
                ValidationCode.RUN_ID_MISMATCH,
                "/run_id",
                "draft run_id differs from the frozen packet",
                expected=packet.run.run_id,
                actual=draft.run_id,
            )
        )
    expected_statuses = (
        packet.run.execution_status,
        packet.run.data_quality_status,
        packet.run.delivery_status,
    )
    actual_statuses = (draft.execution_status, draft.data_quality_status, draft.delivery_status)
    if expected_statuses != actual_statuses:
        issues.append(
            _issue(
                ValidationCode.STATUS_OVERCLAIM,
                "/status",
                "draft status differs from deterministic run status",
                expected="/".join(item.value for item in expected_statuses),
                actual="/".join(getattr(item, "value", str(item)) for item in actual_statuses),
            )
        )
    for pointer, sections, expected in (
        ("/executive_sections", draft.executive_sections, _EXECUTIVE_SECTIONS),
        ("/detailed_sections", draft.detailed_sections, _DETAILED_SECTIONS),
    ):
        actual = tuple(section.section for section in sections)
        if actual != expected:
            missing = tuple(section.value for section in expected if section not in actual)
            if missing:
                issues.append(
                    _issue(
                        ValidationCode.REQUIRED_SECTION_MISSING,
                        pointer,
                        "required report section is missing",
                        expected=", ".join(section.value for section in expected),
                        actual=", ".join(missing),
                    )
                )
            if (
                not missing
                or tuple(section for section in actual if section in expected) != expected
            ):
                issues.append(
                    _issue(
                        ValidationCode.REQUIRED_SECTION_ORDER,
                        pointer,
                        "report sections do not match the required exact order",
                        expected=" / ".join(section.value for section in expected),
                        actual=" / ".join(
                            getattr(section, "value", str(section)) for section in actual
                        ),
                    )
                )
    expected_capabilities = {
        state.capability: state for state in packet.capability_states if not state.available
    }
    explanations: dict[Capability, CapabilityExplanation] = {}
    for explanation in draft.disabled_capability_explanations:
        if explanation.capability in explanations:
            issues.append(
                _issue(
                    ValidationCode.INVALID_CAPABILITY_STATE,
                    "/disabled_capability_explanations",
                    "duplicate capability disclosure",
                )
            )
        explanations[explanation.capability] = explanation
    for capability, state in expected_capabilities.items():
        found_explanation = explanations.get(capability)
        if found_explanation is None:
            issues.append(
                _issue(
                    ValidationCode.DISABLED_CAPABILITY_OMITTED,
                    "/disabled_capability_explanations",
                    "disabled capability and reason are not disclosed",
                    expected=capability.value,
                )
            )
        elif set(found_explanation.reason_codes) != set(code.value for code in state.reason_codes):
            issues.append(
                _issue(
                    ValidationCode.INVALID_CAPABILITY_STATE,
                    f"/disabled_capability_explanations/{capability.value}",
                    "capability disclosure changes deterministic reason codes",
                    expected=", ".join(code.value for code in state.reason_codes),
                    actual=", ".join(found_explanation.reason_codes),
                )
            )
    for capability in set(explanations) - set(expected_capabilities):
        issues.append(
            _issue(
                ValidationCode.INVALID_CAPABILITY_STATE,
                "/disabled_capability_explanations",
                "draft marks an available or absent capability as disabled",
                actual=capability.value,
            )
        )
    _validate_claims(packet, draft, issues)
    ordered = tuple(
        sorted(issues, key=lambda item: (item.json_pointer, item.code.value, item.issue_id))
    )
    is_valid = not any(issue.severity is ValidationSeverity.ERROR for issue in ordered)
    return ValidationReport(
        run_id=packet.run.run_id,
        packet_id=packet.packet_id,
        packet_sha256=packet.canonical_sha256,
        is_valid=is_valid,
        repairable=not is_valid and validation_attempt < 3,
        validation_attempt=validation_attempt,
        repair_attempts_used=validation_attempt - 1,
        issues=ordered,
    )
