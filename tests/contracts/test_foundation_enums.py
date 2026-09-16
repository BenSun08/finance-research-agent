import pytest

from finance_research_agent.domain.enums import (
    Capability,
    ClaimType,
    Coverage,
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    GateStatus,
    InvocationType,
    PlanStatus,
    RunType,
    Session,
)
from finance_research_agent.domain.errors import ErrorCode


@pytest.mark.parametrize("state", ["APPROVED", "EXECUTED", "UNKNOWN", "draft"])
def test_plan_states_cannot_assert_approval_or_execution(state: str) -> None:
    with pytest.raises(ValueError):
        PlanStatus(state)


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    [
        (InvocationType, {"SCHEDULED", "MANUAL"}),
        (RunType, {"PREMARKET"}),
        (
            ExecutionStatus,
            {
                "CREATED",
                "COLLECTING",
                "NORMALIZING",
                "ANALYZING",
                "AWAITING_SYNTHESIS",
                "VALIDATING",
                "PUBLISHED",
                "FAILED",
                "SKIPPED",
            },
        ),
        (DataQualityStatus, {"PASS", "DEGRADED", "FAIL"}),
        (DeliveryStatus, {"ON_TIME", "DELAYED", "MANUAL", "MISSED_WINDOW"}),
        (Session, {"PRE_MARKET", "REGULAR", "POST_MARKET", "COMPLETED_SESSION"}),
        (Coverage, {"single_exchange", "consolidated", "unknown"}),
        (ClaimType, {"FACT", "CALCULATION", "INFERENCE", "HYPOTHESIS"}),
        (GateStatus, {"PASS", "WARNING", "BLOCK"}),
        (PlanStatus, {"DRAFT", "REVIEW_REQUIRED", "BLOCKED", "EXPIRED"}),
        (
            Capability,
            {
                "MARKET_SUMMARY_AVAILABLE",
                "REGIME_CLASSIFICATION_AVAILABLE",
                "WATCHLIST_METRICS_AVAILABLE",
                "EVENT_RISK_CHECK_AVAILABLE",
                "SETUP_DETECTION_AVAILABLE",
                "PLAN_DRAFT_AVAILABLE",
                "POSITION_SIZING_AVAILABLE",
                "PORTFOLIO_HEAT_CHECK_AVAILABLE",
            },
        ),
    ],
)
def test_closed_vocabulary_matches_the_approved_contract(enum_type: type, expected: set) -> None:
    assert {member.value for member in enum_type} == expected
    with pytest.raises(ValueError):
        enum_type("UNDECLARED")


def test_error_codes_are_closed_typed_product_a_failures() -> None:
    assert ErrorCode("SOURCE_CONFLICT") is ErrorCode.SOURCE_CONFLICT
    assert ErrorCode("PORTFOLIO_HEAT_UNAVAILABLE") is ErrorCode.PORTFOLIO_HEAT_UNAVAILABLE
    with pytest.raises(ValueError):
        ErrorCode("ORDER_REJECTED")
