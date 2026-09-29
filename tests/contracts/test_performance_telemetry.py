import pytest
from pydantic import ValidationError

from finance_research_agent.domain import models
from finance_research_agent.domain.types import FrozenMap


def _telemetry_model():
    assert hasattr(models, "PerformanceTelemetry"), "R9 telemetry contract is not implemented"
    return models.PerformanceTelemetry


def test_performance_telemetry_keeps_consistent_immutable_run_totals() -> None:
    telemetry = _telemetry_model()(
        provider_request_counts=FrozenMap({"alpaca": 3, "calendar": 1}),
        response_bytes_by_provider=FrozenMap({"alpaca": 1200, "calendar": 300}),
        response_bytes_total=1500,
        stage_durations_ms=FrozenMap({"COLLECTING": 400, "ANALYZING": 200}),
        synthesis_attempts=0,
        validation_attempts=0,
        research_packet_bytes=8000,
        cache_hits=2,
        cache_misses=3,
        cache_hit_ratio=0.4,
        deadline_budget_ms=30000,
        deadline_consumed_ms=5000,
        remaining_budget_ms=25000,
    )

    assert telemetry.response_bytes_total == sum(telemetry.response_bytes_by_provider.values())
    assert telemetry.cache_hit_ratio == 0.4
    assert telemetry.model_dump()["cache_hit_ratio"] == 0.4
    assert telemetry.model_dump()["remaining_budget_ms"] == 25000
    schema = _telemetry_model().model_json_schema()
    assert "cache_hit_ratio" in schema["properties"]
    assert "remaining_budget_ms" in schema["properties"]
    assert telemetry.remaining_budget_ms == 25000
    with pytest.raises(TypeError):
        telemetry.provider_request_counts["alpaca"] = 99  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("response_bytes_total", 1499),
        ("research_packet_bytes", -1),
        ("cache_hits", -1),
        ("cache_hit_ratio", 0.5),
        ("synthesis_attempts", 4),
        ("validation_attempts", 4),
        ("deadline_budget_ms", 0),
        ("deadline_consumed_ms", 30001),
        ("remaining_budget_ms", 25001),
    ],
)
def test_performance_telemetry_rejects_inconsistent_or_negative_counters(
    field: str, value: int
) -> None:
    payload = {
        "provider_request_counts": FrozenMap({"alpaca": 3, "calendar": 1}),
        "response_bytes_by_provider": FrozenMap({"alpaca": 1200, "calendar": 300}),
        "response_bytes_total": 1500,
        "stage_durations_ms": FrozenMap({"COLLECTING": 400}),
        "synthesis_attempts": 0,
        "validation_attempts": 0,
        "research_packet_bytes": 8000,
        "cache_hits": 2,
        "cache_misses": 3,
        "cache_hit_ratio": 0.4,
        "deadline_budget_ms": 30000,
        "deadline_consumed_ms": 5000,
        "remaining_budget_ms": 25000,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        _telemetry_model().model_validate(payload)


def test_performance_telemetry_empty_cache_and_no_provider_reads_are_well_defined() -> None:
    telemetry = _telemetry_model()(
        provider_request_counts=FrozenMap({}),
        response_bytes_by_provider=FrozenMap({}),
        response_bytes_total=0,
        stage_durations_ms=FrozenMap({}),
        synthesis_attempts=0,
        validation_attempts=0,
        research_packet_bytes=0,
        cache_hits=0,
        cache_misses=0,
        cache_hit_ratio=0.0,
        deadline_budget_ms=1,
        deadline_consumed_ms=0,
        remaining_budget_ms=1,
    )

    assert telemetry.cache_hit_ratio == 0.0
    assert telemetry.remaining_budget_ms == 1


def test_performance_telemetry_preserves_elapsed_time_above_duration_target() -> None:
    telemetry = _telemetry_model()(
        provider_request_counts=FrozenMap({"alpaca": 0, "market-calendar": 0}),
        response_bytes_by_provider=FrozenMap({"alpaca": 0, "market-calendar": 0}),
        response_bytes_total=0,
        stage_durations_ms=FrozenMap({"MARKET_COLLECTION": 900001}),
        synthesis_attempts=0,
        validation_attempts=0,
        research_packet_bytes=0,
        cache_hits=0,
        cache_misses=0,
        cache_hit_ratio=0.0,
        deadline_budget_ms=900000,
        deadline_consumed_ms=900001,
        remaining_budget_ms=0,
    )

    assert telemetry.deadline_consumed_ms == 900001
    assert telemetry.remaining_budget_ms == 0


def test_performance_telemetry_rejects_provider_set_drift() -> None:
    payload = {
        "provider_request_counts": FrozenMap({"alpaca": 3}),
        "response_bytes_by_provider": FrozenMap({"calendar": 1500}),
        "response_bytes_total": 1500,
        "stage_durations_ms": FrozenMap({}),
        "synthesis_attempts": 0,
        "validation_attempts": 0,
        "research_packet_bytes": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_hit_ratio": 0.0,
        "deadline_budget_ms": 1000,
        "deadline_consumed_ms": 0,
        "remaining_budget_ms": 1000,
    }

    with pytest.raises(ValidationError):
        _telemetry_model().model_validate(payload)


def test_performance_telemetry_rejects_secret_fields() -> None:
    payload = {
        "provider_request_counts": FrozenMap({}),
        "response_bytes_by_provider": FrozenMap({}),
        "response_bytes_total": 0,
        "stage_durations_ms": FrozenMap({}),
        "synthesis_attempts": 0,
        "validation_attempts": 0,
        "research_packet_bytes": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_hit_ratio": 0.0,
        "deadline_budget_ms": 1000,
        "deadline_consumed_ms": 0,
        "remaining_budget_ms": 1000,
        "api_key": "never serialize credentials",
    }

    with pytest.raises(ValidationError):
        _telemetry_model().model_validate(payload)
