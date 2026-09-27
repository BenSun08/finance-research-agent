"""Render a validated Product A brief into deterministic Markdown."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping

from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.validation import (
    Claim,
    ResearchBriefDraft,
    ValidationReport,
    validate_research_brief,
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
    return f"- {text}{suffix}"


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
        lines.extend(_claim_line(claim_id, claim_map) for claim_id in section.claim_ids)
        if section.section.value == "Data Warnings":
            lines.extend(f"- {_inline_text(warning)}" for warning in draft.data_warnings)
            lines.extend(
                "- Disabled capability: "
                f"{explanation.capability.value} ({', '.join(explanation.reason_codes)})"
                for explanation in draft.disabled_capability_explanations
            )
        if not section.claim_ids and not (
            section.section.value == "Data Warnings"
            and (draft.data_warnings or draft.disabled_capability_explanations)
        ):
            lines.append("- No validated claims.")
        lines.append("")

    lines.extend(("## Detailed Report", ""))
    for section in draft.detailed_sections:
        lines.extend((f"### {section.section.value}", ""))
        lines.extend(_claim_line(claim_id, claim_map) for claim_id in section.claim_ids)
        if not section.claim_ids:
            lines.append("- No validated claims.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
