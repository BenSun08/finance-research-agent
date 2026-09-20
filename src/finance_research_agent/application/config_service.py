"""Configuration validation, hashing, and immutable snapshot service."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import ClassVar

from finance_research_agent.application.ports import (
    ConfigurationRepository,
    ConfigurationRepositoryFactory,
)
from finance_research_agent.domain.models import ConfigurationSnapshot
from finance_research_agent.domain.policies import canonical_model_hash
from finance_research_agent.domain.types import FrozenMap


def _jsonable(value: object) -> object:
    return json.loads(
        json.dumps(value, default=lambda item: getattr(item, "value", str(item)))
    )


class ConfigService:
    _directory_factory: ClassVar[ConfigurationRepositoryFactory | None] = None

    def __init__(self, repository: ConfigurationRepository) -> None:
        self._repository = repository

    @classmethod
    def register_directory_factory(cls, factory: ConfigurationRepositoryFactory) -> None:
        """Register an adapter-side composition factory without importing that adapter here."""

        cls._directory_factory = factory

    @classmethod
    def from_directories(
        cls,
        source: Path,
        staging_root: Path,
        *,
        repository_factory: ConfigurationRepositoryFactory | None = None,
    ) -> ConfigService:
        """Build a service through an injected trusted directory-repository factory."""

        source_path = Path(source)
        staging_path = Path(staging_root)
        if not source_path.is_dir():
            raise ValueError("configuration source must be a directory; caller paths are rejected")
        factory = repository_factory or cls._directory_factory
        if factory is None:
            raise ValueError("a configuration repository factory must be registered")
        return cls(factory(source_path, staging_path))

    def validate_and_snapshot(self) -> ConfigurationSnapshot:
        configuration = self._repository.load()
        policy_values = (
            ("watchlist", configuration.watchlist),
            ("risk", configuration.risk),
            ("regime", configuration.regime),
            ("setup", configuration.setup),
            ("source", configuration.source),
        )
        hashes = {name: canonical_model_hash(policy) for name, policy in policy_values}
        file_hashes = {
            f"{name}-policy.yaml" if name != "watchlist" else "watchlist.yaml": value
            for name, value in hashes.items()
        }
        serialized = FrozenMap(
            {
                name: (
                    _jsonable(asdict(policy))
                    if is_dataclass(policy)
                    else policy.model_dump(mode="json")
                )
                for name, policy in policy_values
            }
        )
        return ConfigurationSnapshot.model_validate(
            {
                "content_hash_sha256": canonical_model_hash(configuration),
                "file_hashes": FrozenMap(file_hashes),
                "watchlist_version": configuration.watchlist.version,
                "regime_policy_version": configuration.regime.version,
                "setup_policy_version": configuration.setup.version,
                "risk_policy_version": configuration.risk.version,
                "source_policy_version": configuration.source.version,
                "policies": serialized,
                "policy_hashes": FrozenMap(hashes),
                "radar_universe": configuration.regime.radar_universe,
            }
        )
