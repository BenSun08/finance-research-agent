"""Deterministic reduced research report from one frozen packet."""

from finance_research_agent.application.report_renderer import _inline_text
from finance_research_agent.domain.enums import ReducedReportReason, ReportSection
from finance_research_agent.domain.packets import ResearchPacket


def render_reduced_report_base(packet: ResearchPacket) -> bytes:
    """Stage a bounded report without model prose or live source reads."""
    evidence_cutoff_at = packet.run.require_evidence_cutoff()
    lines = [
        "# Premarket Research Brief",
        "",
        "Brief origin: DETERMINISTIC_REDUCED",
        f"Market date: {packet.run.market_date.isoformat()}",
        f"Run ID: {packet.run.run_id}",
        f"Execution status: {packet.run.execution_status.value}",
        f"Data quality status: {packet.run.data_quality_status.value}",
        f"Delivery status: {packet.run.delivery_status.value}",
        f"Evidence cutoff: {evidence_cutoff_at.isoformat()}",
    ]
    warnings = [
        f"- {_inline_text(gate.gate_id)}: {gate.status.value} "
        f"({_inline_text(gate.reason_code)}) — {_inline_text(gate.message)}"
        for gate in packet.gates
    ]
    warnings.extend(
        f"- Disabled capability: {state.capability.value} "
        f"({', '.join(reason.value for reason in state.reason_codes)})"
        for state in packet.capability_states if not state.available
    )
    candidates = [
        f"- {_inline_text(candidate.symbol)}: {candidate.plan_status.value}"
        for candidate in packet.candidates
    ]
    plans = [
        f"- {_inline_text(plan.plan_id)} ({_inline_text(plan.symbol)}): BLOCKED "
        f"for reduced-report review; frozen status {plan.plan_status.value}"
        for plan in packet.deterministic_plan_inputs
    ]
    exclusions = [
        f"- {_inline_text(item.symbol)}: "
        f"{', '.join(_inline_text(reason) for reason in item.reason_codes)}"
        for item in packet.candidate_exclusions
    ]
    provenance = [
        f"- {_inline_text(item.evidence_id)}: {_inline_text(item.source.provider)}"
        for item in packet.evidence
    ]
    section_content = {
        ReportSection.RUN_STATUS: [
            f"- Data quality: {packet.run.data_quality_status.value}; "
            f"delivery: {packet.run.delivery_status.value}."
        ],
        ReportSection.WATCHLIST_PRIORITIES: candidates,
        ReportSection.EXECUTIVE_TRADE_PLAN_DRAFTS: plans,
        ReportSection.DATA_WARNINGS: warnings,
        ReportSection.WATCHLIST_DASHBOARD: candidates,
        ReportSection.DETAILED_TRADE_PLAN_DRAFTS: plans,
        ReportSection.BLOCKED_EXCLUDED_CANDIDATES: exclusions,
        ReportSection.DATA_QUALITY_LIMITATIONS: warnings,
        ReportSection.EVIDENCE_INDEX: provenance,
        ReportSection.METHODOLOGY_RISK_NOTICE: [
            "- Research only. Human review is required before any decision."
        ],
    }
    sections = tuple(ReportSection.__members__.values())
    for title, values in (("Executive Brief", sections[:8]), ("Detailed Report", sections[8:])):
        lines.extend(("", f"## {title}", ""))
        for section in values:
            lines.extend((f"### {section.value}", ""))
            lines.extend(section_content.get(section) or ["- No deterministic reduced content."])
            lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def render_reduced_report(packet: ResearchPacket, reason: ReducedReportReason) -> str:
    """Add one closed fallback reason to the exact staged deterministic report."""
    base = render_reduced_report_base(packet).decode("utf-8")
    rendered = base.replace(
        "Brief origin: DETERMINISTIC_REDUCED\n",
        f"Brief origin: DETERMINISTIC_REDUCED\nFallback reason: {reason.value}\n",
        1,
    )
    return rendered.replace(
        f"Execution status: {packet.run.execution_status.value}\n",
        "Execution status: PUBLISHED\n",
        1,
    )
