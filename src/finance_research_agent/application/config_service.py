"""Configuration validation, hashing, and immutable snapshot service."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass

from finance_research_agent.application.ports import ConfigurationRepository
from finance_research_agent.domain.models import ConfigurationSnapshot
from finance_research_agent.domain.policies import canonical_model_hash
from finance_research_agent.domain.types import FrozenMap


def _jsonable(value: object) -> object:
    return json.loads(
        json.dumps(value, default=lambda item: getattr(item, "value", str(item)))
    )


class ConfigService:
    def __init__(self, repository: ConfigurationRepository) -> None:
        self._repository = repository

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
                "file_hashes": FrozenMap(hashes),
                "watchlist_version": configuration.watchlist.version,
                "regime_policy_version": configuration.regime.version,
                "setup_policy_version": configuration.setup.version,
                "risk_policy_version": configuration.risk.version,
                "source_policy_version": configuration.source.version,
                "policies": serialized,
                "policy_hashes": FrozenMap(hashes),
                "radar_universe": configuration.regime.required_symbols,
            }
        )
