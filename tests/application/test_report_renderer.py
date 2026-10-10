from finance_research_agent.application.packet_service import build_research_packet
from finance_research_agent.application.report_renderer import render_markdown_report
from finance_research_agent.domain.enums import (
    Capability,
    ClaimType,
    ValidationCode,
    ValidationSeverity,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import CapabilityState
from finance_research_agent.domain.validation import (
    CapabilityExplanation,
    PlanNarrative,
    ValidationIssue,
    validate_research_brief,
)
from finance_research_agent.evaluation.harness import _source_limitations_adjacent


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


def test_synthesized_report_places_disabled_capability_limitations_in_affected_sections(
    valid_packet, valid_brief_draft
) -> None:
    disabled = {
        Capability.EVENT_RISK_CHECK_AVAILABLE,
        Capability.SETUP_DETECTION_AVAILABLE,
        Capability.PLAN_DRAFT_AVAILABLE,
        Capability.POSITION_SIZING_AVAILABLE,
        Capability.PORTFOLIO_HEAT_CHECK_AVAILABLE,
    }
    capabilities = tuple(
        CapabilityState(
            capability=capability,
            available=capability not in disabled,
            reason_codes=(
                ()
                if capability not in disabled
                else (ErrorCode.SOURCE_NOT_CONFIGURED,)
            ),
            evidence_ids=(),
        )
        for capability in Capability
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
        plans=valid_packet.deterministic_plan_inputs,
        capabilities=capabilities,
        observations=valid_packet.prior_plan_observations,
        max_serialized_bytes=valid_packet.synthesis_constraints.max_serialized_bytes,
    )
    draft = valid_brief_draft.model_copy(
        update={
            "disabled_capability_explanations": tuple(
                CapabilityExplanation(
                    capability=state.capability,
                    reason_codes=tuple(reason.value for reason in state.reason_codes),
                )
                for state in capabilities
                if not state.available
            )
        }
    )
    validation = validate_research_brief(packet, draft, validation_attempt=1)
    assert validation.is_valid is True

    report = render_markdown_report(packet, draft, validation)

    expected = {
        "Today’s Event Clock": (
            "- Limitation: EVENT_RISK_CHECK_AVAILABLE unavailable (SOURCE_NOT_CONFIGURED)."
        ),
        "Macro and Event Calendar": (
            "- Limitation: EVENT_RISK_CHECK_AVAILABLE unavailable (SOURCE_NOT_CONFIGURED)."
        ),
        "Eligible Setups": (
            "- Limitation: SETUP_DETECTION_AVAILABLE unavailable (SOURCE_NOT_CONFIGURED)."
        ),
    }
    for title, limitation in expected.items():
        heading = f"### {title}\n"
        assert heading in report
        section = report.split(heading, 1)[1].split("\n### ", 1)[0]
        assert limitation in section
    data_quality = report.split("### Data Quality and Limitations\n", 1)[1].split(
        "\n### ", 1
    )[0]
    assert (
        "- Disabled capability: EVENT_RISK_CHECK_AVAILABLE (SOURCE_NOT_CONFIGURED)"
        in data_quality
    )
    assert _source_limitations_adjacent(packet, report) is True
    plan_sections = tuple(
        report.split("### Trade Plan Drafts\n")[index].split("\n### ", 1)[0]
        for index in (1, 2)
    )
    assert all(
        "- Limitation: PLAN_DRAFT_AVAILABLE unavailable (SOURCE_NOT_CONFIGURED)." in section
        for section in plan_sections
    )


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


def test_report_renderer_includes_plan_narrative_risk_context(
    valid_packet, valid_brief_draft, valid_trade_plan
) -> None:
    plan = valid_trade_plan.model_copy(
        update={
            "run_id": valid_packet.run.run_id,
            "plan_id": "plan-aapl",
            "counter_evidence": ("evidence-00",),
            "invalidation_condition": "Close < 100 & hold",
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
    hypothesis = valid_brief_draft.claims[0].model_copy(
        update={
            "claim_id": "claim-plan-hypothesis",
            "claim_type": ClaimType.HYPOTHESIS,
            "text": "AAPL may **continue** | test <unsafe>.",
            "field": None,
            "evidence_ids": (),
            "supports_claim_ids": (valid_brief_draft.claims[0].claim_id,),
            "plan_id": plan.plan_id,
            "plan_status": plan.plan_status,
            "counter_evidence_ids": plan.counter_evidence,
            "invalidation": plan.invalidation_condition,
            "expires_at": plan.expires_at,
        }
    )
    draft = valid_brief_draft.model_copy(
        update={
            "claims": (*valid_brief_draft.claims, hypothesis),
            "detailed_sections": (
                valid_brief_draft.detailed_sections[0].model_copy(
                    update={
                        "claim_ids": (
                            *valid_brief_draft.detailed_sections[0].claim_ids,
                            hypothesis.claim_id,
                        )
                    }
                ),
                *valid_brief_draft.detailed_sections[1:],
            ),
            "plan_narratives": (
                PlanNarrative(
                    plan_id=plan.plan_id,
                    text=hypothesis.text,
                    claim_ids=(hypothesis.claim_id,),
                ),
            ),
        }
    )
    validation = validate_research_brief(packet, draft, validation_attempt=1)
    assert validation.is_valid is True

    report = render_markdown_report(packet, draft, validation)

    expected_narrative = (
        "Plan plan-aapl: AAPL may \\*\\*continue\\*\\* \\| test "
        "&lt;unsafe&gt;. [evidence-00]"
    )
    assert expected_narrative in report
    assert f"Plan status: {plan.plan_status.value}" in report
    assert "Counter-evidence: [evidence-00]" in report
    assert "Invalidation: Close &lt; 100 &amp; hold" in report
    assert f"Expires: {plan.expires_at.isoformat()}" in report
