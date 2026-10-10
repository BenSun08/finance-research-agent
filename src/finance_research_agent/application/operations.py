"""Transport-neutral Product A application operation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finance_research_agent.application.premarket_preparation import PreparedPremarketRunResult
from finance_research_agent.application.watchlist_service import WatchlistChange
from finance_research_agent.domain.enums import Capability, ReducedReportReason
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    ConfigurationSnapshot,
    Identifier,
    PublishedArtifact,
    PublishedRunBundle,
    StoredRun,
)
from finance_research_agent.domain.policies import WatchlistConfig, WatchlistItem
from finance_research_agent.domain.regime import Regime
from finance_research_agent.domain.types import UtcDatetime
from finance_research_agent.domain.validation import ResearchBriefDraft, ValidationReport


class OperationModel(BaseModel):
    """Immutable strict model used only at the application operation boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["0.1"] = "0.1"


class ProductAOperation(StrEnum):
    """Closed set of operations approved for the Product A boundary."""

    GET_SYSTEM_STATUS = "get_system_status"
    VALIDATE_CONFIGURATION = "validate_configuration"
    PREPARE_PREMARKET_RUN = "prepare_premarket_run"
    GET_RUN_STATUS = "get_run_status"
    GET_REPORT = "get_report"
    VALIDATE_AND_PUBLISH_BRIEF = "validate_and_publish_brief"
    PUBLISH_REDUCED_REPORT = "publish_reduced_report"
    LIST_WATCHLIST = "list_watchlist"
    UPSERT_WATCHLIST_ITEM = "upsert_watchlist_item"
    REMOVE_WATCHLIST_ITEM = "remove_watchlist_item"
    RECORD_RUN_FEEDBACK = "record_run_feedback"


OPERATION_NAMES = tuple(operation.value for operation in ProductAOperation)


RunId = Annotated[
    str,
    Field(
        min_length=18,
        max_length=128,
        pattern=r"^premarket-\d{4}-\d{2}-\d{2}-r[1-9]\d*$",
    ),
]
WatchlistVersion = Annotated[str, Field(min_length=1, max_length=16, pattern=r"^[1-9]\d*$")]
Ticker = Annotated[
    str,
    Field(
        min_length=1,
        max_length=16,
        pattern=r"^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$",
    ),
]


class GetSystemStatusRequest(OperationModel):
    pass


class ValidateConfigurationRequest(OperationModel):
    pass


class PreparePremarketRunOperationRequest(OperationModel):
    market_date: date | None = None
    requested_revision: Annotated[int, Field(gt=0)] | None = None


class GetRunStatusRequest(OperationModel):
    run_id: RunId


class GetReportRequest(OperationModel):
    run_id: RunId


class ValidateAndPublishBriefRequest(OperationModel):
    draft: ResearchBriefDraft


class PublishReducedReportRequest(OperationModel):
    run_id: RunId
    reason: ReducedReportReason


class ListWatchlistRequest(OperationModel):
    pass


class UpsertWatchlistItemRequest(OperationModel):
    expected_version: WatchlistVersion
    item: WatchlistItem


class RemoveWatchlistItemRequest(OperationModel):
    expected_version: WatchlistVersion
    symbol: Ticker


class CitationVerdict(StrEnum):
    """Human-only result of reviewing citation support for one selected claim."""

    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"


class ExecutiveEventState(StrEnum):
    """Human identification of whether material events were visible in a brief."""

    UNAVAILABLE = "UNAVAILABLE"
    NO_MATERIAL_EVENTS = "NO_MATERIAL_EVENTS"
    MATERIAL_EVENTS = "MATERIAL_EVENTS"


class CitationEntailmentReview(OperationModel):
    schema_version: Literal["0.1", "0.2"] = "0.1"  # type: ignore[assignment]
    citation_id: Identifier
    claim_id: Identifier
    entails_claim: bool
    verdict: CitationVerdict | None = Field(default=None, exclude_if=lambda value: value is None)
    rationale: Annotated[str, Field(min_length=1, max_length=2000)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    reviewed_at: UtcDatetime | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def versioned_full_review(self) -> CitationEntailmentReview:
        details = (self.verdict, self.rationale, self.reviewed_at)
        if self.schema_version == "0.1" and any(value is not None for value in details):
            raise ValueError("citation review schema 0.1 cannot contain full-review details")
        if self.schema_version == "0.2" and any(value is None for value in details):
            raise ValueError("citation review schema 0.2 requires all full-review details")
        if self.verdict is not None:
            if self.entails_claim != (self.verdict is CitationVerdict.SUPPORTED):
                raise ValueError("entails_claim must match whether the verdict is SUPPORTED")
        if self.rationale is not None and (
            not self.rationale.strip()
            or any(not 32 <= ord(character) <= 126 for character in self.rationale)
        ):
            raise ValueError("citation rationale must be non-blank printable English text")
        return self


class _OperationPayloadModel(BaseModel):
    """Strict immutable nested feedback value without a separate wire version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExecutiveIdentification(_OperationPayloadModel):
    """Bounded human identification of executive posture, events, and priorities."""

    market_posture: Regime
    event_state: ExecutiveEventState
    event_ids: tuple[Identifier, ...] = Field(
        max_length=64, json_schema_extra={"uniqueItems": True}
    )
    priority_symbols: tuple[Ticker, ...] = Field(
        max_length=5, json_schema_extra={"uniqueItems": True}
    )
    disabled_capabilities: tuple[Capability, ...] = Field(
        max_length=8, json_schema_extra={"uniqueItems": True}
    )

    @model_validator(mode="after")
    def unique_and_event_consistent(self) -> ExecutiveIdentification:
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("event ids must be unique")
        if len(set(self.priority_symbols)) != len(self.priority_symbols):
            raise ValueError("priority symbols must be unique")
        if len(set(self.disabled_capabilities)) != len(self.disabled_capabilities):
            raise ValueError("disabled capabilities must be unique")
        if self.event_state is ExecutiveEventState.MATERIAL_EVENTS and not self.event_ids:
            raise ValueError("material events require at least one identified event id")
        if self.event_state is not ExecutiveEventState.MATERIAL_EVENTS and self.event_ids:
            raise ValueError("event ids are allowed only when material events are identified")
        return self


class RecordRunFeedbackRequest(OperationModel):
    schema_version: Literal["0.1", "0.2"] = "0.1"  # type: ignore[assignment]
    run_id: RunId
    clarity_score: Annotated[int, Field(ge=1, le=5)]
    evidence_score: Annotated[int, Field(ge=1, le=5)]
    usefulness_score: Annotated[int, Field(ge=1, le=5)]
    notes: Annotated[str, Field(max_length=1000)] | None = None
    citation_reviews: tuple[CitationEntailmentReview, ...] = Field(default=(), max_length=5)
    executive_review_duration_seconds: Annotated[int, Field(ge=0, le=86400)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    detailed_review_duration_seconds: Annotated[int, Field(ge=0, le=86400)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    executive_identification: ExecutiveIdentification | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def feedback_schema_matches_extensions(self) -> RecordRunFeedbackRequest:
        if self.schema_version == "0.1":
            has_extensions = any(
                value is not None
                for value in (
                    self.executive_review_duration_seconds,
                    self.detailed_review_duration_seconds,
                    self.executive_identification,
                )
            ) or any(review.schema_version != "0.1" for review in self.citation_reviews)
            if has_extensions:
                raise ValueError("feedback request schema 0.1 cannot contain schema 0.2 fields")
        return self


class SystemDiagnosticCode(StrEnum):
    CONFIGURATION_UNAVAILABLE = "CONFIGURATION_UNAVAILABLE"
    MARKET_DATA_UNAVAILABLE = "MARKET_DATA_UNAVAILABLE"
    MARKET_CALENDAR_UNAVAILABLE = "MARKET_CALENDAR_UNAVAILABLE"


class SystemStatusResult(OperationModel):
    configuration_ready: bool
    market_data_ready: bool
    market_calendar_ready: bool
    current_market_date: date | None
    diagnostics: tuple[SystemDiagnosticCode, ...] = Field(max_length=64)


class ValidateAndPublishBriefResult(OperationModel):
    validation_report: ValidationReport | None = None
    publication: PublishedArtifact | None = None

    @model_validator(mode="after")
    def exactly_one_result(self) -> ValidateAndPublishBriefResult:
        if (self.validation_report is None) == (self.publication is None):
            raise ValueError("exactly one validation or publication result is required")
        return self


class FeedbackReceipt(OperationModel):
    feedback_id: Identifier
    run_id: RunId
    recorded_at: UtcDatetime


class OperationError(OperationModel):
    """Closed public failure code; provider and exception text is never carried."""

    code: ErrorCode


@dataclass(frozen=True, slots=True)
class OperationContract:
    """Typed request/result pairing for one fixed application operation."""

    name: ProductAOperation
    request_model: type[OperationModel]
    result_model: type[object]
    error_model: type[OperationModel] = OperationError


_CONTRACTS: dict[str, OperationContract] = {
    ProductAOperation.GET_SYSTEM_STATUS.value: OperationContract(
        ProductAOperation.GET_SYSTEM_STATUS, GetSystemStatusRequest, SystemStatusResult
    ),
    ProductAOperation.VALIDATE_CONFIGURATION.value: OperationContract(
        ProductAOperation.VALIDATE_CONFIGURATION,
        ValidateConfigurationRequest,
        ConfigurationSnapshot,
    ),
    ProductAOperation.PREPARE_PREMARKET_RUN.value: OperationContract(
        ProductAOperation.PREPARE_PREMARKET_RUN,
        PreparePremarketRunOperationRequest,
        PreparedPremarketRunResult,
    ),
    ProductAOperation.GET_RUN_STATUS.value: OperationContract(
        ProductAOperation.GET_RUN_STATUS, GetRunStatusRequest, StoredRun
    ),
    ProductAOperation.GET_REPORT.value: OperationContract(
        ProductAOperation.GET_REPORT, GetReportRequest, PublishedRunBundle
    ),
    ProductAOperation.VALIDATE_AND_PUBLISH_BRIEF.value: OperationContract(
        ProductAOperation.VALIDATE_AND_PUBLISH_BRIEF,
        ValidateAndPublishBriefRequest,
        ValidateAndPublishBriefResult,
    ),
    ProductAOperation.PUBLISH_REDUCED_REPORT.value: OperationContract(
        ProductAOperation.PUBLISH_REDUCED_REPORT,
        PublishReducedReportRequest,
        PublishedArtifact,
    ),
    ProductAOperation.LIST_WATCHLIST.value: OperationContract(
        ProductAOperation.LIST_WATCHLIST, ListWatchlistRequest, WatchlistConfig
    ),
    ProductAOperation.UPSERT_WATCHLIST_ITEM.value: OperationContract(
        ProductAOperation.UPSERT_WATCHLIST_ITEM,
        UpsertWatchlistItemRequest,
        WatchlistChange,
    ),
    ProductAOperation.REMOVE_WATCHLIST_ITEM.value: OperationContract(
        ProductAOperation.REMOVE_WATCHLIST_ITEM,
        RemoveWatchlistItemRequest,
        WatchlistChange,
    ),
    ProductAOperation.RECORD_RUN_FEEDBACK.value: OperationContract(
        ProductAOperation.RECORD_RUN_FEEDBACK,
        RecordRunFeedbackRequest,
        FeedbackReceipt,
    ),
}
OPERATION_CONTRACTS = MappingProxyType(_CONTRACTS)

_OPERATION_SCHEMA_MODELS: dict[str, type[Any]] = {
    **{
        f"product-a-operation-{name.replace('_', '-')}-request.schema.json": (
            contract.request_model
        )
        for name, contract in OPERATION_CONTRACTS.items()
    },
    **{
        f"product-a-operation-{name.replace('_', '-')}-result.schema.json": (
            contract.result_model
        )
        for name, contract in OPERATION_CONTRACTS.items()
    },
    "product-a-citation-entailment-review.schema.json": CitationEntailmentReview,
    "product-a-feedback-receipt.schema.json": FeedbackReceipt,
    "product-a-operation-error.schema.json": OperationError,
}
OPERATION_SCHEMA_MODELS = MappingProxyType(_OPERATION_SCHEMA_MODELS)


type OperationRequest = (
    GetSystemStatusRequest
    | ValidateConfigurationRequest
    | PreparePremarketRunOperationRequest
    | GetRunStatusRequest
    | GetReportRequest
    | ValidateAndPublishBriefRequest
    | PublishReducedReportRequest
    | ListWatchlistRequest
    | UpsertWatchlistItemRequest
    | RemoveWatchlistItemRequest
    | RecordRunFeedbackRequest
)


def validate_operation_request(operation_name: str, arguments_json: str) -> OperationRequest:
    """Validate serialized MCP arguments against one exact operation schema."""
    if type(operation_name) is not str or type(arguments_json) is not str:
        raise TypeError("operation name and serialized arguments must be strings")
    if len(arguments_json) > 1_000_000:
        raise ValueError("operation arguments exceed the maximum payload size")
    try:
        operation = ProductAOperation(operation_name)
    except ValueError:
        raise ValueError("unknown Product A operation") from None
    contract = OPERATION_CONTRACTS[operation.value]
    return cast(
        OperationRequest,
        contract.request_model.model_validate_json(arguments_json, strict=True),
    )
