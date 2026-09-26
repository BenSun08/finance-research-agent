"""Build and budget immutable Product A synthesis packets."""

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256

from finance_research_agent.application.prompt_source import canonical_prompt_sha256
from finance_research_agent.domain.metrics import MetricResult
from finance_research_agent.domain.models import (
    CapabilityState,
    EventRecord,
    EvidenceItem,
    GateResult,
    MarketSnapshot,
    RunContext,
)
from finance_research_agent.domain.observations import PlanObservation
from finance_research_agent.domain.packets import (
    PacketBudgetExceeded,
    ResearchPacket,
    SynthesisConstraints,
)
from finance_research_agent.domain.plans import TradePlanDraft
from finance_research_agent.domain.scoring import SetupCandidate
from finance_research_agent.domain.setups import CandidateExclusion
from finance_research_agent.domain.types import FrozenMap

_DISCOVERY_AUTHORITY_TIER = 4


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _payload(packet: ResearchPacket) -> dict[str, object]:
    return packet.model_dump(mode="json", exclude={"packet_id", "canonical_sha256"})


def _packet(
    *,
    run: RunContext,
    evidence: tuple[EvidenceItem, ...],
    market: FrozenMap[str, MarketSnapshot],
    events: tuple[EventRecord, ...],
    metrics: tuple[MetricResult, ...],
    gates: tuple[GateResult, ...],
    candidates: tuple[SetupCandidate, ...],
    exclusions: tuple[CandidateExclusion, ...],
    plans: tuple[TradePlanDraft, ...],
    capabilities: tuple[CapabilityState, ...],
    observations: tuple[PlanObservation, ...],
    max_serialized_bytes: int,
    omitted_bytes: Mapping[str, int],
) -> ResearchPacket:
    omitted_sections = tuple(omitted_bytes)
    provisional = ResearchPacket.model_construct(
        packet_id="packet-pending",
        canonical_sha256="0" * 64,
        run=run,
        evidence=evidence,
        market=market,
        events=events,
        metrics=metrics,
        gates=gates,
        candidates=candidates,
        candidate_exclusions=exclusions,
        deterministic_plan_inputs=plans,
        capability_states=capabilities,
        prior_plan_observations=observations,
        synthesis_constraints=SynthesisConstraints(
            max_serialized_bytes=max_serialized_bytes,
            serialized_bytes=1,
            truncated=bool(omitted_sections),
            omitted_sections=omitted_sections,
            omitted_bytes=FrozenMap(omitted_bytes),
        ),
    )
    digest = sha256(_canonical_json(_payload(provisional))).hexdigest()
    packet = provisional.model_copy(
        update={"packet_id": f"packet-{digest}", "canonical_sha256": digest}
    )
    while True:
        size = len(_canonical_json(packet.model_dump(mode="json")))
        if size == packet.synthesis_constraints.serialized_bytes:
            break
        packet = packet.model_copy(
            update={
                "synthesis_constraints": packet.synthesis_constraints.model_copy(
                    update={"serialized_bytes": size}
                )
            }
        )
        digest = sha256(_canonical_json(_payload(packet))).hexdigest()
        packet = packet.model_copy(
            update={"packet_id": f"packet-{digest}", "canonical_sha256": digest}
        )
    return packet


def _validated(packet: ResearchPacket) -> ResearchPacket:
    """Revalidate only a budget-compliant final packet."""
    return ResearchPacket.model_validate(packet, strict=True)


def _check_cutoff(
    run: RunContext,
    evidence: tuple[EvidenceItem, ...],
    market: FrozenMap[str, MarketSnapshot],
    metrics: tuple[MetricResult, ...],
) -> None:
    cutoff = run.evidence_cutoff_at
    for item in evidence:
        if item.source.retrieved_at > cutoff or item.source.observed_at > cutoff:
            raise ValueError("source observation is after evidence cutoff")
        if item.published_time is not None and item.published_time > cutoff:
            raise ValueError("evidence was published after evidence cutoff")
    for snapshot in market.values():
        for source in snapshot.source_observations:
            if source.retrieved_at > cutoff or source.observed_at > cutoff:
                raise ValueError("market source observation is after evidence cutoff")
        price = snapshot.latest_price
        if price is not None and (price.observed_at > cutoff or price.retrieved_at > cutoff):
            raise ValueError("market price observation is after evidence cutoff")
        for current_bar in snapshot.current_session_bars:
            if current_bar.end_at > cutoff or current_bar.retrieved_at > cutoff:
                raise ValueError("current market bar is after evidence cutoff")
        for daily_bar in snapshot.completed_daily_bars:
            if daily_bar.retrieved_at > cutoff:
                raise ValueError("completed market bar is after evidence cutoff")
    if any(metric.calculated_at > cutoff for metric in metrics):
        raise ValueError("metric was calculated after evidence cutoff")


def build_research_packet(
    run: RunContext,
    evidence: Sequence[EvidenceItem],
    snapshots: Mapping[str, MarketSnapshot],
    events: Sequence[EventRecord],
    metrics: Sequence[MetricResult],
    gates: Sequence[GateResult],
    candidates: Sequence[SetupCandidate],
    exclusions: Sequence[CandidateExclusion],
    plans: Sequence[TradePlanDraft],
    capabilities: Sequence[CapabilityState],
    observations: Sequence[PlanObservation],
    max_serialized_bytes: int,
) -> ResearchPacket:
    """Assemble deterministic content; only discovery prose is eligible for trimming."""
    if type(max_serialized_bytes) is not int or max_serialized_bytes <= 0:
        raise ValueError("max_serialized_bytes must be a positive integer")
    if run.prompt_version != canonical_prompt_sha256():
        raise ValueError("run prompt_version does not match canonical prompt digest")
    evidence_values = tuple(sorted(evidence, key=lambda item: item.evidence_id))
    if len({item.evidence_id for item in evidence_values}) != len(evidence_values):
        raise ValueError("duplicate evidence identifier")
    market_values = FrozenMap(snapshots)
    event_values = tuple(sorted(events, key=lambda item: item.event_id))
    metric_values = tuple(sorted(metrics, key=lambda item: item.metric_id))
    gate_values = tuple(sorted(gates, key=lambda item: item.gate_id))
    candidate_values = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    exclusion_values = tuple(exclusions)
    plan_values = tuple(sorted(plans, key=lambda item: item.plan_id))
    capability_values = tuple(sorted(capabilities, key=lambda item: item.capability.value))
    observation_values = tuple(sorted(observations, key=lambda item: item.plan_id))
    _check_cutoff(run, evidence_values, market_values, metric_values)

    trimmed = list(evidence_values)
    omitted: dict[str, int] = {}
    packet = _packet(
        run=run,
        evidence=tuple(trimmed),
        market=market_values,
        events=event_values,
        metrics=metric_values,
        gates=gate_values,
        candidates=candidate_values,
        exclusions=exclusion_values,
        plans=plan_values,
        capabilities=capability_values,
        observations=observation_values,
        max_serialized_bytes=max_serialized_bytes,
        omitted_bytes=omitted,
    )
    if packet.synthesis_constraints.serialized_bytes <= max_serialized_bytes:
        return _validated(packet)

    seen: set[str] = set()
    for index, item in enumerate(trimmed):
        excerpt = item.source.excerpt
        if item.authority_tier >= _DISCOVERY_AUTHORITY_TIER and excerpt and excerpt in seen:
            omitted["duplicate_discovery_excerpts"] = omitted.get(
                "duplicate_discovery_excerpts", 0
            ) + len(excerpt.encode("utf-8"))
            trimmed[index] = item.model_copy(
                update={"source": item.source.model_copy(update={"excerpt": ""})}
            )
        elif excerpt:
            seen.add(excerpt)
    if omitted:
        packet = _packet(
            run=run,
            evidence=tuple(trimmed),
            market=market_values,
            events=event_values,
            metrics=metric_values,
            gates=gate_values,
            candidates=candidate_values,
            exclusions=exclusion_values,
            plans=plan_values,
            capabilities=capability_values,
            observations=observation_values,
            max_serialized_bytes=max_serialized_bytes,
            omitted_bytes=omitted,
        )
        if packet.synthesis_constraints.serialized_bytes <= max_serialized_bytes:
            return _validated(packet)

    removed = 0
    for index, item in enumerate(trimmed):
        excerpt = item.source.excerpt
        if item.authority_tier >= _DISCOVERY_AUTHORITY_TIER and excerpt:
            removed += len(excerpt.encode("utf-8"))
            trimmed[index] = item.model_copy(
                update={"source": item.source.model_copy(update={"excerpt": ""})}
            )
    if removed:
        omitted["low_authority_discovery_excerpts"] = removed
    packet = _packet(
        run=run,
        evidence=tuple(trimmed),
        market=market_values,
        events=event_values,
        metrics=metric_values,
        gates=gate_values,
        candidates=candidate_values,
        exclusions=exclusion_values,
        plans=plan_values,
        capabilities=capability_values,
        observations=observation_values,
        max_serialized_bytes=max_serialized_bytes,
        omitted_bytes=omitted,
    )
    if packet.synthesis_constraints.serialized_bytes <= max_serialized_bytes:
        return _validated(packet)

    description_bytes = 0
    for index, item in enumerate(trimmed):
        replacement = item.evidence_id
        description_bytes += max(
            0,
            len(item.citation_label.encode("utf-8")) - len(replacement.encode("utf-8")),
        )
        trimmed[index] = item.model_copy(update={"citation_label": replacement})
    if description_bytes:
        omitted["verbose_non_factual_descriptions"] = description_bytes
    packet = _packet(
        run=run,
        evidence=tuple(trimmed),
        market=market_values,
        events=event_values,
        metrics=metric_values,
        gates=gate_values,
        candidates=candidate_values,
        exclusions=exclusion_values,
        plans=plan_values,
        capabilities=capability_values,
        observations=observation_values,
        max_serialized_bytes=max_serialized_bytes,
        omitted_bytes=omitted,
    )
    if packet.synthesis_constraints.serialized_bytes > max_serialized_bytes:
        raise PacketBudgetExceeded("protected packet content exceeds max_serialized_bytes")
    return _validated(packet)
