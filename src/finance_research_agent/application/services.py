"""Trusted composition root for the typed Product A operation boundary."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter_ns
from typing import cast
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from finance_research_agent.application.config_service import ConfigService
from finance_research_agent.application.feedback_service import RunFeedbackService
from finance_research_agent.application.operations import (
    OPERATION_CONTRACTS,
    FeedbackReceipt,
    GetReportRequest,
    GetRunStatusRequest,
    GetSystemStatusRequest,
    ListWatchlistRequest,
    OperationRequest,
    PreparePremarketRunOperationRequest,
    ProductAOperation,
    PublishReducedReportRequest,
    RecordRunFeedbackRequest,
    RemoveWatchlistItemRequest,
    SystemDiagnosticCode,
    SystemStatusResult,
    UpsertWatchlistItemRequest,
    ValidateAndPublishBriefRequest,
    ValidateAndPublishBriefResult,
    ValidateConfigurationRequest,
    validate_operation_request,
)
from finance_research_agent.application.ports import (
    Clock,
    ConfigurationRepository,
    EventProvider,
    FeedbackRepository,
    MarketCalendarReadinessProvider,
    MarketDataProvider,
    PublishedArtifactReader,
    RunRepository,
    WatchlistRepository,
)
from finance_research_agent.application.publication_service import (
    PublicationRepository,
    publish_reduced_report,
    publish_validated_brief,
    validate_staged_brief,
)
from finance_research_agent.application.run_service import (
    PreparePremarketRunRequest,
    RunDependencies,
    prepare_premarket_run,
)
from finance_research_agent.application.watchlist_service import WatchlistService
from finance_research_agent.domain.enums import InvocationType
from finance_research_agent.domain.models import (
    ConfigurationSnapshot,
    PublishedArtifact,
    PublishedRunBundle,
    StoredRun,
)
from finance_research_agent.domain.packets import ResearchPacket


class ApplicationServices:
    """Own service wrappers and dispatch only through the eleven fixed operations."""

    def __init__(
        self,
        *,
        clock: Clock,
        calendar: MarketCalendarReadinessProvider,
        configuration_repository: ConfigurationRepository,
        market_data: MarketDataProvider,
        run_repository: RunRepository,
        published_artifact_reader: PublishedArtifactReader,
        watchlist_repository: WatchlistRepository,
        feedback_repository: FeedbackRepository,
        event_providers: tuple[EventProvider, ...] = (),
        monotonic_ns: Callable[[], int] = perf_counter_ns,
    ) -> None:
        self._clock = clock
        self._calendar = calendar
        self._configuration_repository = configuration_repository
        self._market_data = market_data
        self._run_repository = run_repository
        self._published_artifact_reader = published_artifact_reader
        self._watchlist = WatchlistService(watchlist_repository)
        self._feedback = RunFeedbackService(
            published_artifact_reader, feedback_repository, clock
        )
        self._event_providers = event_providers
        self._monotonic_ns = monotonic_ns
        self._handlers: dict[str, Callable[[OperationRequest], object]] = {
            ProductAOperation.GET_SYSTEM_STATUS.value: self._get_system_status,
            ProductAOperation.VALIDATE_CONFIGURATION.value: self._validate_configuration,
            ProductAOperation.PREPARE_PREMARKET_RUN.value: self._prepare_premarket_run,
            ProductAOperation.GET_RUN_STATUS.value: self._get_run_status,
            ProductAOperation.GET_REPORT.value: self._get_report,
            ProductAOperation.VALIDATE_AND_PUBLISH_BRIEF.value: self._validate_and_publish_brief,
            ProductAOperation.PUBLISH_REDUCED_REPORT.value: self._publish_reduced_report,
            ProductAOperation.LIST_WATCHLIST.value: self._list_watchlist,
            ProductAOperation.UPSERT_WATCHLIST_ITEM.value: self._upsert_watchlist_item,
            ProductAOperation.REMOVE_WATCHLIST_ITEM.value: self._remove_watchlist_item,
            ProductAOperation.RECORD_RUN_FEEDBACK.value: self._record_run_feedback,
        }

    def dispatch(self, operation: ProductAOperation | str, arguments_json: str) -> object:
        """Validate untrusted JSON, invoke one allowlisted service, validate its result."""
        name = operation.value if isinstance(operation, ProductAOperation) else operation
        if name not in OPERATION_CONTRACTS:
            raise ValueError("unknown Product A operation")
        request = validate_operation_request(name, arguments_json)
        result = self._handlers[name](request)
        result_model = OPERATION_CONTRACTS[name].result_model
        return TypeAdapter(result_model).validate_python(result, strict=True)

    def _get_system_status(self, request: OperationRequest) -> SystemStatusResult:
        cast(GetSystemStatusRequest, request)
        diagnostics: list[SystemDiagnosticCode] = []
        configuration_ready = False
        market_data_ready = False
        market_calendar_ready = False
        try:
            ConfigService(self._configuration_repository).validate_and_snapshot()
            configuration_ready = True
        except Exception:
            diagnostics.append(SystemDiagnosticCode.CONFIGURATION_UNAVAILABLE)
        try:
            market_data_ready = self._market_data.readiness().available
            if not market_data_ready:
                diagnostics.append(SystemDiagnosticCode.MARKET_DATA_UNAVAILABLE)
        except Exception:
            diagnostics.append(SystemDiagnosticCode.MARKET_DATA_UNAVAILABLE)
        try:
            market_calendar_ready = self._calendar.readiness().available
            if not market_calendar_ready:
                diagnostics.append(SystemDiagnosticCode.MARKET_CALENDAR_UNAVAILABLE)
        except Exception:
            diagnostics.append(SystemDiagnosticCode.MARKET_CALENDAR_UNAVAILABLE)
        try:
            current_market_date = self._clock.now_utc().astimezone(
                ZoneInfo("America/New_York")
            ).date()
        except Exception:
            current_market_date = None
        return SystemStatusResult(
            configuration_ready=configuration_ready,
            market_data_ready=market_data_ready,
            market_calendar_ready=market_calendar_ready,
            current_market_date=current_market_date,
            diagnostics=tuple(diagnostics),
        )

    def _validate_configuration(self, request: OperationRequest) -> ConfigurationSnapshot:
        cast(ValidateConfigurationRequest, request)
        return ConfigService(self._configuration_repository).validate_and_snapshot()

    def _prepare_premarket_run(
        self, request: OperationRequest
    ) -> object:
        typed = cast(PreparePremarketRunOperationRequest, request)
        return prepare_premarket_run(
            PreparePremarketRunRequest(
                market_date=typed.market_date,
                requested_revision=typed.requested_revision,
                invocation=InvocationType.MANUAL,
            ),
            RunDependencies(
                clock=self._clock,
                calendar=self._calendar,
                config_repository=self._configuration_repository,
                run_repository=self._run_repository,
                market_data=self._market_data,
                event_providers=self._event_providers,
                monotonic_ns=self._monotonic_ns,
            ),
        )

    def _get_run_status(self, request: OperationRequest) -> StoredRun:
        typed = cast(GetRunStatusRequest, request)
        stored = self._run_repository.load(typed.run_id)
        if stored is None:
            raise LookupError("run was not found")
        return stored

    def _get_report(self, request: OperationRequest) -> PublishedRunBundle:
        typed = cast(GetReportRequest, request)
        bundle = self._published_artifact_reader.load_published_bundle(typed.run_id)
        if bundle is None:
            raise LookupError("published report was not found")
        return bundle

    def _load_staged_packet(self, run_id: str) -> ResearchPacket:
        payload = self._run_repository.read_staged_artifact(run_id, "research_packet")
        if payload is None:
            raise LookupError("frozen research packet was not found")
        packet = ResearchPacket.model_validate_json(payload, strict=True)
        if packet.run.run_id != run_id:
            raise ValueError("frozen research packet identity does not match its run")
        return packet

    def _validate_and_publish_brief(
        self, request: OperationRequest
    ) -> ValidateAndPublishBriefResult:
        typed = cast(ValidateAndPublishBriefRequest, request)
        run_id = typed.draft.run_id
        packet = self._load_staged_packet(run_id)
        checkpointed_at = self._clock.now_utc()
        report, _ = validate_staged_brief(
            self._run_repository, packet, typed.draft, checkpointed_at
        )
        if not report.is_valid:
            return ValidateAndPublishBriefResult(validation_report=report)
        artifact = publish_validated_brief(
            cast(PublicationRepository, self._run_repository), packet, checkpointed_at
        )
        return ValidateAndPublishBriefResult(publication=artifact)

    def _publish_reduced_report(self, request: OperationRequest) -> PublishedArtifact:
        typed = cast(PublishReducedReportRequest, request)
        packet = self._load_staged_packet(typed.run_id)
        return publish_reduced_report(
            cast(PublicationRepository, self._run_repository),
            packet,
            typed.reason,
            self._clock.now_utc(),
        )

    def _list_watchlist(self, request: OperationRequest) -> object:
        cast(ListWatchlistRequest, request)
        return self._watchlist.list()

    def _upsert_watchlist_item(self, request: OperationRequest) -> object:
        typed = cast(UpsertWatchlistItemRequest, request)
        return self._watchlist.upsert(typed.expected_version, typed.item)

    def _remove_watchlist_item(self, request: OperationRequest) -> object:
        typed = cast(RemoveWatchlistItemRequest, request)
        return self._watchlist.remove(typed.expected_version, typed.symbol)

    def _record_run_feedback(self, request: OperationRequest) -> FeedbackReceipt:
        typed = cast(RecordRunFeedbackRequest, request)
        return self._feedback.record(typed)


__all__ = ["ApplicationServices"]
