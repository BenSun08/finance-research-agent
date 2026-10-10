"""Deterministic, bounded human citation-review sampling."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import yaml

from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.domain.enums import (
    BriefOrigin,
    ClaimType,
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    ReportSection,
)
from finance_research_agent.domain.models import PublishedArtifact, PublishedRunBundle
from finance_research_agent.domain.types import FrozenMap, canonical_bytes
from finance_research_agent.domain.validation import (
    Claim,
    PlanNarrative,
    ReportSectionClaims,
    ResearchBriefDraft,
)


def _claim(
    claim_id: str,
    claim_type: ClaimType,
    *,
    evidence_ids: tuple[str, ...] = (),
    metric_ids: tuple[str, ...] = (),
    counter_evidence_ids: tuple[str, ...] = (),
    supports_claim_ids: tuple[str, ...] = (),
    plan_id: str | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_type=claim_type,
        text=f"Claim text for {claim_id}.",
        subject_symbol="AAPL",
        field="research_value",
        evidence_ids=evidence_ids,
        metric_ids=metric_ids,
        counter_evidence_ids=counter_evidence_ids,
        supports_claim_ids=supports_claim_ids,
        plan_id=plan_id,
    )


def _draft(
    claims: tuple[Claim, ...],
    *,
    run_id: str,
    roots: tuple[str, ...],
    plan_claim_ids: tuple[str, ...] = (),
) -> ResearchBriefDraft:
    executive_sections = tuple(
        ReportSectionClaims(
            section=section,
            claim_ids=roots if section is ReportSection.MARKET_POSTURE else (),
        )
        for section in (
            ReportSection.RUN_STATUS,
            ReportSection.MARKET_POSTURE,
            ReportSection.WHAT_CHANGED,
            ReportSection.TODAY_EVENT_CLOCK,
            ReportSection.CORE_MARKET_RISKS,
            ReportSection.WATCHLIST_PRIORITIES,
            ReportSection.EXECUTIVE_TRADE_PLAN_DRAFTS,
            ReportSection.DATA_WARNINGS,
        )
    )
    detailed_sections = tuple(
        ReportSectionClaims(section=section, claim_ids=())
        for section in (
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
    )
    return ResearchBriefDraft(
        run_id=run_id,
        origin=BriefOrigin.SYNTHESIZED,
        execution_status=ExecutionStatus.PUBLISHED,
        data_quality_status=DataQualityStatus.DEGRADED,
        delivery_status=DeliveryStatus.MANUAL,
        executive_sections=executive_sections,
        detailed_sections=detailed_sections,
        claims=claims,
        plan_narratives=tuple(
            PlanNarrative(
                plan_id=f"plan-{claim_id}",
                text="Plan conclusion.",
                claim_ids=(claim_id,),
            )
            for claim_id in plan_claim_ids
        ),
        disabled_capability_explanations=(),
        data_warnings=(),
    )


def _bundle(
    valid_packet,
    valid_brief_draft,
    *,
    claims: tuple[Claim, ...] | None = None,
    roots: tuple[str, ...] | None = None,
    second_tier: bool = False,
    plan_claim_ids: tuple[str, ...] = (),
    packet_override=None,
):
    if claims is None:
        claims = valid_brief_draft.claims
    if roots is None:
        roots = tuple(
            claim.claim_id for claim in claims if claim.claim_id != "orphan"
        )
    draft = _draft(
        claims,
        run_id=valid_packet.run.run_id,
        roots=roots,
        plan_claim_ids=plan_claim_ids,
    )
    packet = packet_override if packet_override is not None else valid_packet
    if second_tier:
        packet = build_research_packet(
            run=valid_packet.run,
            evidence=(
                valid_packet.evidence[0],
                valid_packet.evidence[1].model_copy(update={"authority_tier": 1}),
            ),
            snapshots=valid_packet.market,
            events=valid_packet.events,
            metrics=(replace(valid_packet.metrics[0], input_evidence_ids=("evidence-01",)),),
            gates=valid_packet.gates,
            candidates=valid_packet.candidates,
            exclusions=valid_packet.candidate_exclusions,
            plans=valid_packet.deterministic_plan_inputs,
            capabilities=valid_packet.capability_states,
            observations=valid_packet.prior_plan_observations,
            max_serialized_bytes=valid_packet.synthesis_constraints.max_serialized_bytes,
            regime_result=valid_packet.regime_result,
        )
    return PublishedRunBundle.model_construct(
        run=valid_packet.run,
        bundle=FrozenMap(
            {
                "research_packet": packet.model_dump(mode="json"),
                "brief_draft": draft.model_dump(mode="json"),
            }
        ),
        report_markdown="",
        markdown_sha256=None,
    )


def _publication_receipt(bundle: PublishedRunBundle, published_at: datetime) -> PublishedArtifact:
    return PublishedArtifact(
        run_id=bundle.run.run_id,
        bundle_sha256=sha256(canonical_bytes(bundle)).hexdigest(),
        markdown_sha256="a" * 64,
        published_at=published_at,
    )


def test_selector_covers_available_claim_types_and_two_authority_tiers(
    valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.evaluation.citation_sampling import (
        select_citation_entailment_sample,
    )

    claims = (
        _claim("fact-low", ClaimType.FACT, evidence_ids=("evidence-00",)),
        _claim("fact-plan", ClaimType.FACT, evidence_ids=("evidence-00",), plan_id="plan-1"),
        _claim("fact-unprioritized", ClaimType.FACT, evidence_ids=("evidence-00",)),
        _claim(
            "calculation-high",
            ClaimType.CALCULATION,
            metric_ids=("metric-sma-0123456789abcdef",),
        ),
        _claim("inference-low", ClaimType.INFERENCE, evidence_ids=("evidence-00",)),
        _claim("hypothesis-low", ClaimType.HYPOTHESIS, evidence_ids=("evidence-00",)),
        _claim("orphan", ClaimType.FACT, evidence_ids=("evidence-01",)),
    )
    bundle = _bundle(
        valid_packet,
        valid_brief_draft,
        claims=claims,
        roots=(
            "fact-low",
            "fact-unprioritized",
            "calculation-high",
            "inference-low",
            "hypothesis-low",
        ),
        second_tier=True,
        plan_claim_ids=("fact-plan",),
    )

    sample = select_citation_entailment_sample(bundle)

    selected = {claim.claim_id: claim for claim in claims}
    assert len(sample) == 5
    assert "fact-plan" in sample
    assert "orphan" not in sample
    assert {selected[claim_id].claim_type for claim_id in sample} == set(ClaimType)
    assert len({selected[claim_id].claim_type for claim_id in sample[:4]}) == 4
    sample_evidence_ids = {
        evidence_id
        for claim_id in sample
        for evidence_id in selected[claim_id].evidence_ids
        + selected[claim_id].counter_evidence_ids
    }
    sample_metric_ids = {
        metric_id for claim_id in sample for metric_id in selected[claim_id].metric_ids
    }
    packet = bundle.bundle["research_packet"]
    sample_evidence_ids.update(
        evidence_id
        for metric in packet["metrics"]
        if metric["metric_id"] in sample_metric_ids
        for evidence_id in metric["input_evidence_ids"]
    )
    tiers = {
        evidence["authority_tier"]
        for evidence in packet["evidence"]
        if evidence["evidence_id"] in sample_evidence_ids
    }
    assert len(tiers) >= 2


def test_selector_uses_reachable_claim_graph_and_is_stable(
    valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.evaluation.citation_sampling import (
        select_citation_entailment_sample,
    )

    supporting = _claim("support", ClaimType.FACT, evidence_ids=("evidence-00",))
    root = _claim(
        "root", ClaimType.INFERENCE, supports_claim_ids=("support",)
    )
    orphan = _claim("orphan", ClaimType.HYPOTHESIS, evidence_ids=("evidence-01",))
    bundle = _bundle(
        valid_packet,
        valid_brief_draft,
        claims=(root, supporting, orphan),
        roots=("root",),
    )

    first = select_citation_entailment_sample(bundle)
    second = select_citation_entailment_sample(bundle)

    assert first == second
    assert set(first) == {"root", "support"}
    assert "orphan" not in first


def test_selector_uses_canonical_bundle_hash_for_equal_priority_ties(
    valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.evaluation.citation_sampling import (
        select_citation_entailment_sample,
    )

    claims = (
        _claim("claim-alpha", ClaimType.FACT, evidence_ids=("evidence-00",)),
        _claim("claim-zeta", ClaimType.FACT, evidence_ids=("evidence-00",)),
    )
    bundle = _bundle(
        valid_packet,
        valid_brief_draft,
        claims=claims,
        roots=("claim-alpha", "claim-zeta"),
    )

    assert select_citation_entailment_sample(bundle, maximum_claims=1) == ("claim-zeta",)


def test_selector_preserves_tier_coverage_before_plan_priority(
    valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.evaluation.citation_sampling import (
        select_citation_entailment_sample,
    )

    claims = (
        _claim("priority-fact", ClaimType.FACT, evidence_ids=("evidence-01",)),
        _claim("tier-two-fact", ClaimType.FACT, evidence_ids=("evidence-00",)),
        _claim("tier-one-inference", ClaimType.INFERENCE, evidence_ids=("evidence-01",)),
    )
    bundle = _bundle(
        valid_packet,
        valid_brief_draft,
        claims=claims,
        roots=tuple(claim.claim_id for claim in claims),
        second_tier=True,
        plan_claim_ids=("priority-fact",),
    )

    selected = select_citation_entailment_sample(bundle, maximum_claims=2)

    assert set(selected) == {"tier-two-fact", "tier-one-inference"}
    assert "priority-fact" not in selected


def test_selector_caps_sample_at_five_and_handles_fewer_claims_single_tier(
    valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.evaluation.citation_sampling import (
        select_citation_entailment_sample,
    )

    claims = tuple(
        _claim(f"claim-{index}", ClaimType.FACT, evidence_ids=("evidence-00",))
        for index in range(7)
    )
    many = _bundle(valid_packet, valid_brief_draft, claims=claims)
    few = _bundle(
        valid_packet,
        valid_brief_draft,
        claims=claims[:2],
    )

    assert len(select_citation_entailment_sample(many, maximum_claims=99)) == 5
    assert len(select_citation_entailment_sample(few)) == 2


def test_selector_returns_empty_only_for_brief_without_reachable_material_claims(
    valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.evaluation.citation_sampling import (
        select_citation_entailment_sample,
    )

    empty = _bundle(valid_packet, valid_brief_draft, claims=(), roots=())
    orphan = _bundle(
        valid_packet,
        valid_brief_draft,
        claims=(_claim("orphan", ClaimType.FACT, evidence_ids=("evidence-00",)),),
        roots=(),
    )

    assert select_citation_entailment_sample(empty) == ()
    assert select_citation_entailment_sample(orphan) == ()


def test_review_context_materializes_bounded_bundle_evidence_and_provenance(
    valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.evaluation.citation_sampling import (
        build_citation_entailment_review_sample,
    )

    metric_id = "metric-sma-0123456789abcdef"
    support = _claim(
        "support", ClaimType.FACT, evidence_ids=("evidence-01",)
    )
    root = _claim(
        "root",
        ClaimType.CALCULATION,
        evidence_ids=("evidence-00",),
        metric_ids=(metric_id,),
        counter_evidence_ids=("evidence-01",),
        supports_claim_ids=("support",),
    )
    source = valid_packet.evidence[0].source.model_copy(
        update={"excerpt": "Observed supporting excerpt."}
    )
    evidence_with_excerpt = valid_packet.evidence[0].model_copy(
        update={"source": source}
    )
    packet = build_research_packet(
        run=valid_packet.run,
        evidence=(evidence_with_excerpt, valid_packet.evidence[1]),
        snapshots=valid_packet.market,
        events=valid_packet.events,
        metrics=valid_packet.metrics,
        gates=valid_packet.gates,
        candidates=valid_packet.candidates,
        exclusions=valid_packet.candidate_exclusions,
        plans=valid_packet.deterministic_plan_inputs,
        capabilities=valid_packet.capability_states,
        observations=valid_packet.prior_plan_observations,
        max_serialized_bytes=valid_packet.synthesis_constraints.max_serialized_bytes,
        regime_result=valid_packet.regime_result,
    )
    bundle = _bundle(
        valid_packet,
        valid_brief_draft,
        claims=(root, support),
        roots=("root",),
        packet_override=packet,
    )
    published_at = datetime(2026, 8, 26, 13, 0, tzinfo=UTC)
    publication = _publication_receipt(bundle, published_at)

    sample = build_citation_entailment_review_sample(bundle, publication=publication)

    assert sample.empty_reason is None
    assert set(sample.claim_ids) == {"root", "support"}
    context = next(context for context in sample.contexts if context.claim.claim_id == "root")
    assert context.claim.text == "Claim text for root."
    assert tuple(claim.claim_id for claim in context.supporting_claims) == ("support",)
    assert tuple(item.evidence_id for item in context.cited_evidence) == (
        "evidence-00",
        "evidence-01",
    )
    assert tuple(item.evidence_id for item in context.counter_evidence) == ("evidence-01",)
    assert tuple(metric.metric_id for metric in context.metric_bindings) == (metric_id,)
    assert tuple(item.evidence_id for item in context.metric_input_evidence) == ("evidence-00",)
    assert context.cited_evidence[0].source.excerpt == "Observed supporting excerpt."
    assert context.cited_evidence[0].structured_fields["subject"] == "AAPL"
    assert context.publication_time == publication.published_at
    assert context.evidence_cutoff == valid_packet.run.evidence_cutoff_at


def test_review_context_marks_no_reachable_claims_with_structural_reason(
    valid_packet, valid_brief_draft
) -> None:
    from finance_research_agent.evaluation.citation_sampling import (
        CitationSampleEmptyReason,
        build_citation_entailment_review_sample,
    )

    bundle = _bundle(valid_packet, valid_brief_draft, claims=(), roots=())

    sample = build_citation_entailment_review_sample(
        bundle,
        publication=_publication_receipt(
            bundle, datetime(2026, 8, 26, 13, 0, tzinfo=UTC)
        ),
    )

    assert sample.contexts == ()
    assert sample.claim_ids == ()
    assert sample.empty_reason is CitationSampleEmptyReason.NO_MATERIAL_CLAIMS


def test_review_context_rejects_publication_receipts_not_bound_to_the_bundle(
    valid_packet, valid_brief_draft
) -> None:
    import pytest

    from finance_research_agent.evaluation.citation_sampling import (
        build_citation_entailment_review_sample,
    )

    claim = _claim("claim-1", ClaimType.FACT, evidence_ids=("evidence-00",))
    bundle = _bundle(
        valid_packet,
        valid_brief_draft,
        claims=(claim,),
        roots=("claim-1",),
    )
    publication = _publication_receipt(bundle, datetime(2026, 8, 26, 13, 0, tzinfo=UTC))

    for mismatched_receipt in (
        publication.model_copy(update={"run_id": "premarket-2026-08-27-r1"}),
        publication.model_copy(update={"bundle_sha256": "b" * 64}),
    ):
        with pytest.raises(ValueError, match="matching publication receipt"):
            build_citation_entailment_review_sample(
                bundle, publication=mismatched_receipt
            )


def test_citation_rubric_preserves_human_only_reviews_and_required_context() -> None:
    rubric_path = Path(__file__).resolve().parents[2] / "evals/rubrics/citation-entailment.yaml"
    rubric = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))

    assert rubric["schema_version"] == "0.2"
    assert rubric["selection"]["maximum_claims"] == 5
    assert rubric["selection"]["reachable_claims_only"] is True
    assert set(rubric["verdicts"]) == {"SUPPORTED", "PARTIAL", "UNSUPPORTED"}
    assert rubric["review_requirements"]["no_automatic_verdict"] is True
    assert rubric["review_requirements"]["citation_presence_is_not_entailment"] is True
    assert {
        "exact_claim_text",
        "cited_evidence_with_bounded_excerpts_and_structured_fields",
        "counter_evidence_with_bounded_excerpts_and_structured_fields",
        "authority_tiers",
        "metric_bindings_and_input_evidence_ids",
        "observation_and_publication_times",
        "evidence_cutoff",
    }.issubset(rubric["review_context"]["required"])


def test_selector_rejects_zero_maximum() -> None:
    import pytest

    from finance_research_agent.evaluation.citation_sampling import (
        select_citation_entailment_sample,
    )

    with pytest.raises(ValueError, match="maximum_claims"):
        select_citation_entailment_sample(None, maximum_claims=0)  # type: ignore[arg-type]
