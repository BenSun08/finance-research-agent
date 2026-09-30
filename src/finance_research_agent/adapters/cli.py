"""Diagnostic and frozen-replay CLI for Product A."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from pydantic import BaseModel, ValidationError

from finance_research_agent.adapters.exchange_calendar import ExchangeCalendarAdapter
from finance_research_agent.adapters.feedback import FileSystemFeedbackRepository
from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.adapters.yaml_config import (
    YamlConfigurationRepository,
    YamlWatchlistRepository,
)
from finance_research_agent.application.component_versions import current_component_versions
from finance_research_agent.application.operations import (
    GetRunStatusRequest,
    ProductAOperation,
)
from finance_research_agent.application.ports import Clock, MarketDataProvider
from finance_research_agent.application.replay_service import (
    ArtifactNotFoundError,
    ArtifactReplayResult,
    replay_published_artifact,
)
from finance_research_agent.application.services import ApplicationServices
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import ConfigurationSnapshot, ProviderReadiness
from finance_research_agent.settings import Settings


class _Dispatcher(Protocol):
    def dispatch(self, operation: str, arguments_json: str) -> object: ...


Replay = Callable[[str], object]


class _SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)


class _ConfiguredMarketDataReadiness:
    """Report local credential configuration without making provider requests."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def readiness(self) -> ProviderReadiness:
        configured = (
            self._settings.alpaca_api_key is not None
            and self._settings.alpaca_api_secret is not None
        )
        return ProviderReadiness(
            provider="alpaca",
            configured=configured,
            available=configured,
            error_code=None if configured else ErrorCode.CREDENTIALS_MISSING,
        )


def _default_runtime() -> tuple[ApplicationServices, Replay]:
    settings = Settings()
    data_root = settings.data_dir
    configuration = YamlConfigurationRepository(data_root / "config")
    run_repository = FileSystemRunRepository(data_root)
    market_data = cast(MarketDataProvider, _ConfiguredMarketDataReadiness(settings))
    calendar = ExchangeCalendarAdapter()
    services = ApplicationServices(
        clock=cast(Clock, _SystemClock()),
        calendar=calendar,
        configuration_repository=configuration,
        market_data=market_data,
        run_repository=run_repository,
        published_artifact_reader=run_repository,
        watchlist_repository=YamlWatchlistRepository(data_root / "config"),
        feedback_repository=FileSystemFeedbackRepository(data_root / "feedback"),
    )

    def replay(run_id: str) -> ArtifactReplayResult:
        bundle = run_repository.load_published_bundle(run_id)
        if bundle is None:
            raise ArtifactNotFoundError("no complete published artifact exists for this run")
        return replay_published_artifact(
            run_repository,
            run_id,
            current_component_versions(bundle.run.configuration_snapshot),
        )

    return services, replay


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finance-research-agent",
        description="Inspect Product A readiness or replay a frozen published artifact.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="report system readiness diagnostics")
    commands.add_parser("config", help="validate and summarize local configuration")
    replay_parser = commands.add_parser(
        "replay", help="verify a stored published artifact without provider access"
    )
    replay_parser.add_argument("run_id", help="published Product A run identifier")
    return parser


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


def _configuration_summary(value: object) -> object:
    if not isinstance(value, ConfigurationSnapshot):
        return _json_value(value)
    return {
        "content_hash_sha256": value.content_hash_sha256,
        "file_hashes": dict(value.file_hashes),
        "policy_hashes": None if value.policy_hashes is None else dict(value.policy_hashes),
        "policy_versions": {
            "watchlist": value.watchlist_version,
            "regime": value.regime_policy_version,
            "setup": value.setup_policy_version,
            "risk": value.risk_policy_version,
            "source": value.source_policy_version,
        },
        "radar_universe_size": len(value.radar_universe),
    }


def _replay_summary(value: object) -> object:
    if not isinstance(value, ArtifactReplayResult):
        return _json_value(value)
    return {
        "run_id": value.run_id,
        "json_matches": value.json_matches,
        "markdown_matches": value.markdown_matches,
        "stored_json_sha256": value.stored_json_sha256,
        "replayed_json_sha256": value.replayed_json_sha256,
        "stored_markdown_sha256": value.stored_markdown_sha256,
        "replayed_markdown_sha256": value.replayed_markdown_sha256,
        "component_version_mismatches": list(value.component_version_mismatches),
        "current_policy_config_compared": False,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    services: _Dispatcher | None = None,
    replay: Replay | None = None,
) -> int:
    """Run a diagnostic or frozen-artifact replay command and write JSON output."""
    parser = _parser()
    arguments = parser.parse_args(argv)

    if arguments.command == "replay":
        try:
            typed = GetRunStatusRequest(run_id=arguments.run_id)
        except ValidationError:
            parser.error("invalid Product A run identifier")
        run_id = typed.run_id
    else:
        run_id = None

    if services is None or (arguments.command == "replay" and replay is None):
        default_services, default_replay = _default_runtime()
        services = services or default_services
        replay = replay or default_replay

    if arguments.command == "status":
        value = services.dispatch(ProductAOperation.GET_SYSTEM_STATUS.value, "{}")
    elif arguments.command == "config":
        value = _configuration_summary(
            services.dispatch(ProductAOperation.VALIDATE_CONFIGURATION.value, "{}")
        )
    else:
        if replay is None or run_id is None:
            parser.error("replay runtime is unavailable")
        value = _replay_summary(replay(run_id))

    print(json.dumps(_json_value(value), sort_keys=True, separators=(",", ":")))
    return 0


__all__ = ["main"]
