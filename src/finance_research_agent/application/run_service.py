"""Deterministic Product A run preparation entrypoint contracts."""

from dataclasses import dataclass
from datetime import date

from finance_research_agent.application.ports import (
    Clock,
    ConfigurationRepository,
    EventProvider,
    MarketDataProvider,
    RunRepository,
)
from finance_research_agent.domain.enums import InvocationType
from finance_research_agent.domain.market_calendar import TradingCalendar


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
