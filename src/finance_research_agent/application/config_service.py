"""Configuration validation, hashing, and immutable snapshot service."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml

from finance_research_agent.application.ports import (
    ConfigurationRepository,
    ConfigurationRepositoryFactory,
)
from finance_research_agent.domain.models import ConfigurationSnapshot
from finance_research_agent.domain.policies import (
    AppConfiguration,
    RiskPolicy,
    SetupPolicy,
    SourcePolicy,
    WatchlistConfig,
    canonical_model_hash,
)
from finance_research_agent.domain.regime import RegimeComponent, RegimePolicy
from finance_research_agent.domain.types import FrozenMap

FIXED_CONFIGURATION_FILES = {
    "watchlist": "watchlist.yaml",
    "risk": "risk-policy.yaml",
    "regime": "regime-policy.yaml",
    "setup": "setup-policy.yaml",
    "source": "source-policy.yaml",
}


def _tuplify(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tuplify(item) for item in value)
    if isinstance(value, dict):
        return {key: _tuplify(item) for key, item in value.items()}
    return value


def _read_configuration_file(root: Path, filename: str) -> dict[str, Any]:
    root = Path(root)
    if not root.is_dir():
        raise ValueError("configuration root must be a directory")
    path = root / filename
    if path.parent != root or path.name != filename:
        raise ValueError("configuration paths must be fixed filenames")
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{filename} must contain a mapping")
    return cast(dict[str, Any], _tuplify(value))


def load_configuration(root: Path) -> AppConfiguration:
    """Load exactly the five trusted configuration files beneath *root*."""

    values = {
        name: _read_configuration_file(root, filename)
        for name, filename in FIXED_CONFIGURATION_FILES.items()
    }
    regime_values = values["regime"]
    regime_values["component_weights"] = tuple(
        (
            RegimeComponent[component]
            if component in RegimeComponent.__members__
            else RegimeComponent(component),
            Decimal(weight),
        )
        for component, weight in regime_values["component_weights"]
    )
    for field in (
        "volatility_positive_maximum",
        "volatility_negative_minimum",
        "permissive_threshold",
        "defensive_threshold",
    ):
        regime_values[field] = Decimal(regime_values[field])
    setup_values = values["setup"]
    for field in (
        "entry_zone_atr_buffers",
        "extension_limits",
        "pullback_support_tolerances",
        "score_weights",
    ):
        setup_values[field] = tuple(Decimal(item) for item in setup_values[field])
    return AppConfiguration(
        watchlist=WatchlistConfig.model_validate(values["watchlist"]),
        risk=RiskPolicy.model_validate(values["risk"]),
        regime=RegimePolicy(**regime_values),
        setup=SetupPolicy.model_validate(setup_values),
        source=SourcePolicy.model_validate(values["source"]),
    )


class DirectoryConfigurationRepository:
    """Trusted fixed-file bootstrap repository used by the public application API."""

    def __init__(self, root: Path, staging_root: Path | None = None) -> None:
        self._root = Path(root)
        self._staging_root = Path(staging_root) if staging_root is not None else self._root

    def load(self) -> AppConfiguration:
        return load_configuration(self._root)


def _jsonable(value: object) -> object:
    return json.loads(
        json.dumps(value, default=lambda item: getattr(item, "value", str(item)))
    )


class ConfigService:
    def __init__(self, repository: ConfigurationRepository) -> None:
        self._repository = repository

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
        factory = repository_factory or DirectoryConfigurationRepository
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
