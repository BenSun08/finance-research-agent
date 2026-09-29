"""Compose frozen market-data quality evaluation with its durable checkpoint."""

import json
from dataclasses import dataclass
from datetime import datetime

from finance_research_agent.application.collection_quality import (
    evaluate_collected_market_data_quality,
)
from finance_research_agent.application.collection_service import (
    CollectedRunMarketData,
    load_frozen_market_data_for_run,
)
from finance_research_agent.application.config_service import configuration_from_snapshot
from finance_research_agent.application.ports import (
    MarketCalendarReadinessProvider,
    MarketDataProvider,
    RunRepository,
)
from finance_research_agent.application.quality_checkpoint import (
    checkpoint_market_data_quality,
)
from finance_research_agent.application.source_health import read_configured_source_health
from finance_research_agent.domain.enums import DataQualityStatus, PlanStatus
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import CapabilityState, StoredRun
from finance_research_agent.domain.quality import DataQualityResult


@dataclass(frozen=True, slots=True)
class CheckpointedMarketDataQuality:
    """Deterministic quality evaluation and its current persisted run state."""

    stored_run: StoredRun
    quality: DataQualityResult


def _decode_quality(payload: bytes) -> DataQualityResult:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("staged quality result must be an object")
    value["status"] = DataQualityStatus(value["status"])
    value["capabilities"] = tuple(
        CapabilityState.model_validate_json(json.dumps(item))
        for item in value["capabilities"]
    )
    value["global_reason_codes"] = tuple(
        ErrorCode(code) for code in value["global_reason_codes"]
    )
    value["symbol_capabilities"] = {
        symbol: tuple(
            CapabilityState.model_validate_json(json.dumps(item)) for item in states
        )
        for symbol, states in value["symbol_capabilities"].items()
    }
    value["symbol_plan_statuses"] = {
        symbol: PlanStatus(status)
        for symbol, status in value["symbol_plan_statuses"].items()
    }
    return DataQualityResult.model_validate(value, strict=True)


def checkpoint_collected_market_data_quality(
    repository: RunRepository,
    collected: CollectedRunMarketData,
    market_data: MarketDataProvider,
    calendar: MarketCalendarReadinessProvider,
    *,
    checkpointed_at: datetime,
) -> CheckpointedMarketDataQuality:
    """Evaluate the exact frozen collection, then persist a restart-safe result.

    The configured source health and policies come from the run's frozen
    configuration snapshot. When the quality checkpoint already exists, its
    canonical staged result is resumed without reading current provider state.
    """
    if not isinstance(collected, CollectedRunMarketData):
        raise TypeError("collected must be CollectedRunMarketData")
    authoritative = load_frozen_market_data_for_run(
        collected.frozen_run.run, repository
    )
    if authoritative != collected:
        raise ValueError("quality evaluation collection differs from frozen collection")
    stored = authoritative.frozen_run

    existing_quality = repository.read_staged_artifact(stored.run_id, "data_quality")
    if stored.checkpoints[-1].stage == "QUALITY_EVALUATED":
        if existing_quality is None:
            raise ValueError("quality checkpoint is missing its staged result")
        quality = _decode_quality(existing_quality)
    elif existing_quality is not None:
        quality = _decode_quality(existing_quality)
    else:
        configuration = configuration_from_snapshot(stored.run.configuration_snapshot)
        source_health = read_configured_source_health(
            configuration.source, market_data, calendar
        )
        quality = evaluate_collected_market_data_quality(
            collected.collection,
            source_health=source_health,
            source_policy=configuration.source,
            risk_policy=configuration.risk,
        )

    updated = checkpoint_market_data_quality(
        repository,
        stored,
        quality,
        checkpointed_at=checkpointed_at,
    )
    return CheckpointedMarketDataQuality(stored_run=updated, quality=quality)
