"""Strict, schema-versioned Product A foundation values."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, model_validator

from finance_research_agent.domain.enums import (
    Capability,
    Coverage,
    DataQualityStatus,
    DeliveryStatus,
    ExecutionStatus,
    GateStatus,
    RunType,
    Session,
)
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.types import FrozenMap, JsonValue, PositiveDecimal, UtcDatetime

Identifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]
Version = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$")
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Symbol = Annotated[
    str, Field(min_length=1, max_length=16, pattern=r"^[A-Z][A-Z0-9]*([.-][A-Z0-9]+)*$")
]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


def _unique[T](values: tuple[T, ...]) -> tuple[T, ...]:
    if len(set(values)) != len(values):
        raise ValueError("collection entries must be unique")
    return values


EvidenceIds = Annotated[
    tuple[Identifier, ...],
    Field(max_length=256, json_schema_extra={"uniqueItems": True}),
    AfterValidator(_unique),
]
QualityFlags = Annotated[
    tuple[Identifier, ...],
    Field(max_length=64, json_schema_extra={"uniqueItems": True}),
    AfterValidator(_unique),
]
ReasonCodes = Annotated[
    tuple[ErrorCode, ...],
    Field(max_length=64, json_schema_extra={"uniqueItems": True}),
    AfterValidator(_unique),
]


def _source_url(value: str) -> str:
    parsed = HttpUrl(value)
    if (
        parsed.scheme != "https"
        or not parsed.host
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ValueError("source_url must be an HTTPS URL without credentials or whitespace")
    return value


SourceUrl = Annotated[
    str, Field(min_length=1, max_length=2048, pattern=r"^https://"), AfterValidator(_source_url)
]


class StrictModel(BaseModel):
    """Common immutable serialized boundary; Python input uses strict types."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        revalidate_instances="always",
    )

    schema_version: Literal["0.1"] = "0.1"


class ConfigurationSnapshot(StrictModel):
    """Immutable configuration identity only; policy contents are a later slice."""

    content_hash_sha256: Sha256
    file_hashes: FrozenMap[Identifier, Sha256]
    watchlist_version: Version
    regime_policy_version: Version
    setup_policy_version: Version
    risk_policy_version: Version
    source_policy_version: Version


class ComponentVersions(StrictModel):
    """Independent declared component versions, without runtime provenance."""

    core_version: Version
    mcp_contract_version: Version
    plugin_version: Version
    skill_version: Version
    prompt_version: Version
    report_template_version: Version
    schema_versions: FrozenMap[Identifier, Version]
    watchlist_version: Version
    regime_policy_version: Version
    setup_policy_version: Version
    risk_policy_version: Version
    source_policy_version: Version


class RunContext(StrictModel):
    """Immutable identity and orthogonal status snapshot, not a state machine."""

    run_id: Annotated[
        str, Field(max_length=128, pattern=r"^premarket-\d{4}-\d{2}-\d{2}-r[1-9]\d*$")
    ]
    run_type: RunType
    market_date: date
    revision: Annotated[int, Field(gt=0)]
    invoked_at: UtcDatetime
    evidence_cutoff_at: UtcDatetime
    execution_status: ExecutionStatus
    data_quality_status: DataQualityStatus
    delivery_status: DeliveryStatus
    configuration_snapshot: ConfigurationSnapshot
    core_version: Version
    mcp_contract_version: Version
    plugin_version: Version
    skill_version: Version
    prompt_version: Version
    report_template_version: Version
    schema_versions: FrozenMap[Identifier, Version]

    @model_validator(mode="after")
    def _consistent_identity(self) -> Self:
        expected = f"premarket-{self.market_date.isoformat()}-r{self.revision}"
        if self.run_id != expected:
            raise ValueError("run_id must match market_date and revision")
        return self


class SourceObservation(StrictModel):
    """Source provenance with separately retained observation and retrieval times."""

    observation_id: Identifier
    provider: Identifier
    source_url: SourceUrl | None
    source_hash_sha256: Sha256
    observed_at: UtcDatetime
    retrieved_at: UtcDatetime
    content_type: Annotated[str, Field(min_length=1, max_length=128)]
    excerpt: Annotated[str, Field(max_length=8192)]
    persistence_allowed: bool
    quality_flags: QualityFlags

    @model_validator(mode="after")
    def _observation_precedes_retrieval(self) -> Self:
        if self.observed_at > self.retrieved_at:
            raise ValueError("observed_at must not be after retrieved_at")
        return self


class EvidenceItem(StrictModel):
    """Citable bounded evidence; no source-authority or freshness decisions."""

    evidence_id: Identifier
    source: SourceObservation
    authority_tier: Annotated[int, Field(ge=1, le=255)]
    instrument_id: Identifier | None
    event_time: UtcDatetime | None
    published_time: UtcDatetime | None
    structured_fields: FrozenMap[Identifier, JsonValue]
    citation_label: Annotated[str, Field(min_length=1, max_length=256, pattern=r"\S")]


class InstrumentIdentity(StrictModel):
    """Normalized research metadata; unknown classification stays explicit."""

    instrument_id: Identifier
    symbol: Symbol
    name: Annotated[str, Field(min_length=1, max_length=256, pattern=r"\S")]
    instrument_type: Literal["COMMON_STOCK", "ETF", "OTHER", "UNKNOWN"]
    primary_exchange: Identifier | None
    listing_country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None
    currency: Currency
    is_active: bool | None
    is_leveraged: bool | None
    is_inverse: bool | None
    is_otc: bool | None


class PriceObservation(StrictModel):
    """Positive Decimal price paired with its original market-data provenance."""

    instrument_id: Identifier
    value: PositiveDecimal
    currency: Currency
    session: Session
    provider: Identifier
    feed: Identifier
    coverage: Coverage
    observed_at: UtcDatetime
    retrieved_at: UtcDatetime
    evidence_id: Identifier
    quality_flags: QualityFlags

    @model_validator(mode="after")
    def _observation_precedes_retrieval(self) -> Self:
        if self.observed_at > self.retrieved_at:
            raise ValueError("observed_at must not be after retrieved_at")
        return self


class EventRecord(StrictModel):
    """Known or scheduled event with ordered supporting and conflicting evidence."""

    event_id: Identifier
    event_type: Identifier
    subject_symbol: Symbol | None
    event_time: UtcDatetime
    verified: bool
    materiality: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    supporting_evidence_ids: EvidenceIds
    conflict_evidence_ids: EvidenceIds


class GateResult(StrictModel):
    """A recorded gate decision; constructing it performs no policy evaluation."""

    gate_id: Identifier
    status: GateStatus
    reason_code: Identifier
    message: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
    evidence_ids: EvidenceIds
    capability: Capability
    rule_version: Version


class CapabilityState(StrictModel):
    """Explicit availability with mandatory reasons for disabled capabilities."""

    capability: Capability
    available: bool
    reason_codes: ReasonCodes
    evidence_ids: EvidenceIds

    @model_validator(mode="after")
    def _disabled_requires_reason(self) -> Self:
        if not self.available and not self.reason_codes:
            raise ValueError("disabled capability requires at least one reason code")
        return self
