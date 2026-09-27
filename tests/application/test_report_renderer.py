from finance_research_agent.application.report_renderer import render_markdown_report
from finance_research_agent.domain.enums import ValidationCode, ValidationSeverity
from finance_research_agent.domain.validation import ValidationIssue, validate_research_brief


def test_report_renderer_is_deterministic_and_keeps_validated_sections_and_citations(
    valid_packet, valid_brief_draft
) -> None:
    validation = validate_research_brief(valid_packet, valid_brief_draft, validation_attempt=1)

    first = render_markdown_report(valid_packet, valid_brief_draft, validation)
    second = render_markdown_report(valid_packet, valid_brief_draft, validation)

    assert first == second
    assert first.startswith("# Premarket Research Brief\n")
    assert "Run ID: premarket-2026-08-26-r1" in first
    assert "## Executive Brief" in first
    assert "## Detailed Report" in first
    assert "### Market Posture" in first
    assert "AAPL has a reported headline. [evidence-00]" in first
    assert (
        "AAPL two-session SMA is 103.00 price. [evidence-00] "
        "[metric-sma-0123456789abcdef]"
    ) in first


def test_report_renderer_rejects_a_validation_report_for_another_packet(
    valid_packet, valid_brief_draft
) -> None:
    validation = validate_research_brief(valid_packet, valid_brief_draft, validation_attempt=1)
    mismatched = validation.model_copy(update={"packet_sha256": "d" * 64})

    try:
        render_markdown_report(valid_packet, valid_brief_draft, mismatched)
    except ValueError as error:
        assert "validation report does not match the frozen packet" in str(error)
    else:
        raise AssertionError("mismatched packet validation must not render")


def test_report_renderer_rejects_an_invalid_draft(
    valid_packet, valid_brief_draft
) -> None:
    validation = validate_research_brief(
        valid_packet, valid_brief_draft, validation_attempt=1
    ).model_copy(
        update={"is_valid": False}
    )

    try:
        render_markdown_report(valid_packet, valid_brief_draft, validation)
    except ValueError as error:
        assert "only a valid draft can be rendered" in str(error)
    else:
        raise AssertionError("invalid drafts must not render")


def test_report_renderer_rejects_a_forged_validation_issue(
    valid_packet, valid_brief_draft
) -> None:
    validation = validate_research_brief(
        valid_packet, valid_brief_draft, validation_attempt=1
    )
    forged = validation.model_copy(
        update={
            "issues": (
                ValidationIssue(
                    issue_id="forged-warning",
                    code=ValidationCode.TEXT_LIMIT_EXCEEDED,
                    severity=ValidationSeverity.WARNING,
                    json_pointer="/claims/0/text",
                    message="unverified warning",
                    related_evidence_ids=(),
                ),
            )
        }
    )

    try:
        render_markdown_report(valid_packet, valid_brief_draft, forged)
    except ValueError as error:
        assert "does not match deterministic validation" in str(error)
    else:
        raise AssertionError("a caller-forged validation report must not render")
