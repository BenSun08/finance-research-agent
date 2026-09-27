"""Pure composition helpers for immutable Product A run snapshots."""

from finance_research_agent.domain.models import RunCheckpoint, RunContext


def compose_publication_context(run: RunContext, checkpoint: RunCheckpoint) -> RunContext:
    """Build a publication snapshot using the final checkpoint's status values."""
    if checkpoint.run_id != run.run_id:
        raise ValueError("checkpoint run_id must match the publication RunContext")
    return run.model_copy(
        update={
            "execution_status": checkpoint.execution_status,
            "data_quality_status": checkpoint.data_quality_status,
            "delivery_status": checkpoint.delivery_status,
        }
    )
