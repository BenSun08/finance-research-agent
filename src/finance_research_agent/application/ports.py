"""Provider-neutral outbound capabilities required by application use cases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from finance_research_agent.domain.enums import InvocationType
from finance_research_agent.domain.market_calendar import TradingCalendar
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    EventCollection,
    InstrumentIdentity,
    MissedRunRecord,
    PriceObservation,
    ProviderFailure,
    ProviderReadiness,
    PublishedArtifact,
    PublishedRunBundle,
    RunCheckpoint,
    RunContext,
    RunContextSeed,
    RunKey,
    RunLease,
    StoredRun,
)
from finance_research_agent.domain.policies import AppConfiguration, WatchlistConfig
from finance_research_agent.market_data.historical import (
    HistoricalBarsFetchResult,
    HistoricalDailyBarsRequest,
)

if TYPE_CHECKING:
    from finance_research_agent.application.feedback_service import RecordedFeedback

__all__ = [
    "Clock",
    "ConfigurationRepository",
    "ConfigurationRepositoryFactory",
    "EventProvider",
    "FeedbackRepository",
    "HistoricalBarsFetcher",
    "MarketDataProvider",
    "ProviderRequestObserver",
    "RunRepository",
    "PublishedArtifactReader",
    "WatchlistRepository",
    "TradingCalendar",
]


class Clock(Protocol):
    """Injectable source of the current UTC instant for deterministic services."""

    def now_utc(self) -> datetime: ...


class MarketCalendarReadinessProvider(TradingCalendar, Protocol):
    """Trading calendar that reports its configured source readiness."""

    def readiness(self) -> ProviderReadiness: ...


class ProviderRequestObserver(Protocol):
    """Secret-free observer for bounded provider transport totals."""

    def record_http_exchange(
        self, adapter: str, request_attempts: int, response_bytes: int
    ) -> None: ...


class MarketDataProvider(Protocol):
    """Provider-neutral Product A market-data collection boundary."""

    def readiness(self) -> ProviderReadiness: ...

    def fetch_instruments(
        self,
        symbols: Sequence[str],
        *,
        telemetry_observer: ProviderRequestObserver | None = None,
    ) -> Mapping[str, InstrumentIdentity | ProviderFailure]: ...

    def fetch_daily_bars(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        expected_sessions: tuple[date, ...] | None = None,
        completed_through_session: date | None = None,
        evidence_cutoff_at: datetime | None = None,
        instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
        telemetry_observer: ProviderRequestObserver | None = None,
    ) -> Mapping[str, tuple[CompletedDailyBar, ...] | ProviderFailure]: ...

    def fetch_premarket_observations(
        self,
        symbols: Sequence[str],
        as_of: datetime | None,
        *,
        instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
        telemetry_observer: ProviderRequestObserver | None = None,
    ) -> Mapping[str, PriceObservation | ProviderFailure]: ...


@runtime_checkable
class EventProvider(Protocol):
    """Provider-neutral collection boundary for bounded event evidence."""

    provider_id: str

    def collect_events(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        cutoff_at: datetime,
    ) -> EventCollection: ...


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
        self,
        seed: RunContextSeed,
        invocation: InvocationType,
        requested_revision: int | None = None,
    ) -> RunContext: ...

    def acquire_lease(self, key: RunKey, now: datetime) -> RunLease: ...

    def heartbeat(self, lease: RunLease, now: datetime) -> RunLease: ...

    def record_missed_run(self, record: MissedRunRecord) -> MissedRunRecord: ...

    def get_missed_run(self, market_date: date) -> MissedRunRecord | None: ...

    def load(self, run_id: str) -> StoredRun | None: ...

    def create(self, context: RunContext) -> None: ...

    def checkpoint(self, run_id: str, checkpoint: RunCheckpoint) -> None: ...

    def checkpoint_if_current(
        self, run_id: str, checkpoint: RunCheckpoint, expected_count: int
    ) -> None: ...

    def stage_artifact(self, run_id: str, artifact_name: str, payload: bytes) -> str: ...

    def read_staged_artifact(self, run_id: str, artifact_name: str) -> bytes | None: ...

    def freeze_evidence(self, run_id: str, cutoff_at: datetime) -> StoredRun: ...

    def publish_atomically(self, bundle: PublishedRunBundle) -> PublishedArtifact: ...

    def get_latest(self, market_date: date) -> str | None: ...

    def get_previous_research_run(self, market_date: date) -> str | None:
        """Find the latest prior date's latest indexed research publication.

        Operational publications without a packet are skipped by date. Corrupt
        indexed publications fail closed rather than selecting older research.
        """
        ...


class PublishedArtifactReader(Protocol):
    """Read-only boundary for replaying an indexed immutable publication."""

    def load_published_bundle(self, run_id: str) -> PublishedRunBundle | None: ...

    def get_report(self, run_id: str) -> str | None: ...

    def get_published_artifact(self, run_id: str) -> PublishedArtifact | None: ...


class FeedbackRepository(Protocol):
    """Append-only local persistence for immutable run feedback records."""

    def append_feedback(self, feedback: RecordedFeedback) -> None: ...

    def list_feedback(self) -> tuple[RecordedFeedback, ...]: ...
