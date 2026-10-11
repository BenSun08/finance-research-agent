"""Deterministic reduced research report from one frozen packet."""

from datetime import datetime

from finance_research_agent.application.report_renderer import (
    _capability_limitation_lines,
    _inline_text,
)
from finance_research_agent.domain.enums import ReducedReportReason, ReportSection
from finance_research_agent.domain.packets import ResearchPacket


def _timestamp_range(start: datetime, end: datetime) -> str:
    if start == end:
        return start.isoformat()
    return f"{start.isoformat()} through {end.isoformat()}"


def _market_source_lines(packet: ResearchPacket) -> tuple[str, ...]:
    grouped: dict[
        tuple[str, str, str, str, str, str],
        list[tuple[datetime, datetime, datetime]],
    ] = {}

    def add(
        *,
        symbol: str,
        kind: str,
        provider: str,
        feed: str,
        coverage: str,
        session: str,
        observed_start: datetime,
        observed_end: datetime,
        retrieved_at: datetime,
    ) -> None:
        key = (symbol, kind, provider, feed, coverage, session)
        grouped.setdefault(key, []).append((observed_start, observed_end, retrieved_at))

    for _, snapshot in sorted(packet.market.items()):
        symbol = snapshot.instrument.symbol
        price = snapshot.latest_price
        if price is not None:
            add(
                symbol=symbol,
                kind="latest price",
                provider=price.provider,
                feed=price.feed,
                coverage=price.coverage.value,
                session=price.session.value,
                observed_start=price.observed_at,
                observed_end=price.observed_at,
                retrieved_at=price.retrieved_at,
            )
        for bar in snapshot.completed_daily_bars:
            add(
                symbol=symbol,
                kind="completed daily bars",
                provider=bar.provider,
                feed=bar.feed,
                coverage=bar.coverage.value,
                session=bar.session.value,
                observed_start=bar.source_timestamp,
                observed_end=bar.source_timestamp,
                retrieved_at=bar.retrieved_at,
            )
        for current_bar in snapshot.current_session_bars:
            add(
                symbol=symbol,
                kind="current-session bars",
                provider=current_bar.provider,
                feed=current_bar.feed,
                coverage=current_bar.coverage.value,
                session=current_bar.session.value,
                observed_start=current_bar.start_at,
                observed_end=current_bar.end_at,
                retrieved_at=current_bar.retrieved_at,
            )

    coverage_notes = {
        "consolidated": "U.S. consolidated coverage",
        "single_exchange": "one exchange only",
        "unknown": "coverage is unknown",
    }
    lines: list[str] = []
    for key, values in sorted(grouped.items()):
        symbol, kind, provider, feed, coverage, session = key
        observed_start = min(value[0] for value in values)
        observed_end = max(value[1] for value in values)
        retrieved_start = min(value[2] for value in values)
        retrieved_end = max(value[2] for value in values)
        coverage_note = coverage_notes.get(coverage, "coverage is unverified")
        lines.append(
            f"- Market-data source: {_inline_text(symbol)} {_inline_text(kind)}; "
            f"provider={_inline_text(provider)}; feed={_inline_text(feed)}; "
            f"coverage={_inline_text(coverage)} ({coverage_note}); "
            f"session={_inline_text(session)}; "
            f"observed_at={_timestamp_range(observed_start, observed_end)}; "
            f"retrieved_at={_timestamp_range(retrieved_start, retrieved_end)}."
        )
    return tuple(lines)


def _event_conflict_lines(packet: ResearchPacket) -> tuple[str, ...]:
    lines = []
    for event in sorted(packet.events, key=lambda item: item.event_id):
        if not event.conflict_evidence_ids:
            continue
        subject = event.subject_symbol or "market-wide"
        supporting = ", ".join(_inline_text(item) for item in event.supporting_evidence_ids)
        conflicts = ", ".join(_inline_text(item) for item in event.conflict_evidence_ids)
        lines.append(
            f"- Source conflict: {_inline_text(event.event_id)}; "
            f"event_type={_inline_text(event.event_type)}; "
            f"subject={_inline_text(subject)}; event_time={event.event_time.isoformat()}; "
            f"materiality={_inline_text(event.materiality)}; "
            f"verified={'yes' if event.verified else 'no'}; "
            f"supporting evidence={supporting or 'none'}; "
            f"conflicting evidence={conflicts}."
        )
    return tuple(lines)


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
    warnings.extend(_market_source_lines(packet))
    warnings.extend(_event_conflict_lines(packet))
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
            content = (
                *_capability_limitation_lines(packet, section),
                *section_content.get(section, ()),
            )
            lines.extend(content or ("- No deterministic reduced content.",))
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
