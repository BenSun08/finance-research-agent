"""Run-scoped, secret-free performance measurements and checkpoint binding."""

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from hashlib import sha256
from time import perf_counter_ns

from finance_research_agent.application.ports import RunRepository
from finance_research_agent.domain.models import (
    PerformanceTelemetry,
    RunCheckpoint,
    StoredRun,
)
from finance_research_agent.domain.types import FrozenMap

_ARTIFACT_PREFIX = "performance_telemetry_"
_PROVIDERS = ("alpaca", "market-calendar")
_HTTP_ADAPTERS = frozenset({"alpaca"})
_STAGES = (
    "RUN_PREPARATION",
    "MARKET_COLLECTION",
    "QUALITY_EVALUATION",
    "PACKET_ASSEMBLY",
    "VALIDATION",
    "PUBLICATION",
)
_DURATION_TARGET_MS = 900_000


def _canonical_bytes(telemetry: PerformanceTelemetry) -> bytes:
    return json.dumps(
        telemetry.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


class RunTelemetryRecorder:
    """Aggregate approved run measurements without retaining request content."""

    def __init__(
        self,
        *,
        monotonic_ns: Callable[[], int] = perf_counter_ns,
        initial: PerformanceTelemetry | None = None,
    ) -> None:
        self._monotonic_ns = monotonic_ns
        self._provider_attempts = {provider: 0 for provider in _PROVIDERS}
        self._response_bytes = {provider: 0 for provider in _PROVIDERS}
        self._stage_durations = {stage: 0 for stage in _STAGES}
        if initial is not None:
            if (
                set(initial.provider_request_counts) != set(_PROVIDERS)
                or set(initial.response_bytes_by_provider) != set(_PROVIDERS)
                or not set(initial.stage_durations_ms).issubset(_STAGES)
            ):
                raise ValueError("initial telemetry contains unsupported measurement keys")
            self._provider_attempts.update(initial.provider_request_counts)
            self._response_bytes.update(initial.response_bytes_by_provider)
            self._stage_durations.update(initial.stage_durations_ms)

    def measure_stage(self, stage: str) -> AbstractContextManager[None]:
        """Accumulate active monotonic duration for a named R9 stage."""
        if stage not in _STAGES:
            raise ValueError("unsupported telemetry stage")

        @contextmanager
        def _measurement() -> Iterator[None]:
            started = self._monotonic_ns()
            try:
                yield
            finally:
                elapsed_ns = max(0, self._monotonic_ns() - started)
                self._stage_durations[stage] += elapsed_ns // 1_000_000

        return _measurement()

    def record_stage_duration_ms(self, stage: str, duration_ms: int) -> None:
        """Add an elapsed duration measured around a multi-step application stage."""
        if stage not in _STAGES:
            raise ValueError("unsupported telemetry stage")
        if type(duration_ms) is not int or duration_ms < 0:
            raise ValueError("duration_ms must be a non-negative integer")
        self._stage_durations[stage] += duration_ms

    def restore(self, telemetry: PerformanceTelemetry) -> None:
        """Replace accumulated values with the latest verified checkpoint snapshot."""
        if not isinstance(telemetry, PerformanceTelemetry):
            raise TypeError("telemetry must be PerformanceTelemetry")
        if (
            set(telemetry.provider_request_counts) != set(_PROVIDERS)
            or set(telemetry.response_bytes_by_provider) != set(_PROVIDERS)
            or not set(telemetry.stage_durations_ms).issubset(_STAGES)
        ):
            raise ValueError("telemetry contains unsupported measurement keys")
        self._provider_attempts = dict(telemetry.provider_request_counts)
        self._response_bytes = dict(telemetry.response_bytes_by_provider)
        self._stage_durations = {stage: 0 for stage in _STAGES}
        self._stage_durations.update(telemetry.stage_durations_ms)

    def record_http_exchange(
        self, adapter: str, request_attempts: int, response_bytes: int
    ) -> None:
        """Record bounded numeric totals for the approved Alpaca adapter."""
        if adapter not in _HTTP_ADAPTERS:
            raise ValueError("unsupported telemetry adapter")
        if type(request_attempts) is not int or request_attempts < 0:
            raise ValueError("request_attempts must be a non-negative integer")
        if type(response_bytes) is not int or response_bytes < 0:
            raise ValueError("response_bytes must be a non-negative integer")
        self._provider_attempts[adapter] += request_attempts
        self._response_bytes[adapter] += response_bytes

    def snapshot(
        self, *, research_packet_bytes: int = 0, validation_attempts: int = 0
    ) -> PerformanceTelemetry:
        """Return an immutable validated view of measurements captured so far."""
        if type(research_packet_bytes) is not int or research_packet_bytes < 0:
            raise ValueError("research_packet_bytes must be a non-negative integer")
        if type(validation_attempts) is not int or not 0 <= validation_attempts <= 3:
            raise ValueError("validation_attempts must be between zero and three")
        consumed = sum(self._stage_durations.values())
        return PerformanceTelemetry(
            provider_request_counts=FrozenMap(self._provider_attempts),
            response_bytes_by_provider=FrozenMap(self._response_bytes),
            response_bytes_total=sum(self._response_bytes.values()),
            stage_durations_ms=FrozenMap(self._stage_durations),
            synthesis_attempts=0,
            validation_attempts=validation_attempts,
            research_packet_bytes=research_packet_bytes,
            cache_hits=0,
            cache_misses=0,
            cache_hit_ratio=0.0,
            deadline_budget_ms=_DURATION_TARGET_MS,
            deadline_consumed_ms=consumed,
            remaining_budget_ms=max(0, _DURATION_TARGET_MS - consumed),
        )


def checkpoint_with_telemetry(
    repository: RunRepository,
    checkpoint: RunCheckpoint,
    telemetry: PerformanceTelemetry,
) -> RunCheckpoint:
    """Stage canonical telemetry and bind its digest to a checkpoint value."""
    if not isinstance(checkpoint, RunCheckpoint):
        raise TypeError("checkpoint must be RunCheckpoint")
    if not isinstance(telemetry, PerformanceTelemetry):
        raise TypeError("telemetry must be PerformanceTelemetry")
    payload = _canonical_bytes(telemetry)
    digest = sha256(payload).hexdigest()
    artifact_name = _ARTIFACT_PREFIX + digest
    staged_digest = repository.stage_artifact(checkpoint.run_id, artifact_name, payload)
    if staged_digest != digest:
        raise RuntimeError("repository returned an invalid telemetry artifact digest")
    hashes = dict(checkpoint.artifact_hashes)
    for previous_name in tuple(hashes):
        if previous_name.startswith(_ARTIFACT_PREFIX):
            del hashes[previous_name]
    hashes[artifact_name] = digest
    return checkpoint.model_copy(update={"artifact_hashes": FrozenMap(hashes)})


def load_checkpoint_telemetry(
    repository: RunRepository, checkpoint: RunCheckpoint
) -> PerformanceTelemetry | None:
    """Load telemetry only when bytes match its checkpoint-bound digest."""
    names = [name for name in checkpoint.artifact_hashes if name.startswith(_ARTIFACT_PREFIX)]
    if not names:
        return None
    if len(names) != 1:
        raise ValueError("checkpoint has multiple telemetry artifacts")
    artifact_name = names[0]
    digest = checkpoint.artifact_hashes[artifact_name]
    if artifact_name != _ARTIFACT_PREFIX + digest:
        raise ValueError("telemetry artifact name does not match its checkpoint hash")
    payload = repository.read_staged_artifact(checkpoint.run_id, artifact_name)
    if payload is None or sha256(payload).hexdigest() != digest:
        raise ValueError("telemetry artifact does not match its checkpoint hash")
    try:
        telemetry = PerformanceTelemetry.model_validate_json(payload)
    except ValueError as exc:
        raise ValueError("telemetry artifact is malformed") from exc
    if _canonical_bytes(telemetry) != payload:
        raise ValueError("telemetry artifact is not canonical")
    return telemetry


def load_latest_checkpoint_telemetry(
    repository: RunRepository, stored: StoredRun
) -> PerformanceTelemetry | None:
    """Load telemetry from the latest checkpoint that binds a snapshot."""
    for checkpoint in reversed(stored.checkpoints):
        if any(name.startswith(_ARTIFACT_PREFIX) for name in checkpoint.artifact_hashes):
            return load_checkpoint_telemetry(repository, checkpoint)
    return None
