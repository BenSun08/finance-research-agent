"""Deterministic Product A premarket run preparation service."""

from dataclasses import dataclass
from datetime import date

from finance_research_agent import __version__
from finance_research_agent.application.config_service import ConfigService
from finance_research_agent.application.ports import (
    Clock,
    ConfigurationRepository,
    EventProvider,
    MarketDataProvider,
    RunRepository,
)
from finance_research_agent.domain.enums import (
    InvocationType,
)
from finance_research_agent.domain.market_calendar import (
    RunWindowDecision,
    TradingCalendar,
    format_run_id,
    resolve_run_window,
)
from finance_research_agent.domain.models import (
    ComponentVersions,
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
    if not decision.should_run:
        return PreparePremarketRunResult(decision, None)

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
            return PreparePremarketRunResult(decision, stored)

    if decision.delivery_status is None:
        raise RuntimeError("runnable window decision must include delivery status")

    configuration_snapshot = ConfigService(
        dependencies.config_repository
    ).validate_and_snapshot()
    versions = ComponentVersions(
        core_version=__version__,
        mcp_contract_version="0.1",
        plugin_version="0.1",
        skill_version="0.1",
        prompt_version="0.1",
        report_template_version="0.1",
        schema_versions=FrozenMap({"run-context": "0.1"}),
        watchlist_version=configuration_snapshot.watchlist_version,
        regime_policy_version=configuration_snapshot.regime_policy_version,
        setup_policy_version=configuration_snapshot.setup_policy_version,
        risk_policy_version=configuration_snapshot.risk_policy_version,
        source_policy_version=configuration_snapshot.source_policy_version,
    )
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
    return PreparePremarketRunResult(decision, stored)
