import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter

from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.application.collection_service import (
    CollectedRunMarketData,
)
from finance_research_agent.application.config_service import (
    ConfigService,
    DirectoryConfigurationRepository,
    configuration_from_snapshot,
)
from finance_research_agent.application.market_collection import (
    MarketDataCollection,
    SymbolMarketCollection,
)
from finance_research_agent.application.ports import (
    MarketCalendarReadinessProvider,
    MarketDataProvider,
    RunRepository,
)
from finance_research_agent.domain.enums import ExecutionStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import ProviderFailure, ProviderReadiness, RunCheckpoint
from finance_research_agent.domain.types import FrozenMap

_CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config" / "examples"
_COLLECTED_AT = datetime(2026, 9, 28, 12, 45, tzinfo=UTC)


class _MarketData:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls = 0

    def readiness(self) -> ProviderReadiness:
        self.calls += 1
        return ProviderReadiness(
            provider="alpaca",
            configured=True,
            available=self.available,
            error_code=None if self.available else ErrorCode.PROVIDER_UNAVAILABLE,
        )


class _Calendar:
    def __init__(self) -> None:
        self.calls = 0

    def readiness(self) -> ProviderReadiness:
        self.calls += 1
        return ProviderReadiness(
            provider="market-calendar", configured=True, available=True
        )

    def is_trading_day(self, market_date) -> bool:
        raise AssertionError("quality checkpoint must not collect calendar sessions")

    def session_open_close(self, market_date):
        raise AssertionError("quality checkpoint must not resolve a session window")


def _stored_collection(repository, valid_packet):
    configuration = ConfigService(
        DirectoryConfigurationRepository(_CONFIG_ROOT)
    ).validate_and_snapshot()
    config = configuration_from_snapshot(configuration)
    run = valid_packet.run.model_copy(
        update={
            "evidence_cutoff_at": None,
            "configuration_snapshot": configuration,
        }
    )
    repository.create(run)
    symbols: dict[str, None] = {}
    for item in config.watchlist.items:
        symbols.setdefault(item.symbol, None)
        if item.benchmark_symbol is not None:
            symbols.setdefault(item.benchmark_symbol, None)
        if item.sector_proxy_symbol is not None:
            symbols.setdefault(item.sector_proxy_symbol, None)
    for symbol in config.regime.radar_universe:
        symbols.setdefault(symbol, None)
    collection = MarketDataCollection(
        symbols=tuple(
            SymbolMarketCollection(
                symbol=symbol,
                instrument=ProviderFailure(
                    provider="alpaca",
                    symbol=symbol,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
                daily_bars=ProviderFailure(
                    provider="alpaca",
                    symbol=symbol,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
                premarket_observation=ProviderFailure(
                    provider="alpaca",
                    symbol=symbol,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=True,
                ),
            )
            for symbol in symbols
        ),
        completed_at=_COLLECTED_AT,
    )
    payload = json.dumps(
        TypeAdapter(MarketDataCollection).dump_python(collection, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    digest = repository.stage_artifact(run.run_id, "market_data_collection", payload)
    repository.checkpoint(
        run.run_id,
        RunCheckpoint(
            run_id=run.run_id,
            stage="EVIDENCE_COLLECTED",
            execution_status=run.execution_status,
            data_quality_status=run.data_quality_status,
            delivery_status=run.delivery_status,
            written_at=_COLLECTED_AT,
            evidence_cutoff_at=None,
            artifact_hashes=FrozenMap({"market_data_collection": digest}),
            resumable=True,
        ),
    )
    frozen_run = repository.freeze_evidence(run.run_id, _COLLECTED_AT)
    assert frozen_run.checkpoints[-1].stage == "EVIDENCE_FROZEN"
    assert sha256(payload).hexdigest() == digest
    return CollectedRunMarketData(frozen_run, collection)


def test_checkpoint_quality_uses_frozen_source_policy_and_stored_collection(
    tmp_path: Path, valid_packet
) -> None:
    from finance_research_agent.application.quality_pipeline import (
        checkpoint_collected_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    collected = _stored_collection(repository, valid_packet)
    market_data = _MarketData()
    calendar = _Calendar()
    checkpointed_at = _COLLECTED_AT + timedelta(seconds=1)

    result = checkpoint_collected_market_data_quality(
        repository,
        collected,
        cast(MarketDataProvider, market_data),
        cast(MarketCalendarReadinessProvider, calendar),
        checkpointed_at=checkpointed_at,
    )

    stored = repository.load(collected.frozen_run.run_id)
    assert stored is not None
    assert stored.checkpoints[-1].stage == "QUALITY_EVALUATED"
    assert stored.checkpoints[-1].execution_status is ExecutionStatus.ANALYZING
    assert stored.checkpoints[-1].written_at == checkpointed_at
    assert stored.checkpoints[-1].data_quality_status is result.quality.status
    assert result.stored_run == stored
    assert result.quality.status.value == "DEGRADED"
    assert market_data.calls == calendar.calls == 1


def test_unavailable_configured_market_data_source_records_global_quality_failure(
    tmp_path: Path, valid_packet
) -> None:
    from finance_research_agent.application.quality_pipeline import (
        checkpoint_collected_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    collected = _stored_collection(repository, valid_packet)
    result = checkpoint_collected_market_data_quality(
        repository,
        collected,
        cast(MarketDataProvider, _MarketData(available=False)),
        cast(MarketCalendarReadinessProvider, _Calendar()),
        checkpointed_at=_COLLECTED_AT + timedelta(seconds=1),
    )

    assert result.quality.status.value == "FAIL"
    assert result.quality.global_reason_codes == (ErrorCode.PROVIDER_UNAVAILABLE,)


def test_checkpoint_quality_rejects_an_invalid_collection_wrapper(
    tmp_path: Path,
) -> None:
    from finance_research_agent.application.quality_pipeline import (
        checkpoint_collected_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    market_data = _MarketData()
    calendar = _Calendar()

    with pytest.raises(TypeError, match="CollectedRunMarketData"):
        checkpoint_collected_market_data_quality(
            repository,
            cast(CollectedRunMarketData, None),
            cast(MarketDataProvider, market_data),
            cast(MarketCalendarReadinessProvider, calendar),
            checkpointed_at=_COLLECTED_AT + timedelta(seconds=1),
        )

    assert market_data.calls == calendar.calls == 0


def test_checkpoint_quality_rejects_a_missing_stored_run(
    tmp_path: Path, valid_packet
) -> None:
    from finance_research_agent.application.quality_pipeline import (
        checkpoint_collected_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    collected = _stored_collection(repository, valid_packet)
    other_repository = FileSystemRunRepository(tmp_path / "empty")
    market_data = _MarketData()
    calendar = _Calendar()

    with pytest.raises(ValueError, match="current unpublished run"):
        checkpoint_collected_market_data_quality(
            other_repository,
            collected,
            cast(MarketDataProvider, market_data),
            cast(MarketCalendarReadinessProvider, calendar),
            checkpointed_at=_COLLECTED_AT + timedelta(seconds=1),
        )

    assert market_data.calls == calendar.calls == 0


def test_quality_artifact_decoder_rejects_non_object_payload() -> None:
    from finance_research_agent.application.quality_pipeline import _decode_quality

    with pytest.raises(ValueError, match="must be an object"):
        _decode_quality(b"[]")


def test_quality_checkpoint_retry_uses_recorded_result_without_provider_reads(
    tmp_path: Path, valid_packet
) -> None:
    from finance_research_agent.application.quality_pipeline import (
        checkpoint_collected_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    collected = _stored_collection(repository, valid_packet)
    market_data = _MarketData()
    calendar = _Calendar()
    first = checkpoint_collected_market_data_quality(
        repository,
        collected,
        cast(MarketDataProvider, market_data),
        cast(MarketCalendarReadinessProvider, calendar),
        checkpointed_at=_COLLECTED_AT + timedelta(seconds=1),
    )

    resumed = checkpoint_collected_market_data_quality(
        repository,
        CollectedRunMarketData(first.stored_run, collected.collection),
        cast(MarketDataProvider, market_data),
        cast(MarketCalendarReadinessProvider, calendar),
        checkpointed_at=_COLLECTED_AT + timedelta(seconds=2),
    )

    assert resumed.quality == first.quality
    assert resumed.stored_run.checkpoints == first.stored_run.checkpoints
    assert market_data.calls == calendar.calls == 1


def test_quality_checkpoint_resume_rejects_a_missing_quality_artifact(
    tmp_path: Path, valid_packet
) -> None:
    from finance_research_agent.application.quality_pipeline import (
        checkpoint_collected_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    collected = _stored_collection(repository, valid_packet)
    market_data = _MarketData()
    calendar = _Calendar()
    first = checkpoint_collected_market_data_quality(
        repository,
        collected,
        cast(MarketDataProvider, market_data),
        cast(MarketCalendarReadinessProvider, calendar),
        checkpointed_at=_COLLECTED_AT + timedelta(seconds=1),
    )

    class MissingQualityArtifact:
        def load(self, run_id: str):
            return repository.load(run_id)

        def read_staged_artifact(self, run_id: str, artifact_name: str):
            if artifact_name == "data_quality":
                return None
            return repository.read_staged_artifact(run_id, artifact_name)

    with pytest.raises(ValueError, match="missing its staged result"):
        checkpoint_collected_market_data_quality(
            cast(RunRepository, MissingQualityArtifact()),
            CollectedRunMarketData(first.stored_run, collected.collection),
            cast(MarketDataProvider, market_data),
            cast(MarketCalendarReadinessProvider, calendar),
            checkpointed_at=_COLLECTED_AT + timedelta(seconds=2),
        )

    assert market_data.calls == calendar.calls == 1


def test_checkpoint_quality_rejects_collection_different_from_frozen_artifact(
    tmp_path: Path, valid_packet
) -> None:
    from finance_research_agent.application.quality_pipeline import (
        checkpoint_collected_market_data_quality,
    )

    repository = FileSystemRunRepository(tmp_path)
    collected = _stored_collection(repository, valid_packet)
    different_collection = MarketDataCollection(
        symbols=collected.collection.symbols,
        completed_at=_COLLECTED_AT + timedelta(seconds=1),
    )
    market_data = _MarketData()
    calendar = _Calendar()

    with pytest.raises(ValueError, match="frozen collection"):
        checkpoint_collected_market_data_quality(
            repository,
            CollectedRunMarketData(collected.frozen_run, different_collection),
            cast(MarketDataProvider, market_data),
            cast(MarketCalendarReadinessProvider, calendar),
            checkpointed_at=_COLLECTED_AT + timedelta(seconds=2),
        )

    assert market_data.calls == calendar.calls == 0
    stored = repository.load(collected.frozen_run.run_id)
    assert stored is not None
    assert stored.checkpoints[-1].stage == "EVIDENCE_FROZEN"
