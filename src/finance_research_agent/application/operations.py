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
from finance_research_agent.domain.enums import ReducedReportReason
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    ConfigurationSnapshot,
    Identifier,
    PublishedArtifact,
    PublishedRunBundle,
    StoredRun,
)
from finance_research_agent.domain.policies import WatchlistConfig, WatchlistItem
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


class CitationEntailmentReview(OperationModel):
    citation_id: Identifier
    claim_id: Identifier
    entails_claim: bool


class RecordRunFeedbackRequest(OperationModel):
    run_id: RunId
    clarity_score: Annotated[int, Field(ge=1, le=5)]
    evidence_score: Annotated[int, Field(ge=1, le=5)]
    usefulness_score: Annotated[int, Field(ge=1, le=5)]
    notes: Annotated[str, Field(max_length=1000)] | None = None
    citation_reviews: tuple[CitationEntailmentReview, ...] = Field(default=(), max_length=5)


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
