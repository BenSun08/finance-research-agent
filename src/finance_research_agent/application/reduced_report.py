"""Deterministic reduced research report from one frozen packet."""

from finance_research_agent.application.report_renderer import _inline_text
from finance_research_agent.domain.packets import ResearchPacket


def render_reduced_report_base(packet: ResearchPacket) -> bytes:
    """Stage a bounded report without model prose or live source reads."""
    lines = [
        "# Premarket Research Brief",
        "",
        "Brief origin: DETERMINISTIC_REDUCED",
        f"Market date: {packet.run.market_date.isoformat()}",
        f"Run ID: {packet.run.run_id}",
        f"Execution status: {packet.run.execution_status.value}",
        f"Data quality status: {packet.run.data_quality_status.value}",
        f"Delivery status: {packet.run.delivery_status.value}",
        f"Evidence cutoff: {packet.run.evidence_cutoff_at.isoformat()}",
        "",
        "## Data Warnings and Gates",
        "",
    ]
    lines.extend(
        f"- {_inline_text(gate.gate_id)}: {gate.status.value} "
        f"({_inline_text(gate.reason_code)}) — {_inline_text(gate.message)}"
        for gate in packet.gates
    )
    lines.extend(
        f"- Disabled capability: {state.capability.value} "
        f"({', '.join(reason.value for reason in state.reason_codes)})"
        for state in packet.capability_states if not state.available
    )
    lines.extend(("", "## Watchlist Candidates", ""))
    lines.extend(
        f"- {_inline_text(candidate.symbol)}: {candidate.plan_status.value}"
        for candidate in packet.candidates
    )
    lines.extend(("", "## Research Plan Drafts", ""))
    lines.extend(
        f"- {_inline_text(plan.plan_id)} ({_inline_text(plan.symbol)}): BLOCKED "
        f"for reduced-report review; frozen status {plan.plan_status.value}"
        for plan in packet.deterministic_plan_inputs
    )
    lines.extend(("", "## Excluded Candidates", ""))
    lines.extend(
        f"- {_inline_text(item.symbol)}: "
        f"{', '.join(_inline_text(reason) for reason in item.reason_codes)}"
        for item in packet.candidate_exclusions
    )
    lines.extend(("", "## Evidence Provenance", ""))
    lines.extend(
        f"- {_inline_text(item.evidence_id)}: {_inline_text(item.source.provider)}"
        for item in packet.evidence
    )
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")
