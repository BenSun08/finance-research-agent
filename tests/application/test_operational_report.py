"""Deterministic operational reports for hard failures before synthesis."""

from finance_research_agent.domain.enums import DataQualityStatus
from finance_research_agent.domain.errors import ErrorCode


def test_operational_report_uses_only_run_identity_and_closed_failure_code(
    valid_packet,
) -> None:
    from finance_research_agent.application.operational_report import (
        render_operational_report,
    )

    run = valid_packet.run.model_copy(
        update={"data_quality_status": DataQualityStatus.FAIL}
    )
    report = render_operational_report(run, ErrorCode.MARKET_CALENDAR_UNAVAILABLE)

    assert report == (
        "# Premarket Operational Report\n"
        "\n"
        "Brief origin: OPERATIONAL\n"
        "Run ID: premarket-2026-08-26-r1\n"
        "Market date: 2026-08-26\n"
        "Execution status: PUBLISHED\n"
        "Data quality status: FAIL\n"
        "Delivery status: MANUAL\n"
        "Failure code: MARKET_CALENDAR_UNAVAILABLE\n"
        "\n"
        "No market conclusion or trade plan is available.\n"
        "Human review is required before any decision.\n"
    )
