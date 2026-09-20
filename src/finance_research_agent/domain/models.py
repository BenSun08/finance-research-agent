"""Strict, schema-versioned Product A foundation values."""

import re
import unicodedata
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    model_validator,
)

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

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)

# Unicode 15.1.0 Default_Ignorable_Code_Point ranges, merging adjacent entries:
# https://www.unicode.org/Public/15.1.0/ucd/DerivedCoreProperties.txt
# Keep this explicit rather than rejecting broad Unicode categories: visible
# international text and ordinary combining marks are valid source URL content.
_DEFAULT_IGNORABLE_CODE_POINT_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def _is_default_ignorable(character: str) -> bool:
    code_point = ord(character)
    return any(
        first <= code_point <= last
        for first, last in _DEFAULT_IGNORABLE_CODE_POINT_RANGES
    )


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
    if any(
        character.isspace()
        or unicodedata.category(character) in {"Cc", "Cf"}
        or _is_default_ignorable(character)
        for character in value
    ):
        raise ValueError(
            "source_url must not contain whitespace, Unicode control/format, or "
            "default-ignorable characters"
        )
    parsed = _HTTP_URL_ADAPTER.validate_python(value, strict=True)
    if (
        parsed.scheme != "https"
        or not parsed.host
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("source_url must be an HTTPS URL without credentials")

    # Inspect the original path: URL parsers may already remove dot segments.
    # Query and fragment text are not path segments. This inspection never
    # replaces the caller's accepted URL with a decoded or normalized value.
    authority_and_path = re.split(r"[?#]", value.removeprefix("https://"), maxsplit=1)[0]
    path = authority_and_path.replace("\\", "/").partition("/")[2]
    while True:
        decoded = re.sub(
            r"%[0-9A-Fa-f]{2}",
            lambda match: chr(int(match[0][1:], 16)),
            path,
        )
        if decoded == path:
            break
        # Each replacement shortens the bounded input, so nested percent
        # encodings terminate within the 2,048-character source URL limit.
        path = decoded
    if any(segment in {".", ".."} for segment in path.replace("\\", "/").split("/")):
        raise ValueError("source_url must not contain traversal path segments")
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
    """Immutable configuration identity and optional frozen policy copies."""

    content_hash_sha256: Sha256
    file_hashes: FrozenMap[Identifier, Sha256]
    watchlist_version: Version
    regime_policy_version: Version
    setup_policy_version: Version
    risk_policy_version: Version
    source_policy_version: Version
    policies: FrozenMap[str, JsonValue] | None = None
    policy_hashes: FrozenMap[str, Sha256] | None = None
    radar_universe: tuple[Identifier, ...] = ()


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


class CompletedDailyBar(StrictModel):
    """One completed daily bar with its evidence and provider provenance."""

    instrument_id: Identifier
    session_date: date
    source_timestamp: UtcDatetime
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: Annotated[int, Field(ge=0)] | None
    session: Session
    provider: Identifier
    feed: Identifier
    coverage: Coverage
    adjustment: Identifier
    retrieved_at: UtcDatetime
    evidence_cutoff_at: UtcDatetime
    evidence_id: Identifier
    quality_flags: QualityFlags

    @model_validator(mode="after")
    def _valid_completed_bar(self) -> Self:
        if self.session is not Session.COMPLETED_SESSION:
            raise ValueError("completed daily bars require COMPLETED_SESSION")
        if not self.low <= self.open <= self.high:
            raise ValueError("completed daily-bar open must be within low and high")
        if not self.low <= self.close <= self.high:
            raise ValueError("completed daily-bar close must be within low and high")
        if self.source_timestamp > self.retrieved_at:
            raise ValueError("source_timestamp must not be after retrieved_at")
        if self.source_timestamp > self.evidence_cutoff_at:
            raise ValueError("source_timestamp must not be after evidence_cutoff_at")
        return self


class CurrentSessionBar(StrictModel):
    """Typed current-session bar contract; R2 does not collect these values."""

    instrument_id: Identifier
    session: Session
    start_at: UtcDatetime
    end_at: UtcDatetime
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: Annotated[int, Field(ge=0)] | None
    provider: Identifier
    feed: Identifier
    coverage: Coverage
    retrieved_at: UtcDatetime
    evidence_id: Identifier
    quality_flags: QualityFlags

    @model_validator(mode="after")
    def _valid_current_bar(self) -> Self:
        if self.session is Session.COMPLETED_SESSION:
            raise ValueError("current-session bars cannot use COMPLETED_SESSION")
        if self.start_at > self.end_at:
            raise ValueError("current-session bar start_at must not follow end_at")
        if self.end_at > self.retrieved_at:
            raise ValueError("current-session bar end_at must not follow retrieved_at")
        if not self.low <= self.open <= self.high:
            raise ValueError("current-session bar open must be within low and high")
        if not self.low <= self.close <= self.high:
            raise ValueError("current-session bar close must be within low and high")
        return self


class MarketSnapshot(StrictModel):
    """Canonical Product A market facts for one normalized instrument."""

    instrument: InstrumentIdentity
    latest_price: PriceObservation | None
    completed_daily_bars: tuple[CompletedDailyBar, ...]
    current_session_bars: tuple[CurrentSessionBar, ...]
    source_observations: tuple[SourceObservation, ...]
    quality_flags: QualityFlags

    @model_validator(mode="after")
    def _consistent_instrument_and_history(self) -> Self:
        instrument_id = self.instrument.instrument_id
        if self.latest_price is not None and self.latest_price.instrument_id != instrument_id:
            raise ValueError("latest price must match instrument")
        if any(bar.instrument_id != instrument_id for bar in self.completed_daily_bars):
            raise ValueError("completed bars must match instrument")
        if any(bar.instrument_id != instrument_id for bar in self.current_session_bars):
            raise ValueError("current-session bars must match instrument")
        dates = tuple(bar.session_date for bar in self.completed_daily_bars)
        if any(current >= following for current, following in zip(dates, dates[1:])):
            raise ValueError("completed daily bars must have unique increasing dates")
        observation_ids = tuple(
            observation.observation_id for observation in self.source_observations
        )
        if len(set(observation_ids)) != len(observation_ids):
            raise ValueError("source observation IDs must be unique")
        if (
            self.latest_price is not None
            or self.completed_daily_bars
            or self.current_session_bars
        ) and not self.source_observations:
            raise ValueError("market values require source observations")
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
