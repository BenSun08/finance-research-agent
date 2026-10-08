"""Deterministic Product A premarket run preparation service."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from time import perf_counter_ns

from finance_research_agent.application.component_versions import current_component_versions
from finance_research_agent.application.config_service import ConfigService
from finance_research_agent.application.performance_telemetry import (
    RunTelemetryRecorder,
    checkpoint_with_telemetry,
)
from finance_research_agent.application.ports import (
    Clock,
    ConfigurationRepository,
    EventProvider,
    MarketDataProvider,
    RunRepository,
)
from finance_research_agent.domain.enums import DeliveryStatus, InvocationType
from finance_research_agent.domain.market_calendar import (
    RunWindowDecision,
    TradingCalendar,
    format_run_id,
    resolve_run_window,
)
from finance_research_agent.domain.models import (
    RunCheckpoint,
    RunContextSeed,
    StoredRun,
)
from finance_research_agent.domain.types import FrozenMap


@dataclass(frozen=True, slots=True)
class PreparePremarketRunRequest:
    """Caller intent for preparing one premarket run."""

    market_date: date | None
    requested_revision: int | None
    invocation: InvocationType

    def __post_init__(self) -> None:
        if self.market_date is not None and type(self.market_date) is not date:
            raise TypeError("market_date must be a date or None")
        if self.requested_revision is not None and (
            type(self.requested_revision) is not int or self.requested_revision <= 0
        ):
            raise ValueError("requested_revision must be a positive integer or None")
        if not isinstance(self.invocation, InvocationType):
            raise TypeError("invocation must be a declared InvocationType")


@dataclass(frozen=True, slots=True)
class RunDependencies:
    """Trusted injected ports for one deterministic preparation operation."""

    clock: Clock
    calendar: TradingCalendar
    config_repository: ConfigurationRepository
    run_repository: RunRepository
    market_data: MarketDataProvider
    event_providers: tuple[EventProvider, ...]
    monotonic_ns: Callable[[], int] = perf_counter_ns

    def __post_init__(self) -> None:
        if type(self.event_providers) is not tuple:
            raise TypeError("event_providers must be a tuple")


@dataclass(frozen=True, slots=True)
class PreparePremarketRunResult:
    """Window decision and frozen run state produced by preparation."""

    window_decision: RunWindowDecision
    stored_run: StoredRun | None


def prepare_premarket_run(
    request: PreparePremarketRunRequest,
    dependencies: RunDependencies,
) -> PreparePremarketRunResult:
    """Resolve one premarket request and freeze its first immutable context."""
    if (
        request.invocation is InvocationType.SCHEDULED
        and request.requested_revision is not None
    ):
        raise ValueError("scheduled invocation cannot request a revision")

    invoked_at = dependencies.clock.now_utc()
    decision = resolve_run_window(
        invoked_at,
        dependencies.calendar,
        request.market_date,
        request.invocation,
    )
    existing_revision = (
        1
        if request.invocation is InvocationType.SCHEDULED
        else request.requested_revision
    )
    if existing_revision is not None:
        run_id = format_run_id(decision.market_date, existing_revision)
        stored = dependencies.run_repository.load(run_id)
        if stored is not None:
            if request.invocation is InvocationType.MANUAL and stored.published:
                raise ValueError("cannot resume a published manual revision")
            stored_invocation = (
                InvocationType.MANUAL
                if stored.run.delivery_status is DeliveryStatus.MANUAL
                else InvocationType.SCHEDULED
            )
            stored_decision = resolve_run_window(
                stored.run.invoked_at,
                dependencies.calendar,
                stored.run.market_date,
                stored_invocation,
            )
            if (
                not stored_decision.should_run
                or stored_decision.delivery_status is not stored.run.delivery_status
            ):
                raise RuntimeError("stored run window decision does not match its context")
            if stored.published and stored_decision.publish_missed_report:
                stored_decision = replace(stored_decision, publish_missed_report=False)
            return PreparePremarketRunResult(stored_decision, stored)

    if not decision.should_run:
        return PreparePremarketRunResult(decision, None)

    if decision.delivery_status is None:
        raise RuntimeError("runnable window decision must include delivery status")

    telemetry = RunTelemetryRecorder(monotonic_ns=dependencies.monotonic_ns)
    stage_started_ns = dependencies.monotonic_ns()
    configuration_snapshot = ConfigService(
        dependencies.config_repository
    ).validate_and_snapshot()
    versions = current_component_versions(configuration_snapshot)
    seed = RunContextSeed(
        market_date=decision.market_date,
        invoked_at=invoked_at,
        delivery_status=decision.delivery_status,
        configuration_snapshot=configuration_snapshot,
        component_versions=versions,
    )
    context = dependencies.run_repository.allocate_revision(
        seed,
        request.invocation,
        requested_revision=request.requested_revision,
    )
    stored = dependencies.run_repository.load(context.run_id)
    if stored is None:
        raise RuntimeError("allocated run context could not be reloaded")
    if stored.published:
        return PreparePremarketRunResult(decision, stored)
    elapsed_ns = max(0, dependencies.monotonic_ns() - stage_started_ns)
    telemetry.record_stage_duration_ms("RUN_PREPARATION", elapsed_ns // 1_000_000)
    checkpoint = RunCheckpoint(
        run_id=context.run_id,
        stage="CONFIG_FROZEN",
        execution_status=context.execution_status,
        data_quality_status=context.data_quality_status,
        delivery_status=context.delivery_status,
        written_at=invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({}),
        resumable=True,
    )
    checkpoint = checkpoint_with_telemetry(
        dependencies.run_repository, checkpoint, telemetry.snapshot()
    )
    dependencies.run_repository.checkpoint(context.run_id, checkpoint)
    stored = dependencies.run_repository.load(context.run_id)
    if stored is None:
        raise RuntimeError("prepared run checkpoint could not be reloaded")
    return PreparePremarketRunResult(decision, stored)
