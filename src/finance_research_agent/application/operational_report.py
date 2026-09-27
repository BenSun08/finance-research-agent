"""Safe deterministic report when a run cannot reach synthesis."""

from finance_research_agent.domain.enums import ExecutionStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import RunContext


def render_operational_report(run: RunContext, reason: ErrorCode) -> str:
    """Render a closed failure code without market claims or caller prose."""
    return (
        "# Premarket Operational Report\n"
        "\n"
        "Brief origin: OPERATIONAL\n"
        f"Run ID: {run.run_id}\n"
        f"Market date: {run.market_date.isoformat()}\n"
        f"Execution status: {ExecutionStatus.PUBLISHED.value}\n"
        f"Data quality status: {run.data_quality_status.value}\n"
        f"Delivery status: {run.delivery_status.value}\n"
        f"Failure code: {reason.value}\n"
        "\n"
        "No market conclusion or trade plan is available.\n"
        "Human review is required before any decision.\n"
    )
