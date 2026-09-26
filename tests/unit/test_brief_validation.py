from decimal import Decimal

import pytest
from pydantic import ValidationError

from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.domain.validation import (
    PlanNarrative,
    RepairContext,
    ResearchBriefDraft,
    create_repair_context,
    validate_research_brief,
)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("change_numeric_value", "DETERMINISTIC_VALUE_MISMATCH"),
        ("change_numeric_scale", "DETERMINISTIC_VALUE_MISMATCH"),
        ("cite_unrelated_evidence", "IRRELEVANT_CITATION"),
        ("omit_counter_evidence", "COUNTER_EVIDENCE_OMITTED"),
        ("upgrade_degraded_to_pass", "STATUS_OVERCLAIM"),
        ("claim_iex_is_full_market", "IEX_COVERAGE_OVERCLAIM"),
        ("add_buy_imperative", "IMPERATIVE_TRADING_LANGUAGE"),
        ("narrate_blocked_plan_as_actionable", "BLOCKED_PLAN_OVERCLAIM"),
        ("invent_position_size", "UNSUPPORTED_SIZING_VALUE"),
    ],
)
def test_validator_rejects_unsupported_or_overstated_draft(
    valid_packet,
    valid_brief_draft,
    mutate_draft,
    mutation: str,
    code: str,
) -> None:
    draft = mutate_draft(valid_brief_draft, mutation)
    report = validate_research_brief(valid_packet, draft, validation_attempt=1)
    assert report.is_valid is False
    assert code in {issue.code.value for issue in report.issues}


def test_third_repair_is_rejected(valid_packet, valid_brief_draft) -> None:
    with pytest.raises(ValueError, match="at most two repair attempts"):
        validate_research_brief(valid_packet, valid_brief_draft, validation_attempt=4)


def test_valid_draft_preserves_required_english_sections(
    valid_packet,
    valid_brief_draft,
) -> None:
    report = validate_research_brief(valid_packet, valid_brief_draft, validation_attempt=1)
    assert report.is_valid is True
    assert report.issues == ()


def test_repair_context_is_bounded_and_binds_the_frozen_packet(
    valid_packet,
    valid_brief_draft,
) -> None:
    invalid = valid_brief_draft.model_copy(update={"run_id": "premarket-2026-08-25-r1"})
    report = validate_research_brief(valid_packet, invalid, validation_attempt=1)
    repair = create_repair_context(valid_packet, report)
    assert isinstance(repair, RepairContext)
    assert repair.validation_attempt == 2
    assert repair.packet_sha256 == valid_packet.canonical_sha256
    second = validate_research_brief(valid_packet, invalid, validation_attempt=2)
    third = validate_research_brief(valid_packet, invalid, validation_attempt=3)
    with pytest.raises(ValueError, match="at most two repair attempts"):
        create_repair_context(valid_packet, third)
    assert second.repair_attempts_used == 1


def test_draft_is_strict_and_rejects_unknown_fields(valid_brief_draft) -> None:
    with pytest.raises(ValidationError):
        ResearchBriefDraft.model_validate(
            {**valid_brief_draft.model_dump(), "synthesis_metadata": {}},
            strict=True,
        )


def test_plan_narrative_cannot_bypass_imperative_language_validation(
    valid_packet,
    valid_brief_draft,
) -> None:
    draft = valid_brief_draft.model_copy(
        update={
            "plan_narratives": (
                PlanNarrative(
                    plan_id="unknown-plan",
                    text="Buy 10 shares now.",
                    claim_ids=("claim-headline",),
                ),
            ),
        }
    )
    report = validate_research_brief(valid_packet, draft, validation_attempt=1)
    assert "IMPERATIVE_TRADING_LANGUAGE" in {issue.code.value for issue in report.issues}


def test_data_warning_cannot_contain_imperative_trading_language(
    valid_packet,
    valid_brief_draft,
) -> None:
    draft = valid_brief_draft.model_copy(update={"data_warnings": ("Buy this setup now.",)})
    report = validate_research_brief(valid_packet, draft, validation_attempt=1)
    assert "IMPERATIVE_TRADING_LANGUAGE" in {issue.code.value for issue in report.issues}


def test_data_warning_must_repeat_packet_backed_content(valid_packet, valid_brief_draft) -> None:
    draft = valid_brief_draft.model_copy(
        update={"data_warnings": ("The market is guaranteed to rise.",)}
    )
    report = validate_research_brief(valid_packet, draft, validation_attempt=1)
    assert "UNSUPPORTED_CLAIM" in {issue.code.value for issue in report.issues}


def test_fact_cannot_borrow_a_number_from_a_different_structured_field(
    valid_packet,
    valid_brief_draft,
) -> None:
    evidence = valid_packet.evidence[0].model_copy(
        update={
            "structured_fields": FrozenMap(
                {
                    "subject": "AAPL",
                    "field": "price",
                    "value": "100.00",
                    "unit": "price",
                    "volume": "500.00",
                    "volume_unit": "shares",
                }
            )
        }
    )
    packet = build_research_packet(
        run=valid_packet.run,
        evidence=(evidence, valid_packet.evidence[1]),
        snapshots=valid_packet.market,
        events=valid_packet.events,
        metrics=valid_packet.metrics,
        gates=valid_packet.gates,
        candidates=valid_packet.candidates,
        exclusions=valid_packet.candidate_exclusions,
        plans=valid_packet.deterministic_plan_inputs,
        capabilities=valid_packet.capability_states,
        observations=valid_packet.prior_plan_observations,
        max_serialized_bytes=250_000,
    )
    claim = valid_brief_draft.claims[0].model_copy(
        update={
            "field": "price",
            "numeric_value": Decimal("500.00"),
            "unit": "shares",
            "text": "AAPL price is 500.00 shares.",
        }
    )
    draft = valid_brief_draft.model_copy(update={"claims": (claim, *valid_brief_draft.claims[1:])})
    report = validate_research_brief(packet, draft, validation_attempt=1)
    assert "DETERMINISTIC_VALUE_MISMATCH" in {issue.code.value for issue in report.issues}


def test_time_bounded_claim_rejects_evidence_without_fact_timestamp(
    valid_packet,
    valid_brief_draft,
) -> None:
    evidence = valid_packet.evidence[0].model_copy(
        update={"published_time": None, "event_time": None}
    )
    packet = build_research_packet(
        run=valid_packet.run,
        evidence=(evidence, valid_packet.evidence[1]),
        snapshots=valid_packet.market,
        events=valid_packet.events,
        metrics=valid_packet.metrics,
        gates=valid_packet.gates,
        candidates=valid_packet.candidates,
        exclusions=valid_packet.candidate_exclusions,
        plans=valid_packet.deterministic_plan_inputs,
        capabilities=valid_packet.capability_states,
        observations=valid_packet.prior_plan_observations,
        max_serialized_bytes=250_000,
    )
    claim = valid_brief_draft.claims[0].model_copy(
        update={
            "time_start": packet.run.evidence_cutoff_at,
            "time_end": packet.run.evidence_cutoff_at,
        }
    )
    draft = valid_brief_draft.model_copy(update={"claims": (claim, *valid_brief_draft.claims[1:])})
    report = validate_research_brief(packet, draft, validation_attempt=1)
    assert "IRRELEVANT_CITATION" in {issue.code.value for issue in report.issues}


def test_lower_authority_citation_cannot_ignore_matching_higher_authority_evidence(
    valid_packet,
    valid_brief_draft,
) -> None:
    lower_authority = valid_packet.evidence[0].model_copy(update={"authority_tier": 4})
    higher_authority = valid_packet.evidence[1].model_copy(
        update={"instrument_id": "AAPL", "authority_tier": 1}
    )
    packet = build_research_packet(
        run=valid_packet.run,
        evidence=(lower_authority, higher_authority),
        snapshots=valid_packet.market,
        events=valid_packet.events,
        metrics=valid_packet.metrics,
        gates=valid_packet.gates,
        candidates=valid_packet.candidates,
        exclusions=valid_packet.candidate_exclusions,
        plans=valid_packet.deterministic_plan_inputs,
        capabilities=valid_packet.capability_states,
        observations=valid_packet.prior_plan_observations,
        max_serialized_bytes=250_000,
    )
    report = validate_research_brief(packet, valid_brief_draft, validation_attempt=1)
    assert "IRRELEVANT_CITATION" in {issue.code.value for issue in report.issues}


def test_plan_claim_must_preserve_packet_expiry_invalidation_and_counter_evidence(
    valid_packet,
    valid_brief_draft,
    valid_trade_plan,
) -> None:
    plan = valid_trade_plan.model_copy(
        update={
            "run_id": valid_packet.run.run_id,
            "plan_id": "plan-aapl",
            "counter_evidence": ("evidence-00",),
        }
    )
    packet = build_research_packet(
        run=valid_packet.run,
        evidence=valid_packet.evidence,
        snapshots=valid_packet.market,
        events=valid_packet.events,
        metrics=valid_packet.metrics,
        gates=valid_packet.gates,
        candidates=valid_packet.candidates,
        exclusions=valid_packet.candidate_exclusions,
        plans=(plan,),
        capabilities=valid_packet.capability_states,
        observations=valid_packet.prior_plan_observations,
        max_serialized_bytes=250_000,
    )
    claim = valid_brief_draft.claims[0].model_copy(
        update={
            "plan_id": plan.plan_id,
            "plan_status": plan.plan_status,
            "counter_evidence_ids": (),
            "invalidation": None,
            "expires_at": None,
        }
    )
    draft = valid_brief_draft.model_copy(update={"claims": (claim, *valid_brief_draft.claims[1:])})
    report = validate_research_brief(packet, draft, validation_attempt=1)
    codes = {issue.code.value for issue in report.issues}
    assert "COUNTER_EVIDENCE_OMITTED" in codes
    assert "EXPIRY_INVALIDATION_MISSING" in codes


def test_cyclic_claim_support_returns_a_validation_issue(valid_packet, valid_brief_draft) -> None:
    headline, sma = valid_brief_draft.claims
    cyclic_headline = headline.model_copy(update={"supports_claim_ids": (sma.claim_id,)})
    cyclic_sma = sma.model_copy(update={"supports_claim_ids": (headline.claim_id,)})
    draft = valid_brief_draft.model_copy(update={"claims": (cyclic_headline, cyclic_sma)})

    report = validate_research_brief(valid_packet, draft, validation_attempt=1)

    assert "CLAIM_REACHABILITY" in {issue.code.value for issue in report.issues}


def test_numeric_claim_cannot_inherit_binding_from_a_different_field(
    valid_packet,
    valid_brief_draft,
) -> None:
    headline, sma = valid_brief_draft.claims
    price = headline.model_copy(
        update={
            "claim_id": "claim-price",
            "text": "AAPL price is 103.00 price.",
            "field": "price",
            "numeric_value": Decimal("103.00"),
            "unit": "price",
            "evidence_ids": (),
            "supports_claim_ids": (sma.claim_id,),
        }
    )
    draft = valid_brief_draft.model_copy(update={"claims": (price, sma)})

    report = validate_research_brief(valid_packet, draft, validation_attempt=1)

    assert any(
        issue.code.value == "DETERMINISTIC_VALUE_MISMATCH"
        and issue.json_pointer == "/claims/claim-price/numeric_value"
        for issue in report.issues
    )
