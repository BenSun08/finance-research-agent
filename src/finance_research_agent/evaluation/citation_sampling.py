"""Deterministically select a small, structurally relevant citation-review sample."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from finance_research_agent.domain.enums import ClaimType, ReportSection
from finance_research_agent.domain.metrics import MetricResult
from finance_research_agent.domain.models import (
    EvidenceItem,
    PublishedArtifact,
    PublishedRunBundle,
)
from finance_research_agent.domain.packets import ResearchPacket
from finance_research_agent.domain.types import UtcDatetime, canonical_bytes
from finance_research_agent.domain.validation import Claim, ResearchBriefDraft

_MAX_SAMPLE_SIZE = 5
_PRIORITY_SECTIONS = frozenset(
    {
        ReportSection.EXECUTIVE_TRADE_PLAN_DRAFTS,
        ReportSection.DETAILED_TRADE_PLAN_DRAFTS,
        ReportSection.MARKET_REGIME,
        ReportSection.TODAY_EVENT_CLOCK,
        ReportSection.MACRO_EVENT_CALENDAR,
    }
)


class CitationSampleEmptyReason(StrEnum):
    """Structural reason why a run has no claim available for human review."""

    NO_MATERIAL_CLAIMS = "NO_MATERIAL_CLAIMS"


@dataclass(frozen=True, slots=True)
class CitationReviewContext:
    """Bounded evidence and provenance for one selected human review claim."""

    claim: Claim
    supporting_claims: tuple[Claim, ...]
    cited_evidence: tuple[EvidenceItem, ...]
    counter_evidence: tuple[EvidenceItem, ...]
    metric_bindings: tuple[MetricResult, ...]
    metric_input_evidence: tuple[EvidenceItem, ...]
    authority_tiers: tuple[int, ...]
    publication_time: UtcDatetime
    evidence_cutoff: UtcDatetime


@dataclass(frozen=True, slots=True)
class CitationEntailmentReviewSample:
    """Deterministic review contexts, or the structural empty-sample reason."""

    contexts: tuple[CitationReviewContext, ...]
    empty_reason: CitationSampleEmptyReason | None

    def __post_init__(self) -> None:
        if bool(self.contexts) == (self.empty_reason is not None):
            raise ValueError("review sample must contain contexts or one empty reason")

    @property
    def claim_ids(self) -> tuple[str, ...]:
        """Return selected claim identifiers in review order."""
        return tuple(context.claim.claim_id for context in self.contexts)


@dataclass(frozen=True, slots=True)
class _Candidate:
    claim_id: str
    claim_type: ClaimType
    authority_tiers: frozenset[int]
    priority: bool
    tie_key: str


@dataclass(frozen=True, slots=True)
class _Selection:
    priority_count: int
    tie_keys: tuple[str, ...]
    claim_ids: tuple[str, ...]


def _bundle_models(
    bundle: PublishedRunBundle,
) -> tuple[ResearchPacket | None, ResearchBriefDraft | None]:
    contents = bundle.model_dump(mode="json")["bundle"]
    packet_data = contents.get("research_packet")
    brief_data = contents.get("brief_draft")
    packet = None
    brief = None
    try:
        if packet_data is not None:
            packet = ResearchPacket.model_validate_json(
                json.dumps(packet_data, sort_keys=True, separators=(",", ":"), allow_nan=False)
            )
        if brief_data is not None:
            brief = ResearchBriefDraft.model_validate_json(
                json.dumps(brief_data, sort_keys=True, separators=(",", ":"), allow_nan=False)
            )
    except (TypeError, ValueError) as exc:
        raise ValueError("published bundle has malformed citation-sampling inputs") from exc
    return packet, brief


def _reachable_claim_ids(brief: ResearchBriefDraft) -> frozenset[str]:
    claims: dict[str, Claim] = {}
    for claim in brief.claims:
        if claim.claim_id in claims:
            raise ValueError("published brief has duplicate claim ids")
        claims[claim.claim_id] = claim

    roots = [
        claim_id
        for section in (*brief.executive_sections, *brief.detailed_sections)
        for claim_id in section.claim_ids
    ]
    roots.extend(
        claim_id
        for narrative in brief.plan_narratives
        for claim_id in narrative.claim_ids
    )
    reachable: set[str] = set()
    pending = list(roots)
    while pending:
        claim_id = pending.pop()
        found_claim = claims.get(claim_id)
        if found_claim is None:
            raise ValueError("published brief references a missing material claim")
        if claim_id in reachable:
            continue
        reachable.add(claim_id)
        pending.extend(found_claim.supports_claim_ids)
    return frozenset(reachable)


def _claim_closure(claim_id: str, claims: Mapping[str, Claim]) -> tuple[Claim, ...]:
    result: list[Claim] = []
    seen: set[str] = set()
    pending = [claim_id]
    while pending:
        current_id = pending.pop()
        claim = claims.get(current_id)
        if claim is None:
            raise ValueError("published brief references a missing supporting claim")
        if current_id in seen:
            continue
        seen.add(current_id)
        result.append(claim)
        pending.extend(claim.supports_claim_ids)
    return tuple(result)


def _authority_tiers(
    claim_id: str,
    claims: Mapping[str, Claim],
    evidence_tiers: Mapping[str, int],
    metrics: Mapping[str, MetricResult],
) -> frozenset[int]:
    tiers: set[int] = set()
    for claim in _claim_closure(claim_id, claims):
        evidence_ids = (*claim.evidence_ids, *claim.counter_evidence_ids)
        for evidence_id in evidence_ids:
            tier = evidence_tiers.get(evidence_id)
            if tier is None:
                raise ValueError("published claim references missing evidence")
            tiers.add(tier)
        for metric_id in claim.metric_ids:
            metric = metrics.get(metric_id)
            if metric is None:
                raise ValueError("published claim references a missing metric")
            for evidence_id in metric.input_evidence_ids:
                tier = evidence_tiers.get(evidence_id)
                if tier is None:
                    raise ValueError("published metric references missing evidence")
                tiers.add(tier)
    return frozenset(tiers)


def _is_priority_claim(
    claim: Claim,
    priority_claim_ids: frozenset[str],
) -> bool:
    return claim.plan_id is not None or claim.claim_id in priority_claim_ids


def _extend_tiers(
    anchor: int | None,
    has_multiple: bool,
    candidate_tiers: frozenset[int],
) -> tuple[int | None, bool]:
    if has_multiple:
        return None, True
    if anchor is None:
        if len(candidate_tiers) >= 2:
            return None, True
        return (next(iter(candidate_tiers)) if candidate_tiers else None), False
    if any(tier != anchor for tier in candidate_tiers):
        return None, True
    return anchor, False


def _prefer(candidate: _Selection, current: _Selection | None) -> bool:
    if current is None:
        return True
    if candidate.priority_count != current.priority_count:
        return candidate.priority_count > current.priority_count
    return candidate.tie_keys < current.tie_keys


def _select(
    candidates: tuple[_Candidate, ...],
    maximum_claims: int,
) -> tuple[str, ...]:
    sample_size = min(maximum_claims, _MAX_SAMPLE_SIZE, len(candidates))
    type_indexes = {claim_type: index for index, claim_type in enumerate(ClaimType)}
    states: dict[tuple[int, int, int | None, bool], _Selection] = {
        (0, 0, None, False): _Selection(0, (), ())
    }
    for candidate in candidates:
        next_states = dict(states)
        for (count, type_mask, tier_anchor, has_multiple), selection in states.items():
            if count >= sample_size:
                continue
            new_anchor, new_has_multiple = _extend_tiers(
                tier_anchor, has_multiple, candidate.authority_tiers
            )
            new_mask = type_mask | (1 << type_indexes[candidate.claim_type])
            next_selection = _Selection(
                priority_count=selection.priority_count + int(candidate.priority),
                tie_keys=tuple(sorted((*selection.tie_keys, candidate.tie_key))),
                claim_ids=(*selection.claim_ids, candidate.claim_id),
            )
            key = (count + 1, new_mask, new_anchor, new_has_multiple)
            if _prefer(next_selection, next_states.get(key)):
                next_states[key] = next_selection
        states = next_states

    finalists = (
        (key, value)
        for key, value in states.items()
        if key[0] == sample_size
    )
    best_key: tuple[int, int, int | None, bool] | None = None
    best_selection: _Selection | None = None
    best_score: tuple[int, int, int] | None = None
    for key, selection in finalists:
        score = (key[1].bit_count(), int(key[3]), selection.priority_count)
        if best_score is None or score > best_score or (
            score == best_score
            and best_selection is not None
            and selection.tie_keys < best_selection.tie_keys
        ):
            best_key, best_selection, best_score = key, selection, score
    if best_selection is None or best_key is None:
        return ()

    selected = {
        item.claim_id: item
        for item in candidates
        if item.claim_id in best_selection.claim_ids
    }
    first_by_type: dict[ClaimType, _Candidate] = {}
    for item in selected.values():
        prior = first_by_type.get(item.claim_type)
        if prior is None or item.tie_key < prior.tie_key:
            first_by_type[item.claim_type] = item
    first_ids = tuple(
        first_by_type[claim_type].claim_id
        for claim_type in ClaimType
        if claim_type in first_by_type
    )
    first_set = set(first_ids)
    remaining = sorted(
        (item for item in selected.values() if item.claim_id not in first_set),
        key=lambda item: (-int(item.priority), item.tie_key),
    )
    return (*first_ids, *(item.claim_id for item in remaining))


def select_citation_entailment_sample(
    bundle: PublishedRunBundle,
    maximum_claims: int = _MAX_SAMPLE_SIZE,
) -> tuple[str, ...]:
    """Select reachable claims with bounded type, authority, and conclusion coverage.

    At most five claims are returned even if a larger maximum is requested. The
    selector never infers authority or materiality from claim prose.
    """
    if type(maximum_claims) is not int or maximum_claims < 1:
        raise ValueError("maximum_claims must be a positive integer")
    packet, brief = _bundle_models(bundle)
    if brief is None:
        return ()
    reachable = _reachable_claim_ids(brief)
    if not reachable:
        return ()
    if packet is None:
        raise ValueError("material claims require a frozen research packet")

    claim_map = {claim.claim_id: claim for claim in brief.claims}
    packet_map = {claim_id: claim for claim_id, claim in claim_map.items() if claim_id in reachable}
    evidence_tiers = {item.evidence_id: item.authority_tier for item in packet.evidence}
    metric_map = {metric.metric_id: metric for metric in packet.metrics}
    priority_roots = {
        claim_id
        for section in (*brief.executive_sections, *brief.detailed_sections)
        if section.section in _PRIORITY_SECTIONS
        for claim_id in section.claim_ids
    }
    priority_roots.update(
        claim_id for narrative in brief.plan_narratives for claim_id in narrative.claim_ids
    )
    priority_claim_ids: set[str] = set()
    for root_id in priority_roots:
        priority_claim_ids.update(
            claim.claim_id for claim in _claim_closure(root_id, claim_map)
        )

    bundle_hash = sha256(canonical_bytes(bundle)).hexdigest()
    candidates = tuple(
        _Candidate(
            claim_id=claim.claim_id,
            claim_type=claim.claim_type,
            authority_tiers=_authority_tiers(
                claim.claim_id, claim_map, evidence_tiers, metric_map
            ),
            priority=_is_priority_claim(claim, frozenset(priority_claim_ids)),
            tie_key=sha256(f"{bundle_hash}:{claim.claim_id}".encode()).hexdigest(),
        )
        for claim in brief.claims
        if claim.claim_id in packet_map
    )
    return _select(candidates, maximum_claims)


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def build_citation_entailment_review_sample(
    bundle: PublishedRunBundle,
    *,
    publication: PublishedArtifact,
    maximum_claims: int = _MAX_SAMPLE_SIZE,
) -> CitationEntailmentReviewSample:
    """Materialize human-review context from the bundle and its publication receipt.

    Evidence excerpts and structured fields are retained from the bounded frozen
    packet. The publication timestamp must come from the verified publication
    receipt. This function supplies evidence context only and never assigns a verdict.
    """
    if (
        publication.run_id != bundle.run.run_id
        or publication.bundle_sha256 != sha256(canonical_bytes(bundle)).hexdigest()
    ):
        raise ValueError("citation review context requires a matching publication receipt")

    selected_claim_ids = select_citation_entailment_sample(
        bundle, maximum_claims=maximum_claims
    )
    if not selected_claim_ids:
        return CitationEntailmentReviewSample(
            contexts=(),
            empty_reason=CitationSampleEmptyReason.NO_MATERIAL_CLAIMS,
        )

    packet, brief = _bundle_models(bundle)
    if packet is None or brief is None:
        raise ValueError("selected citation claims require a packet and brief")
    if packet.run.run_id != bundle.run.run_id or brief.run_id != bundle.run.run_id:
        raise ValueError("citation review context inputs must share one run id")

    claim_map = {claim.claim_id: claim for claim in brief.claims}
    evidence_map = {item.evidence_id: item for item in packet.evidence}
    metric_map = {metric.metric_id: metric for metric in packet.metrics}
    if len(evidence_map) != len(packet.evidence):
        raise ValueError("published packet has duplicate evidence ids")
    if len(metric_map) != len(packet.metrics):
        raise ValueError("published packet has duplicate metric ids")

    cutoff = packet.run.require_evidence_cutoff()
    contexts: list[CitationReviewContext] = []
    for selected_claim_id in selected_claim_ids:
        closure = _claim_closure(selected_claim_id, claim_map)
        cited_ids = _ordered_unique(
            evidence_id for claim in closure for evidence_id in claim.evidence_ids
        )
        counter_evidence_ids = _ordered_unique(
            evidence_id
            for claim in closure
            for evidence_id in claim.counter_evidence_ids
        )
        metric_ids = _ordered_unique(
            metric_id for claim in closure for metric_id in claim.metric_ids
        )
        try:
            metrics = tuple(metric_map[metric_id] for metric_id in metric_ids)
            cited = tuple(evidence_map[evidence_id] for evidence_id in cited_ids)
            counter_evidence = tuple(
                evidence_map[evidence_id] for evidence_id in counter_evidence_ids
            )
            metric_input_ids = _ordered_unique(
                evidence_id
                for metric in metrics
                for evidence_id in metric.input_evidence_ids
            )
            metric_input_evidence = tuple(
                evidence_map[evidence_id] for evidence_id in metric_input_ids
            )
        except KeyError as exc:
            raise ValueError("published citation context references missing packet data") from exc

        all_evidence = {
            item.evidence_id: item
            for item in (*cited, *counter_evidence, *metric_input_evidence)
        }
        contexts.append(
            CitationReviewContext(
                claim=claim_map[selected_claim_id],
                supporting_claims=closure[1:],
                cited_evidence=cited,
                counter_evidence=counter_evidence,
                metric_bindings=metrics,
                metric_input_evidence=metric_input_evidence,
                authority_tiers=tuple(
                    sorted({item.authority_tier for item in all_evidence.values()})
                ),
                publication_time=publication.published_at,
                evidence_cutoff=cutoff,
            )
        )

    return CitationEntailmentReviewSample(
        contexts=tuple(contexts),
        empty_reason=None,
    )


__all__ = [
    "CitationEntailmentReviewSample",
    "CitationReviewContext",
    "CitationSampleEmptyReason",
    "build_citation_entailment_review_sample",
    "select_citation_entailment_sample",
]
