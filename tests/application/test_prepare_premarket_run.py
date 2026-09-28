import hashlib
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import cast

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.application.config_service import (
    DirectoryConfigurationRepository,
)
from finance_research_agent.application.ports import (
    Clock,
    ConfigurationRepository,
    EventProvider,
    MarketDataProvider,
)
from finance_research_agent.application.run_service import (
    PreparePremarketRunRequest,
    RunDependencies,
    prepare_premarket_run,
)
from finance_research_agent.domain.enums import (
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    InvocationType,
    RunType,
)
from finance_research_agent.domain.market_calendar import TradingCalendar
from finance_research_agent.domain.models import (
    PublishedRunBundle,
    RunContext,
    RunContextSeed,
)

NOW = datetime(2026, 9, 28, 12, 45, tzinfo=UTC)
MARKET_DATE = date(2026, 9, 28)
CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config" / "examples"


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.calls = 0

    def now_utc(self) -> datetime:
        self.calls += 1
        return self.now


class FixedCalendar:
    def __init__(self, trading_days: set[date] | None = None) -> None:
        self.trading_days = trading_days if trading_days is not None else {MARKET_DATE}

    def is_trading_day(self, market_date: date) -> bool:
        return market_date in self.trading_days

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(market_date, time(13, 30), tzinfo=UTC),
            datetime.combine(market_date, time(20), tzinfo=UTC),
        )


class CountingConfigurationRepository:
    def __init__(self, *, fail_on_load: bool = False) -> None:
        self._repository = DirectoryConfigurationRepository(CONFIG_ROOT)
        self.calls = 0
        self.fail_on_load = fail_on_load

    def load(self):
        self.calls += 1
        if self.fail_on_load:
            raise AssertionError("current configuration must not be loaded")
        return self._repository.load()


def _dependencies(
    tmp_path: Path,
    configuration: CountingConfigurationRepository | None = None,
    *,
    clock: FixedClock | None = None,
    trading_days: set[date] | None = None,
) -> tuple[RunDependencies, CountingConfigurationRepository, FixedClock, FileSystemRunRepository]:
    config = configuration or CountingConfigurationRepository()
    fixed_clock = clock or FixedClock()
    run_repository = FileSystemRunRepository(tmp_path)
    return (
        RunDependencies(
            clock=cast(Clock, fixed_clock),
            calendar=cast(TradingCalendar, FixedCalendar(trading_days)),
            config_repository=cast(ConfigurationRepository, config),
            run_repository=run_repository,
            market_data=cast(MarketDataProvider, object()),
            event_providers=cast(tuple[EventProvider, ...], ()),
        ),
        config,
        fixed_clock,
        run_repository,
    )


def _request(
    invocation: InvocationType = InvocationType.SCHEDULED,
    *,
    market_date: date | None = MARKET_DATE,
    requested_revision: int | None = None,
) -> PreparePremarketRunRequest:
    return PreparePremarketRunRequest(
        market_date=market_date,
        requested_revision=requested_revision,
        invocation=invocation,
    )


def test_prepare_scheduled_run_freezes_configuration_once_into_first_context(
    tmp_path: Path,
) -> None:
    dependencies, config, clock, _ = _dependencies(tmp_path)

    result = prepare_premarket_run(_request(), dependencies)

    assert result.window_decision.reason_code == "ON_TIME"
    assert result.stored_run is not None
    run = result.stored_run.run
    assert run.run_id == "premarket-2026-09-28-r1"
    assert run.run_type is RunType.PREMARKET
    assert run.market_date == MARKET_DATE
    assert run.invoked_at == NOW
    assert run.evidence_cutoff_at == NOW
    assert run.delivery_status is DeliveryStatus.ON_TIME
    assert run.execution_status is ExecutionStatus.CREATED
    assert run.data_quality_status is DataQualityStatus.PASS
    assert run.configuration_snapshot.policies is not None
    assert run.configuration_snapshot.file_hashes["watchlist.yaml"]
    assert run.configuration_snapshot.watchlist_version == "1"
    assert run.configuration_snapshot.regime_policy_version == "regime-policy-v1"
    assert run.configuration_snapshot.setup_policy_version == "1"
    assert run.configuration_snapshot.risk_policy_version == "1"
    assert run.configuration_snapshot.source_policy_version == "1"
    assert run.core_version == "0.5.0.dev0"
    assert run.mcp_contract_version == "0.1"
    assert run.plugin_version == "0.1"
    assert run.skill_version == "0.1"
    assert run.prompt_version == "0.1"
    assert run.report_template_version == "0.1"
    assert run.schema_versions["run-context"] == "0.1"
    assert config.calls == 1
    assert clock.calls == 1


def test_duplicate_scheduled_run_reuses_stored_context_without_loading_configuration(
    tmp_path: Path,
) -> None:
    first_dependencies, _, _, _ = _dependencies(tmp_path)
    first = prepare_premarket_run(_request(), first_dependencies)
    assert first.stored_run is not None

    dependencies, config, _, _ = _dependencies(
        tmp_path, CountingConfigurationRepository(fail_on_load=True)
    )
    duplicate = prepare_premarket_run(_request(), dependencies)

    assert duplicate.stored_run == first.stored_run
    assert config.calls == 0


def test_exact_unpublished_manual_revision_resumes_without_loading_configuration(
    tmp_path: Path,
) -> None:
    first_dependencies, _, _, _ = _dependencies(tmp_path)
    first = prepare_premarket_run(
        _request(InvocationType.MANUAL, requested_revision=1), first_dependencies
    )
    assert first.stored_run is not None

    dependencies, config, _, _ = _dependencies(
        tmp_path, CountingConfigurationRepository(fail_on_load=True)
    )
    resumed = prepare_premarket_run(
        _request(InvocationType.MANUAL, requested_revision=1), dependencies
    )

    assert resumed.stored_run == first.stored_run
    assert config.calls == 0


def test_manual_invocations_without_revision_allocate_the_next_number(
    tmp_path: Path,
) -> None:
    dependencies, config, _, _ = _dependencies(tmp_path)

    first = prepare_premarket_run(_request(InvocationType.MANUAL), dependencies)
    second = prepare_premarket_run(_request(InvocationType.MANUAL), dependencies)

    assert first.stored_run is not None
    assert second.stored_run is not None
    assert first.stored_run.run.run_id == "premarket-2026-09-28-r1"
    assert second.stored_run.run.run_id == "premarket-2026-09-28-r2"
    assert first.stored_run.run.delivery_status is DeliveryStatus.MANUAL
    assert second.stored_run.run.delivery_status is DeliveryStatus.MANUAL
    assert config.calls == 2


def test_published_manual_revision_is_rejected_before_loading_configuration(
    tmp_path: Path,
) -> None:
    dependencies, _, _, repository = _dependencies(tmp_path)
    first = prepare_premarket_run(
        _request(InvocationType.MANUAL, requested_revision=1), dependencies
    )
    assert first.stored_run is not None
    report = "# Existing immutable report\n"
    repository.publish_atomically(
        PublishedRunBundle(
            run=first.stored_run.run,
            report_markdown=report,
            markdown_sha256=hashlib.sha256(report.encode("utf-8")).hexdigest(),
        )
    )
    retry_dependencies, config, _, _ = _dependencies(
        tmp_path, CountingConfigurationRepository(fail_on_load=True)
    )

    with pytest.raises(ValueError, match="published manual revision"):
        prepare_premarket_run(
            _request(InvocationType.MANUAL, requested_revision=1), retry_dependencies
        )

    assert config.calls == 0


def test_scheduled_request_for_explicit_revision_fails_before_side_effects(
    tmp_path: Path,
) -> None:
    dependencies, config, clock, _ = _dependencies(tmp_path)

    with pytest.raises(ValueError, match="scheduled invocation cannot request a revision"):
        prepare_premarket_run(
            _request(InvocationType.SCHEDULED, requested_revision=1), dependencies
        )

    assert config.calls == 0
    assert clock.calls == 0


def test_non_runnable_window_returns_without_loading_configuration_or_allocating(
    tmp_path: Path,
) -> None:
    dependencies, config, clock, repository = _dependencies(
        tmp_path,
        clock=FixedClock(datetime(2026, 9, 28, 12, 44, 59, tzinfo=UTC)),
    )

    result = prepare_premarket_run(_request(), dependencies)

    assert result.window_decision.reason_code == "TOO_EARLY"
    assert result.stored_run is None
    assert config.calls == 0
    assert clock.calls == 1
    assert repository._existing_revision_ids(MARKET_DATE) == ()


def test_missed_window_report_run_freezes_missed_delivery_status(tmp_path: Path) -> None:
    dependencies, _, _, _ = _dependencies(
        tmp_path,
        clock=FixedClock(datetime(2026, 9, 28, 13, 30, tzinfo=UTC)),
    )

    result = prepare_premarket_run(_request(), dependencies)

    assert result.window_decision.publish_missed_report is True
    assert result.stored_run is not None
    assert result.stored_run.run.delivery_status is DeliveryStatus.MISSED_WINDOW


def test_result_reloads_state_if_an_existing_run_advances_during_allocation(
    tmp_path: Path,
) -> None:
    class PublishDuringAllocateRepository(FileSystemRunRepository):
        def allocate_revision(
            self,
            seed: RunContextSeed,
            invocation: InvocationType,
            requested_revision: int | None = None,
        ) -> RunContext:
            context = super().allocate_revision(seed, invocation, requested_revision)
            report = "# Concurrently published report\n"
            self.publish_atomically(
                PublishedRunBundle(
                    run=context,
                    report_markdown=report,
                    markdown_sha256=hashlib.sha256(report.encode("utf-8")).hexdigest(),
                )
            )
            return context

    dependencies, _, _, _ = _dependencies(tmp_path)
    repository = PublishDuringAllocateRepository(tmp_path)
    dependencies = RunDependencies(
        clock=dependencies.clock,
        calendar=dependencies.calendar,
        config_repository=dependencies.config_repository,
        run_repository=repository,
        market_data=dependencies.market_data,
        event_providers=dependencies.event_providers,
    )

    result = prepare_premarket_run(_request(), dependencies)

    assert result.stored_run is not None
    assert result.stored_run.published is True


def test_after_close_record_only_result_does_not_allocate_a_run(tmp_path: Path) -> None:
    dependencies, config, _, repository = _dependencies(
        tmp_path,
        clock=FixedClock(datetime(2026, 9, 28, 20, 0, tzinfo=UTC)),
    )

    result = prepare_premarket_run(_request(), dependencies)

    assert result.window_decision.missed_record_only is True
    assert result.stored_run is None
    assert config.calls == 0
    assert repository._existing_revision_ids(MARKET_DATE) == ()
