"""Safe fixed-name YAML configuration loading."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml

from finance_research_agent.application.config_service import (
    FIXED_CONFIGURATION_FILES,
    DirectoryConfigurationRepository,
    _read_configuration_file,
    _tuplify,
)
from finance_research_agent.domain.policies import (
    AppConfiguration,
    WatchlistConfig,
    canonical_model_hash,
)

FIXED_FILES = FIXED_CONFIGURATION_FILES


def _read(root: Path, filename: str) -> dict[str, Any]:
    return _read_configuration_file(root, filename)


def load_configuration(root: Path) -> AppConfiguration:
    """Compatibility export for the trusted application bootstrap loader."""

    return DirectoryConfigurationRepository(root).load()


def configuration_hashes(configuration: AppConfiguration) -> dict[str, str]:
    return {
        name: canonical_model_hash(policy)
        for name, policy in (
            ("watchlist", configuration.watchlist),
            ("risk", configuration.risk),
            ("regime", configuration.regime),
            ("setup", configuration.setup),
            ("source", configuration.source),
        )
    }


class YamlConfigurationRepository(DirectoryConfigurationRepository):
    """Adapter that owns the fixed local YAML configuration filenames."""

class YamlWatchlistRepository:
    """Atomic YAML watchlist adapter with staging and directory fsync."""

    _lock_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(self, root: Path, staging_root: Path | None = None) -> None:
        self._root = Path(root)
        # The optional argument is retained for source compatibility, but a
        # transaction's staging file must always be a sibling of its target.
        self._staging_root = self._root

    @classmethod
    def _lock_for(cls, target: Path) -> threading.RLock:
        key = str(target.resolve())
        with cls._lock_guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._locks[key] = lock
            return lock

    def load(self) -> WatchlistConfig:
        return WatchlistConfig.model_validate(_tuplify(_read(self._root, "watchlist.yaml")))

    def replace(self, configuration: WatchlistConfig) -> None:
        target = self._root / "watchlist.yaml"
        with self._lock_for(target):
            self._replace_unchecked(configuration)

    def replace_if_version(
        self, expected_version: str, configuration: WatchlistConfig
    ) -> bool:
        target = self._root / "watchlist.yaml"
        with self._lock_for(target):
            current = self.load()
            if current.version != expected_version:
                return False
            expected_next = str(int(expected_version) + 1)
            if configuration.version != expected_next:
                raise ValueError("replacement version must increment the expected version by one")
            self._replace_unchecked(configuration)
            return True

    def _replace_unchecked(self, configuration: WatchlistConfig) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / "watchlist.yaml"
        staging = target.with_name(".watchlist.yaml.staging")
        text = yaml.safe_dump(
            configuration.model_dump(mode="json"), sort_keys=False, allow_unicode=False
        )
        with staging.open("w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            validated = WatchlistConfig.model_validate(_tuplify(_read(self._root, staging.name)))
            if validated != configuration:
                raise ValueError("staged watchlist failed validation round trip")
            os.fsync(stream.fileno())
        os.replace(staging, target)
        directory_fd = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
