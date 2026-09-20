"""Safe fixed-name YAML configuration loading."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml

from finance_research_agent.domain.policies import (
    AppConfiguration,
    RiskPolicy,
    SetupPolicy,
    SourcePolicy,
    WatchlistConfig,
    canonical_model_hash,
)
from finance_research_agent.domain.regime import RegimeComponent, RegimePolicy

FIXED_FILES = {
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


def _read(root: Path, filename: str) -> dict[str, Any]:
    path = root / filename
    if path.parent != root or path.name != filename:
        raise ValueError("configuration paths must be fixed filenames")
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{filename} must contain a mapping")
    return cast(dict[str, Any], _tuplify(value))


def load_configuration(root: Path) -> AppConfiguration:
    """Load exactly the five approved files beneath *root*."""
    values = {name: _read(root, filename) for name, filename in FIXED_FILES.items()}
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


def configuration_hashes(configuration: AppConfiguration) -> dict[str, str]:
    return {
        name: canonical_model_hash(policy)
        for name, policy in (
            ("watchlist", configuration.watchlist),
            ("risk", configuration.risk),
            ("setup", configuration.setup),
            ("source", configuration.source),
        )
    }


class YamlConfigurationRepository:
    """Adapter that owns the fixed local YAML configuration filenames."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def load(self) -> AppConfiguration:
        return load_configuration(self._root)


class YamlWatchlistRepository:
    """Atomic YAML watchlist adapter with staging and directory fsync."""

    def __init__(self, root: Path, staging_root: Path | None = None) -> None:
        self._root = Path(root)
        self._staging_root = Path(staging_root) if staging_root is not None else self._root

    def load(self) -> WatchlistConfig:
        return WatchlistConfig.model_validate(_tuplify(_read(self._root, "watchlist.yaml")))

    def replace(self, configuration: WatchlistConfig) -> None:
        self._staging_root.mkdir(parents=True, exist_ok=True)
        staging = self._staging_root / ".watchlist.yaml.staging"
        text = yaml.safe_dump(
            configuration.model_dump(mode="json"), sort_keys=False, allow_unicode=False
        )
        with staging.open("w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        validated = WatchlistConfig.model_validate(
            _tuplify(_read(self._staging_root, staging.name))
        )
        if validated != configuration:
            raise ValueError("staged watchlist failed validation round trip")
        os.replace(staging, self._root / "watchlist.yaml")
        directory_fd = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
