"""Render a validated Product A brief into deterministic Markdown."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping

from finance_research_agent.domain.enums import Capability, ReportSection
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.validation import (
    Claim,
    ResearchBriefDraft,
    ValidationReport,
    validate_research_brief,
)

_CAPABILITY_AFFECTED_SECTIONS: Mapping[Capability, frozenset[ReportSection]] = {
    Capability.MARKET_SUMMARY_AVAILABLE: frozenset(
        {ReportSection.MARKET_POSTURE, ReportSection.CORE_MARKET_RISKS}
    ),
    Capability.REGIME_CLASSIFICATION_AVAILABLE: frozenset(
        {ReportSection.MARKET_POSTURE, ReportSection.MARKET_REGIME}
    ),
    Capability.WATCHLIST_METRICS_AVAILABLE: frozenset(
        {ReportSection.WATCHLIST_PRIORITIES, ReportSection.WATCHLIST_DASHBOARD}
    ),
    Capability.EVENT_RISK_CHECK_AVAILABLE: frozenset(
        {ReportSection.TODAY_EVENT_CLOCK, ReportSection.MACRO_EVENT_CALENDAR}
    ),
    Capability.SETUP_DETECTION_AVAILABLE: frozenset(
        {ReportSection.WATCHLIST_PRIORITIES, ReportSection.ELIGIBLE_SETUPS}
    ),
    Capability.PLAN_DRAFT_AVAILABLE: frozenset(
        {
            ReportSection.EXECUTIVE_TRADE_PLAN_DRAFTS,
            ReportSection.DETAILED_TRADE_PLAN_DRAFTS,
        }
    ),
    Capability.POSITION_SIZING_AVAILABLE: frozenset(
        {
            ReportSection.EXECUTIVE_TRADE_PLAN_DRAFTS,
            ReportSection.DETAILED_TRADE_PLAN_DRAFTS,
        }
    ),
    Capability.PORTFOLIO_HEAT_CHECK_AVAILABLE: frozenset(
        {
            ReportSection.EXECUTIVE_TRADE_PLAN_DRAFTS,
            ReportSection.DETAILED_TRADE_PLAN_DRAFTS,
        }
    ),
}


def _capability_limitation_lines(
    packet: ResearchPacket, section: ReportSection
) -> tuple[str, ...]:
    return tuple(
        f"- Limitation: {state.capability.value} unavailable "
        f"({', '.join(reason.value for reason in state.reason_codes)})."
        for state in packet.capability_states
        if not state.available
        and section in _CAPABILITY_AFFECTED_SECTIONS[state.capability]
    )


def _inline_text(value: str) -> str:
    """Flatten and escape narrative text so it cannot add Markdown structure."""
    flattened = " ".join(value.split())
    escaped = html.escape(flattened, quote=False)
    return re.sub(r"([\\`*_{}\[\]()!|~])", r"\\\1", escaped)


def _claim_line(claim_id: str, claims: Mapping[str, Claim]) -> str:
    claim = claims[claim_id]
    text = _inline_text(claim.text)
    citations = tuple(
        f"[{citation}]"
        for citation in (
            *claim.evidence_ids,
            *claim.metric_ids,
        )
    )
    suffix = f" {' '.join(citations)}" if citations else ""
    line = f"- {text}{suffix}"
    context = _claim_context_lines(claim)
    if context:
        return "\n".join((line, *(f"  - {item}" for item in context)))
    return line


def _claim_context_lines(claim: Claim) -> tuple[str, ...]:
    context: list[str] = []
    if claim.plan_status is not None:
        context.append(f"Plan status: {claim.plan_status.value}")
    if claim.counter_evidence_ids:
        citations = " ".join(f"[{item}]" for item in claim.counter_evidence_ids)
        context.append(f"Counter-evidence: {citations}")
    if claim.invalidation:
        context.append(f"Invalidation: {_inline_text(claim.invalidation)}")
    if claim.expires_at is not None:
        context.append(f"Expires: {claim.expires_at.isoformat()}")
    return tuple(context)


def _plan_narrative_lines(draft: ResearchBriefDraft, claims: Mapping[str, Claim]) -> list[str]:
    lines: list[str] = []
    for narrative in draft.plan_narratives:
        citations: list[str] = []
        pending = list(narrative.claim_ids)
        seen: set[str] = set()
        while pending:
            claim_id = pending.pop(0)
            if claim_id in seen:
                continue
            seen.add(claim_id)
            claim = claims[claim_id]
            citations.extend((*claim.evidence_ids, *claim.metric_ids))
            pending.extend(claim.supports_claim_ids)
        citation_suffix = " " + " ".join(
            f"[{citation}]" for citation in dict.fromkeys(citations)
        ) if citations else ""
        lines.append(
            f"- Plan {narrative.plan_id}: {_inline_text(narrative.text)}{citation_suffix}"
        )
        for claim_id in narrative.claim_ids:
            lines.extend(
                f"  - {item}" for item in _claim_context_lines(claims[claim_id])
            )
    return lines


def render_markdown_report(
    packet: ResearchPacket,
    draft: ResearchBriefDraft,
    validation: ValidationReport,
) -> str:
    """Render the exact frozen packet/draft pair after deterministic validation.

    The validation result is re-computed here so callers cannot bypass the
    publication boundary by constructing a matching-looking report value.
    """
    if not validation.is_valid:
        raise ValueError("only a valid draft can be rendered")
    if (
        draft.run_id != packet.run.run_id
        or validation.run_id != packet.run.run_id
        or validation.packet_id != packet.packet_id
        or validation.packet_sha256 != packet.canonical_sha256
    ):
        raise ValueError("validation report does not match the frozen packet")
    verified = validate_research_brief(
        packet,
        draft,
        validation_attempt=validation.validation_attempt,
    )
    if verified != validation:
        raise ValueError("validation report does not match deterministic validation")

    claim_map = {claim.claim_id: claim for claim in draft.claims}
    lines = [
        "# Premarket Research Brief",
        "",
        f"Market date: {packet.run.market_date.isoformat()}",
        f"Run ID: {packet.run.run_id}",
        f"Execution status: {draft.execution_status.value}",
        f"Data quality status: {draft.data_quality_status.value}",
        f"Delivery status: {draft.delivery_status.value}",
        f"Brief origin: {draft.origin.value}",
        "",
        "## Executive Brief",
        "",
    ]

    for section in draft.executive_sections:
        lines.extend((f"### {section.section.value}", ""))
        limitations = _capability_limitation_lines(packet, section.section)
        lines.extend(limitations)
        lines.extend(_claim_line(claim_id, claim_map) for claim_id in section.claim_ids)
        if section.section.value == "Data Warnings":
            lines.extend(f"- {_inline_text(warning)}" for warning in draft.data_warnings)
            lines.extend(
                "- Disabled capability: "
                f"{explanation.capability.value} ({', '.join(explanation.reason_codes)})"
                for explanation in draft.disabled_capability_explanations
            )
        has_capability_warnings = (
            section.section.value == "Data Warnings"
            and bool(draft.disabled_capability_explanations)
        )
        if not section.claim_ids and not (
            limitations
            or has_capability_warnings
            or section.section.value == "Data Warnings" and draft.data_warnings
        ):
            lines.append("- No validated claims.")
        lines.append("")

    lines.extend(("## Detailed Report", ""))
    for section in draft.detailed_sections:
        lines.extend((f"### {section.section.value}", ""))
        limitations = _capability_limitation_lines(packet, section.section)
        lines.extend(limitations)
        lines.extend(_claim_line(claim_id, claim_map) for claim_id in section.claim_ids)
        has_capability_warnings = section.section.value == "Data Quality and Limitations"
        if has_capability_warnings:
            lines.extend(
                "- Disabled capability: "
                f"{explanation.capability.value} ({', '.join(explanation.reason_codes)})"
                for explanation in draft.disabled_capability_explanations
            )
        if section.section.value == "Trade Plan Drafts":
            lines.extend(_plan_narrative_lines(draft, claim_map))
        if not section.claim_ids and not (
            limitations
            or has_capability_warnings and draft.disabled_capability_explanations
            or section.section.value == "Trade Plan Drafts" and draft.plan_narratives
        ):
            lines.append("- No validated claims.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
