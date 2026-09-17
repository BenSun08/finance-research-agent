"""Point-in-time identity, provenance, and visible capability state contracts."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

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
from finance_research_agent.domain.models import (
    CapabilityState,
    ComponentVersions,
    ConfigurationSnapshot,
    EventRecord,
    EvidenceItem,
    GateResult,
    InstrumentIdentity,
    PriceObservation,
    RunContext,
    SourceObservation,
    StrictModel,
)
from finance_research_agent.domain.types import FrozenMap, canonical_bytes

NOW = datetime(2026, 9, 16, 12, 45, tzinfo=UTC)
POLICY_VERSIONS = {
    "watchlist_version": "3",
    "regime_policy_version": "1",
    "setup_policy_version": "2",
    "risk_policy_version": "1",
    "source_policy_version": "4",
}
COMPONENT_VERSIONS = {
    "core_version": "0.5.0.dev0",
    "mcp_contract_version": "0.1",
    "plugin_version": "0.1",
    "skill_version": "0.1",
    "prompt_version": "0.1",
    "report_template_version": "0.1",
    "schema_versions": FrozenMap({"run-context": "0.1", "source-observation": "0.1"}),
}


def configuration() -> ConfigurationSnapshot:
    return ConfigurationSnapshot.model_validate(
        {
            **POLICY_VERSIONS,
            "content_hash_sha256": "a" * 64,
            "file_hashes": FrozenMap({"watchlist.yaml": "b" * 64}),
        }
    )


def run(**changes: object) -> RunContext:
    return RunContext.model_validate(
        {
            **COMPONENT_VERSIONS,
            "run_id": "premarket-2026-09-16-r2",
            "run_type": RunType.PREMARKET,
            "market_date": date(2026, 9, 16),
            "revision": 2,
            "invoked_at": NOW,
            "evidence_cutoff_at": NOW + timedelta(minutes=10),
            "execution_status": ExecutionStatus.PUBLISHED,
            "data_quality_status": DataQualityStatus.DEGRADED,
            "delivery_status": DeliveryStatus.DELAYED,
            "configuration_snapshot": configuration(),
            **changes,
        }
    )


def source(**changes: object) -> SourceObservation:
    return SourceObservation.model_validate(
        {
            "observation_id": "obs_official_1",
            "provider": "synthetic",
            "source_url": "https://example.test/news?id=1",
            "source_hash_sha256": "c" * 64,
            "observed_at": NOW,
            "retrieved_at": NOW + timedelta(seconds=2),
            "content_type": "application/json",
            "excerpt": "Synthetic official source excerpt.",
            "persistence_allowed": False,
            "quality_flags": (),
            **changes,
        }
    )


def evidence(**changes: object) -> EvidenceItem:
    return EvidenceItem.model_validate(
        {
            "evidence_id": "ev_1",
            "source": source(),
            "authority_tier": 1,
            "instrument_id": "AAPL",
            "event_time": None,
            "published_time": None,
            "structured_fields": FrozenMap({"headline": "Synthetic fact", "verified": True}),
            "citation_label": "Official synthetic source",
            **changes,
        }
    )


def price(**changes: object) -> PriceObservation:
    return PriceObservation.model_validate(
        {
            "instrument_id": "AAPL",
            "value": Decimal("192.34"),
            "currency": "USD",
            "session": Session.PRE_MARKET,
            "provider": "alpaca",
            "feed": "iex",
            "coverage": Coverage.SINGLE_EXCHANGE,
            "observed_at": NOW,
            "retrieved_at": NOW + timedelta(seconds=2),
            "evidence_id": "ev_price",
            "quality_flags": ("FEED_IEX_SINGLE_EXCHANGE",),
            **changes,
        }
    )


def event(**changes: object) -> EventRecord:
    return EventRecord.model_validate(
        {
            "event_id": "event_1",
            "event_type": "EARNINGS",
            "subject_symbol": "AAPL",
            "event_time": NOW + timedelta(days=2),
            "verified": True,
            "materiality": "HIGH",
            "supporting_evidence_ids": ("ev_2", "ev_1"),
            "conflict_evidence_ids": ("ev_conflict",),
            **changes,
        }
    )


def gate(**changes: object) -> GateResult:
    return GateResult.model_validate(
        {
            "gate_id": "gate_sources",
            "status": GateStatus.BLOCK,
            "reason_code": "SOURCE_CONFLICT",
            "message": "Conflicting synthetic evidence requires review.",
            "evidence_ids": ("ev_2", "ev_1"),
            "capability": Capability.PLAN_DRAFT_AVAILABLE,
            "rule_version": "1",
            **changes,
        }
    )


def capability(**changes: object) -> CapabilityState:
    return CapabilityState.model_validate(
        {
            "capability": Capability.PLAN_DRAFT_AVAILABLE,
            "available": False,
            "reason_codes": (ErrorCode.SOURCE_CONFLICT,),
            "evidence_ids": ("ev_2", "ev_1"),
            **changes,
        }
    )


def test_run_identity_retains_three_independent_status_dimensions() -> None:
    value = run()
    assert value.run_id == "premarket-2026-09-16-r2"
    assert value.execution_status is ExecutionStatus.PUBLISHED
    assert value.data_quality_status is DataQualityStatus.DEGRADED
    assert value.delivery_status is DeliveryStatus.DELAYED


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": "premarket-2026-09-15-r2"},
        {"run_id": "premarket-2026-09-16-r1"},
        {"run_id": "premarket-2026-09-16-r02"},
        {"run_id": "premarket-2026-09-16-r0", "revision": 0},
        {"run_id": "intraday-2026-09-16-r2"},
        {"revision": True},
        {"revision": "2"},
        {"market_date": "2026-09-16"},
        {"execution_status": "PUBLISHED"},
        {"data_quality_status": DeliveryStatus.DELAYED},
        {"delivery_status": DataQualityStatus.PASS},
        {"core_version": ""},
        {"schema_versions": FrozenMap({"run-context": ""})},
    ],
)
def test_run_rejects_inconsistent_identity_and_invalid_versions_or_states(changes: dict) -> None:
    with pytest.raises(ValidationError):
        run(**changes)


def test_independent_component_versions_are_retained_with_policy_snapshot() -> None:
    value = ComponentVersions.model_validate({**COMPONENT_VERSIONS, **POLICY_VERSIONS})
    assert value.core_version == "0.5.0.dev0"
    assert value.source_policy_version == "4"
    assert value.risk_policy_version == "1"
    assert value.schema_versions["source-observation"] == "0.1"
    assert run().configuration_snapshot.watchlist_version == "3"
    with pytest.raises(TypeError):
        value.schema_versions["source-observation"] = "2"


@pytest.mark.parametrize(
    "changes",
    [
        {"observation_id": ""},
        {"observation_id": "x" * 129},
        {"observation_id": "../source"},
        {"observation_id": "obs_é"},
        {"source_hash_sha256": "z" * 64},
        {"source_hash_sha256": "a" * 63},
        {"observed_at": NOW + timedelta(seconds=3)},
        {"source_url": "javascript:alert(1)"},
        {"source_url": "https://user:password@example.test/source"},
        {"excerpt": "x" * 8193},
        {"quality_flags": ["SOURCE_CONFLICT"]},
        {"quality_flags": ("SOURCE_CONFLICT", "SOURCE_CONFLICT")},
        {"persistence_allowed": 1},
    ],
)
def test_source_rejects_invalid_provenance_and_mutable_or_duplicate_flags(changes: dict) -> None:
    with pytest.raises(ValidationError):
        source(**changes)


def test_source_preserves_original_url_and_equal_observation_retrieval_times() -> None:
    value = source(retrieved_at=NOW, source_url="https://example.test")
    assert value.source_url == "https://example.test"
    assert value.observed_at is NOW
    assert value.retrieved_at is NOW


@pytest.mark.parametrize("character", ["\u200b", "\u202e", "\x7f", "\u2066"])
@pytest.mark.parametrize("suffix", ["/filing{}", "/filing?q={}", "/filing#{}"])
def test_source_url_rejects_unicode_control_and_format_characters(
    character: str, suffix: str
) -> None:
    with pytest.raises(ValidationError):
        source(source_url="https://example.test" + suffix.format(character))


@pytest.mark.parametrize(
    "path",
    [
        "../filing",
        "section/../filing",
        "./filing",
        "section/.",
        "section/..",
        "section/%2e%2e/filing",
        "section/.%2E/filing",
        "section/%2e./filing",
        "section/%2E/filing",
        "section/%2e",
        "section/%2e%2e",
        "%2e%2e%2ffiling",
        "section%2F..%2ffiling",
        "section%5c..%5cfiling",
        r"section\..\filing",
        "section/%252e%252e/filing",
        "section/%252E%252e%252Ffiling",
    ],
)
def test_source_url_rejects_literal_and_percent_encoded_dot_segments(path: str) -> None:
    with pytest.raises(ValidationError):
        source(source_url="https://example.test/" + path)


@pytest.mark.parametrize(
    "url",
    [
        "https://reports.example.test/v1.2/company.name/filing.html"
        "?period=2026.09&redirect=../archive#section.1",
        "https://example.test/v1%2E2/company%2ename/filing.json?q=./notes",
    ],
)
def test_source_url_preserves_ordinary_dots_paths_and_query_text(url: str) -> None:
    assert source(source_url=url).source_url == url


def test_evidence_has_explicit_source_and_does_not_invent_event_or_publication_times() -> None:
    value = evidence()
    assert value.source.observation_id == "obs_official_1"
    assert value.source.retrieved_at == NOW + timedelta(seconds=2)
    assert value.event_time is None
    assert value.published_time is None
    scheduled = NOW + timedelta(days=2)
    published = NOW - timedelta(hours=1)
    known_future = evidence(event_time=scheduled, published_time=published)
    assert known_future.event_time == scheduled
    assert known_future.published_time == published


@pytest.mark.parametrize("changes", [{"source": "obs_1"}, {"authority_tier": 0}])
def test_evidence_rejects_missing_source_shape_and_invalid_authority(changes: dict) -> None:
    with pytest.raises(ValidationError):
        evidence(**changes)


def test_price_retains_decimal_and_complete_provenance() -> None:
    value = price()
    assert value.value == Decimal("192.34")
    assert isinstance(value.value, Decimal)
    assert value.provider == "alpaca"
    assert value.feed == "iex"
    assert value.coverage is Coverage.SINGLE_EXCHANGE
    assert value.session is Session.PRE_MARKET
    assert value.observed_at is NOW
    assert value.evidence_id == "ev_price"
    assert value.quality_flags == ("FEED_IEX_SINGLE_EXCHANGE",)


@pytest.mark.parametrize(
    "changes",
    [
        {"value": Decimal("0")},
        {"value": Decimal("-1")},
        {"value": Decimal("NaN")},
        {"value": 192.34},
        {"currency": "usd"},
        {"coverage": "single_exchange"},
        {"retrieved_at": NOW - timedelta(seconds=1)},
        {"quality_flags": ("STALE_DATA", "STALE_DATA")},
    ],
)
def test_price_rejects_invalid_value_provenance_or_temporal_order(changes: dict) -> None:
    with pytest.raises(ValidationError):
        price(**changes)


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (event, "supporting_evidence_ids"),
        (event, "conflict_evidence_ids"),
        (gate, "evidence_ids"),
        (capability, "evidence_ids"),
    ],
)
def test_evidence_references_preserve_order_and_reject_duplicates_and_lists(factory, field) -> None:
    assert getattr(factory(**{field: ("ev_z", "ev_a")}), field) == ("ev_z", "ev_a")
    for references in [("ev_a", "ev_a"), ["ev_a"], ("",)]:
        with pytest.raises(ValidationError):
            factory(**{field: references})


def test_disabled_capability_requires_explicit_stable_reasons() -> None:
    assert capability().reason_codes == (ErrorCode.SOURCE_CONFLICT,)
    with pytest.raises(ValidationError):
        capability(reason_codes=())
    with pytest.raises(ValidationError):
        capability(reason_codes=(ErrorCode.STALE_DATA, ErrorCode.STALE_DATA))
    assert capability(available=True, reason_codes=()).available is True


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (run, "invoked_at"),
        (run, "evidence_cutoff_at"),
        (source, "observed_at"),
        (source, "retrieved_at"),
        (evidence, "event_time"),
        (evidence, "published_time"),
        (price, "observed_at"),
        (price, "retrieved_at"),
        (event, "event_time"),
    ],
)
def test_every_machine_timestamp_rejects_non_utc_input(factory, field) -> None:
    for timestamp in [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=8)))]:
        with pytest.raises(ValidationError, match="UTC"):
            factory(**{field: timestamp})


def instrument(**changes: object) -> InstrumentIdentity:
    return InstrumentIdentity.model_validate(
        {
            "instrument_id": "AAPL",
            "symbol": "AAPL",
            "name": "Synthetic company",
            "instrument_type": "COMMON_STOCK",
            "primary_exchange": "XNAS",
            "listing_country": "US",
            "currency": "USD",
            "is_active": True,
            "is_leveraged": False,
            "is_inverse": False,
            "is_otc": False,
            **changes,
        }
    )


def test_instrument_identity_contains_only_bounded_normalized_research_metadata() -> None:
    value = instrument()
    assert value.instrument_id == "AAPL"
    assert value.instrument_type == "COMMON_STOCK"
    assert value.primary_exchange == "XNAS"
    assert instrument(is_leveraged=None).is_leveraged is None
    for changes in [
        {"symbol": "aapl"},
        {"symbol": "../AAPL"},
        {"instrument_type": "OPTION"},
        {"buying_power": Decimal("1000")},
        {"position": 1},
        {"account_id": "private"},
    ]:
        with pytest.raises(ValidationError):
            instrument(**changes)


@pytest.mark.parametrize(
    "factory", [run, source, evidence, price, event, gate, capability, instrument]
)
def test_public_records_use_frozen_strict_model_and_round_trip_without_drift(factory) -> None:
    value = factory()
    assert isinstance(value, StrictModel)
    assert canonical_bytes(type(value).model_validate_json(canonical_bytes(value))) == (
        canonical_bytes(value)
    )
    with pytest.raises(ValidationError):
        factory(unexpected="rejected")
    with pytest.raises(ValidationError, match="frozen"):
        value.schema_version = "0.1"
