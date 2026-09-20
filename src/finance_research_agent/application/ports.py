"""Provider-neutral outbound capabilities required by application use cases."""

from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from finance_research_agent.domain.enums import InvocationType
from finance_research_agent.domain.market_calendar import TradingCalendar
from finance_research_agent.domain.models import (
    PublishedArtifact,
    PublishedRunBundle,
    RunCheckpoint,
    RunContext,
    RunKey,
    RunLease,
    StoredRun,
)
from finance_research_agent.domain.policies import AppConfiguration, WatchlistConfig
from finance_research_agent.market_data.historical import (
    HistoricalBarsFetchResult,
    HistoricalDailyBarsRequest,
)

__all__ = [
    "ConfigurationRepository",
    "ConfigurationRepositoryFactory",
    "HistoricalBarsFetcher",
    "RunRepository",
    "WatchlistRepository",
    "TradingCalendar",
]


class HistoricalBarsFetcher(Protocol):
    """Fetch completed historical daily bars for one provider-neutral request."""

    def fetch_daily_bars(
        self,
        request: HistoricalDailyBarsRequest,
    ) -> HistoricalBarsFetchResult: ...


class ConfigurationRepository(Protocol):
    """Provider-neutral source of the five validated local policies."""

    def load(self) -> AppConfiguration: ...


class ConfigurationRepositoryFactory(Protocol):
    """Injected composition boundary for a trusted configuration directory."""

    def __call__(self, source: Path, staging_root: Path) -> ConfigurationRepository: ...


class WatchlistRepository(Protocol):
    """Provider-neutral read/replace boundary for the watchlist file."""

    def load(self) -> WatchlistConfig: ...

    def replace(self, configuration: WatchlistConfig) -> None: ...

    def replace_if_version(self, expected_version: str, configuration: WatchlistConfig) -> bool: ...


class RunRepository(Protocol):
    """Provider-neutral boundary for immutable local run state."""

    def allocate_revision(
        self, market_date: date, invocation: InvocationType, now: datetime
    ) -> RunContext: ...

    def acquire_lease(self, key: RunKey, now: datetime) -> RunLease: ...

    def heartbeat(self, lease: RunLease, now: datetime) -> RunLease: ...

    def load(self, run_id: str) -> StoredRun | None: ...

    def create(self, context: RunContext) -> None: ...

    def checkpoint(self, run_id: str, checkpoint: RunCheckpoint) -> None: ...

    def freeze_evidence(self, run_id: str, cutoff_at: datetime) -> StoredRun: ...

    def publish_atomically(self, bundle: PublishedRunBundle) -> PublishedArtifact: ...

    def get_latest(self, market_date: date) -> str | None: ...
