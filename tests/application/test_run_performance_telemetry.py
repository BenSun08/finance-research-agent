from pathlib import Path

import pytest

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.application.performance_telemetry import (
    RunTelemetryRecorder,
    checkpoint_with_telemetry,
    load_checkpoint_telemetry,
)
from finance_research_agent.domain.models import RunCheckpoint
from finance_research_agent.domain.types import FrozenMap


class _Monotonic:
    def __init__(self, step_ns: int = 250_000_000) -> None:
        self.value = 0
        self.step_ns = step_ns

    def __call__(self) -> int:
        self.value += self.step_ns
        return self.value


def test_recorder_accumulates_stage_time_and_reports_zero_provider_metrics() -> None:
    clock = _Monotonic()
    recorder = RunTelemetryRecorder(
        monotonic_ns=clock,
    )

    with recorder.measure_stage("MARKET_COLLECTION"):
        pass
    with recorder.measure_stage("QUALITY_EVALUATION"):
        pass

    telemetry = recorder.snapshot()

    assert telemetry.stage_durations_ms == FrozenMap(
        {
            "RUN_PREPARATION": 0,
            "MARKET_COLLECTION": 250,
            "QUALITY_EVALUATION": 250,
            "PACKET_ASSEMBLY": 0,
            "VALIDATION": 0,
            "PUBLICATION": 0,
        }
    )
    assert telemetry.provider_request_counts == FrozenMap(
        {"alpaca": 0, "market-calendar": 0}
    )
    assert telemetry.response_bytes_by_provider == FrozenMap(
        {"alpaca": 0, "market-calendar": 0}
    )
    assert telemetry.deadline_budget_ms == 900_000
    assert telemetry.deadline_consumed_ms == 500
    assert telemetry.remaining_budget_ms == 899_500


def test_recorder_keeps_actual_duration_above_target_without_stopping_work() -> None:
    clock = _Monotonic(step_ns=900_001_000_000)
    recorder = RunTelemetryRecorder(
        monotonic_ns=clock,
    )

    with recorder.measure_stage("PUBLICATION"):
        pass

    telemetry = recorder.snapshot()

    assert telemetry.deadline_consumed_ms == 900_001
    assert telemetry.remaining_budget_ms == 0


@pytest.mark.parametrize(
    ("adapter", "attempts", "response_bytes"),
    [
        ("other-provider", 1, 0),
        ("market-calendar", 1, 0),
        ("alpaca", -1, 0),
        ("alpaca", 1, -1),
    ],
)
def test_recorder_rejects_unapproved_adapters_and_invalid_counters(
    adapter: str, attempts: int, response_bytes: int
) -> None:
    recorder = RunTelemetryRecorder(
        monotonic_ns=lambda: 0,
    )

    with pytest.raises(ValueError):
        recorder.record_http_exchange(adapter, attempts, response_bytes)


def test_recorder_aggregates_alpaca_http_exchanges() -> None:
    recorder = RunTelemetryRecorder(
        monotonic_ns=lambda: 0,
    )

    recorder.record_http_exchange("alpaca", 2, 17)
    recorder.record_http_exchange("alpaca", 1, 23)
    telemetry = recorder.snapshot()

    assert telemetry.provider_request_counts == FrozenMap(
        {"alpaca": 3, "market-calendar": 0}
    )
    assert telemetry.response_bytes_by_provider == FrozenMap(
        {"alpaca": 40, "market-calendar": 0}
    )
    assert telemetry.response_bytes_total == 40


def test_recorder_restores_cumulative_measurements_without_double_counting() -> None:
    first = _Monotonic()
    recorder = RunTelemetryRecorder(monotonic_ns=first)
    with recorder.measure_stage("MARKET_COLLECTION"):
        pass
    recorder.record_http_exchange("alpaca", 2, 11)
    frozen = recorder.snapshot()

    resumed = RunTelemetryRecorder(
        monotonic_ns=lambda: 0,
        initial=frozen,
    )

    assert resumed.snapshot() == frozen
    resumed.record_http_exchange("alpaca", 1, 7)
    assert resumed.snapshot().provider_request_counts["alpaca"] == 3
    assert resumed.snapshot().response_bytes_total == 18


def test_checkpoint_telemetry_round_trip_is_hash_bound(
    tmp_path: Path, valid_packet
) -> None:
    run = valid_packet.run
    repository = FileSystemRunRepository(tmp_path)
    repository.create(run)
    checkpoint = RunCheckpoint(
        run_id=run.run_id,
        stage="RUN_PREPARED",
        execution_status=run.execution_status,
        data_quality_status=run.data_quality_status,
        delivery_status=run.delivery_status,
        written_at=run.invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({"run_context": "a" * 64}),
        resumable=True,
    )
    recorder = RunTelemetryRecorder(
        monotonic_ns=lambda: 0,
    )
    telemetry = recorder.snapshot()

    checkpointed = checkpoint_with_telemetry(repository, checkpoint, telemetry)

    assert "performance_telemetry" in checkpointed.artifact_hashes
    assert load_checkpoint_telemetry(repository, checkpointed) == telemetry


def test_checkpoint_telemetry_rejects_tampered_staged_bytes(
    tmp_path: Path, valid_packet
) -> None:
    run = valid_packet.run
    repository = FileSystemRunRepository(tmp_path)
    repository.create(run)
    checkpoint = RunCheckpoint(
        run_id=run.run_id,
        stage="RUN_PREPARED",
        execution_status=run.execution_status,
        data_quality_status=run.data_quality_status,
        delivery_status=run.delivery_status,
        written_at=run.invoked_at,
        evidence_cutoff_at=None,
        artifact_hashes=FrozenMap({}),
        resumable=True,
    )
    telemetry = RunTelemetryRecorder(
        monotonic_ns=lambda: 0,
    ).snapshot()
    checkpointed = checkpoint_with_telemetry(repository, checkpoint, telemetry)
    next(tmp_path.rglob("performance_telemetry.bin")).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="hash"):
        load_checkpoint_telemetry(repository, checkpointed)
