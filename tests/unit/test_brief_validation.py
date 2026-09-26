import pytest
from pydantic import ValidationError

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
