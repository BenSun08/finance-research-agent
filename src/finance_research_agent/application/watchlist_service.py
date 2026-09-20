"""Version-checked, atomic local watchlist mutations."""

from __future__ import annotations

from dataclasses import dataclass

from finance_research_agent.application.ports import WatchlistRepository
from finance_research_agent.domain.policies import (
    WatchlistConfig,
    WatchlistItem,
    canonical_model_hash,
    validate_ticker,
)


class ConfigurationVersionConflict(ValueError):
    """The caller attempted to mutate a stale configuration version."""


@dataclass(frozen=True, slots=True)
class WatchlistChange:
    before_version: str
    after_version: str
    item: WatchlistItem | None
    removed_symbol: str | None
    content_hash_sha256: str


class WatchlistService:
    def __init__(self, repository: WatchlistRepository) -> None:
        self._repository = repository

    def list(self) -> WatchlistConfig:
        return self._repository.load()

    @staticmethod
    def _next(version: str) -> str:
        return str(int(version) + 1)

    def upsert(self, expected_version: str, item: WatchlistItem) -> WatchlistChange:
        current = self.list()
        if current.version != expected_version:
            raise ConfigurationVersionConflict(
                f"expected watchlist version {expected_version}, found {current.version}"
            )
        items = tuple(
            existing for existing in current.items if existing.symbol != item.symbol
        ) + (item,)
        updated = WatchlistConfig(version=self._next(current.version), items=items)
        if not self._repository.replace_if_version(expected_version, updated):
            raise ConfigurationVersionConflict(
                f"expected watchlist version {expected_version} was superseded"
            )
        return WatchlistChange(
            current.version, updated.version, item, None, canonical_model_hash(updated)
        )

    def remove(self, expected_version: str, symbol: str) -> WatchlistChange:
        validate_ticker(symbol)
        current = self.list()
        if current.version != expected_version:
            raise ConfigurationVersionConflict(
                f"expected watchlist version {expected_version}, found {current.version}"
            )
        updated = WatchlistConfig(
            version=self._next(current.version),
            items=tuple(item for item in current.items if item.symbol != symbol),
        )
        if not self._repository.replace_if_version(expected_version, updated):
            raise ConfigurationVersionConflict(
                f"expected watchlist version {expected_version} was superseded"
            )
        return WatchlistChange(
            current.version, updated.version, None, symbol, canonical_model_hash(updated)
        )
