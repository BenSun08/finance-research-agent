from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest

from finance_research_agent.application.config_service import (
    ConfigService,
    DirectoryConfigurationRepository,
)
from finance_research_agent.application.ports import Clock, MarketDataProvider, TradingCalendar
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.models import (
    CompletedDailyBar,
    InstrumentIdentity,
    PriceObservation,
    ProviderFailure,
)
from finance_research_agent.domain.policies import WatchlistConfig, canonical_model_hash
from finance_research_agent.domain.types import FrozenMap

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config" / "examples"
MARKET_DATE = date(2026, 9, 28)
COMPLETED_AT = datetime(2026, 9, 28, 12, 45, tzinfo=UTC)


class _Calendar:
    def is_trading_day(self, market_date: date) -> bool:
        return market_date.weekday() < 5

    def session_open_close(self, market_date: date) -> tuple[datetime, datetime]:
        raise AssertionError("session times are not needed for collection")


class _Clock:
    def now_utc(self) -> datetime:
        return COMPLETED_AT


class _MarketData:
    def __init__(self) -> None:
        self.requested_symbols: tuple[str, ...] | None = None
        self.daily_request: tuple[date, date, tuple[date, ...], date] | None = None

    def fetch_instruments(
        self, symbols: Sequence[str]
    ) -> Mapping[str, InstrumentIdentity | ProviderFailure]:
        self.requested_symbols = tuple(symbols)
        return {symbol: _failure(symbol) for symbol in symbols}

    def fetch_daily_bars(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        expected_sessions: tuple[date, ...] | None = None,
        completed_through_session: date | None = None,
        evidence_cutoff_at: datetime | None = None,
        instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
    ) -> Mapping[str, tuple[CompletedDailyBar, ...] | ProviderFailure]:
        assert expected_sessions is not None
        assert completed_through_session is not None
        self.daily_request = (start, end, expected_sessions, completed_through_session)
        return {symbol: _failure(symbol) for symbol in symbols}

    def fetch_premarket_observations(
        self,
        symbols: Sequence[str],
        as_of: datetime | None,
        *,
        instrument_identities: Mapping[str, InstrumentIdentity] | None = None,
    ) -> Mapping[str, PriceObservation | ProviderFailure]:
        return {symbol: _failure(symbol) for symbol in symbols}


def _failure(symbol: str) -> ProviderFailure:
    return ProviderFailure(
        provider="alpaca",
        symbol=symbol,
        error_code=ErrorCode.PROVIDER_NO_DATA,
        retryable=False,
        message="offline fixture",
    )


def _run(valid_packet, *, snapshot=None):
    configuration = snapshot or ConfigService(
        DirectoryConfigurationRepository(CONFIG_ROOT)
    ).validate_and_snapshot()
    return valid_packet.run.model_copy(
        update={
            "run_id": "premarket-2026-09-28-r1",
            "market_date": MARKET_DATE,
            "evidence_cutoff_at": None,
            "configuration_snapshot": configuration,
        }
    )


def test_collect_market_data_for_run_uses_frozen_watchlist_and_history_policy(valid_packet) -> None:
    from finance_research_agent.application.collection_service import (
        collect_market_data_for_run,
    )

    run = _run(valid_packet)
    market_data = _MarketData()

    collection = collect_market_data_for_run(
        run,
        cast(MarketDataProvider, market_data),
        cast(Clock, _Clock()),
        cast(TradingCalendar, _Calendar()),
    )

    assert market_data.requested_symbols is not None
    assert market_data.requested_symbols[:3] == ("AAPL", "SPY", "XLK")
    assert set(run.configuration_snapshot.radar_universe).issubset(
        market_data.requested_symbols
    )
    assert len(market_data.requested_symbols) == len(set(market_data.requested_symbols))
    assert market_data.daily_request is not None
    start, end, sessions, completed_through = market_data.daily_request
    assert len(sessions) == 252
    assert sessions[0] == start
    assert sessions[-1] == end == completed_through
    assert sessions[-1] < MARKET_DATE
    assert collection.completed_at == COMPLETED_AT


def test_collect_market_data_for_run_rejects_missing_frozen_policy_snapshot(valid_packet) -> None:
    from finance_research_agent.application.collection_service import (
        collect_market_data_for_run,
    )

    run = _run(
        valid_packet,
        snapshot=valid_packet.run.configuration_snapshot.model_copy(
            update={"policies": None, "policy_hashes": None}
        ),
    )
    market_data = _MarketData()

    with pytest.raises(ValueError, match="missing frozen policies"):
        collect_market_data_for_run(
            run,
            cast(MarketDataProvider, market_data),
            cast(Clock, _Clock()),
            cast(TradingCalendar, _Calendar()),
        )

    assert market_data.requested_symbols is None


def test_collect_market_data_for_run_rejects_missing_collection_policy(valid_packet) -> None:
    from finance_research_agent.application.collection_service import (
        collect_market_data_for_run,
    )

    snapshot = ConfigService(
        DirectoryConfigurationRepository(CONFIG_ROOT)
    ).validate_and_snapshot()
    incomplete_snapshot = snapshot.model_copy(
        update={"policies": FrozenMap({"watchlist": snapshot.policies["watchlist"]})}
    )
    market_data = _MarketData()

    with pytest.raises(ValueError, match="policy set"):
        collect_market_data_for_run(
            _run(valid_packet, snapshot=incomplete_snapshot),
            cast(MarketDataProvider, market_data),
            cast(Clock, _Clock()),
            cast(TradingCalendar, _Calendar()),
        )

    assert market_data.requested_symbols is None


def test_collect_market_data_for_run_rejects_frozen_policy_hash_mismatch(valid_packet) -> None:
    from finance_research_agent.application.collection_service import (
        collect_market_data_for_run,
    )

    snapshot = ConfigService(
        DirectoryConfigurationRepository(CONFIG_ROOT)
    ).validate_and_snapshot()
    invalid_snapshot = snapshot.model_copy(
        update={
            "policy_hashes": FrozenMap(
                {**dict(snapshot.policy_hashes or {}), "watchlist": "f" * 64}
            )
        }
    )
    market_data = _MarketData()

    with pytest.raises(ValueError, match="configuration snapshot"):
        collect_market_data_for_run(
            _run(valid_packet, snapshot=invalid_snapshot),
            cast(MarketDataProvider, market_data),
            cast(Clock, _Clock()),
            cast(TradingCalendar, _Calendar()),
        )

    assert market_data.requested_symbols is None


def test_collect_market_data_for_run_rejects_frozen_file_hash_mismatch(valid_packet) -> None:
    from finance_research_agent.application.collection_service import (
        collect_market_data_for_run,
    )

    snapshot = ConfigService(
        DirectoryConfigurationRepository(CONFIG_ROOT)
    ).validate_and_snapshot()
    tampered_snapshot = snapshot.model_copy(
        update={
            "file_hashes": FrozenMap(
                {**dict(snapshot.file_hashes), "watchlist.yaml": "f" * 64}
            )
        }
    )
    market_data = _MarketData()

    with pytest.raises(ValueError, match="file hashes"):
        collect_market_data_for_run(
            _run(valid_packet, snapshot=tampered_snapshot),
            cast(MarketDataProvider, market_data),
            cast(Clock, _Clock()),
            cast(TradingCalendar, _Calendar()),
        )

    assert market_data.requested_symbols is None


def test_collect_market_data_for_run_rejects_self_consistent_but_stale_snapshot_hashes(
    valid_packet,
) -> None:
    from finance_research_agent.application.collection_service import (
        collect_market_data_for_run,
    )

    snapshot = ConfigService(
        DirectoryConfigurationRepository(CONFIG_ROOT)
    ).validate_and_snapshot()
    policy_data = snapshot.model_dump(mode="json")["policies"]
    policy_data["watchlist"]["items"][0]["symbol"] = "NVDA"
    changed_watchlist = WatchlistConfig.model_validate(policy_data["watchlist"])
    changed_hash = canonical_model_hash(changed_watchlist)
    tampered_snapshot = snapshot.model_copy(
        update={
            "policies": FrozenMap(policy_data),
            "policy_hashes": FrozenMap({**dict(snapshot.policy_hashes), "watchlist": changed_hash}),
            "file_hashes": FrozenMap(
                {**dict(snapshot.file_hashes), "watchlist.yaml": changed_hash}
            ),
        }
    )
    market_data = _MarketData()

    with pytest.raises(ValueError, match="configuration snapshot"):
        collect_market_data_for_run(
            _run(valid_packet, snapshot=tampered_snapshot),
            cast(MarketDataProvider, market_data),
            cast(Clock, _Clock()),
            cast(TradingCalendar, _Calendar()),
        )

    assert market_data.requested_symbols is None


def test_collect_market_data_for_run_rejects_radar_universe_drift(valid_packet) -> None:
    from finance_research_agent.application.collection_service import (
        collect_market_data_for_run,
    )

    snapshot = ConfigService(
        DirectoryConfigurationRepository(CONFIG_ROOT)
    ).validate_and_snapshot()
    tampered_snapshot = snapshot.model_copy(update={"radar_universe": ("NVDA",)})
    market_data = _MarketData()

    with pytest.raises(ValueError, match="configuration snapshot"):
        collect_market_data_for_run(
            _run(valid_packet, snapshot=tampered_snapshot),
            cast(MarketDataProvider, market_data),
            cast(Clock, _Clock()),
            cast(TradingCalendar, _Calendar()),
        )

    assert market_data.requested_symbols is None


def test_collect_market_data_for_run_rejects_snapshot_policy_version_mismatch(
    valid_packet,
) -> None:
    from finance_research_agent.application.collection_service import (
        collect_market_data_for_run,
    )

    snapshot = ConfigService(
        DirectoryConfigurationRepository(CONFIG_ROOT)
    ).validate_and_snapshot()
    tampered_snapshot = snapshot.model_copy(update={"watchlist_version": "stale-version"})
    market_data = _MarketData()

    with pytest.raises(ValueError, match="versions"):
        collect_market_data_for_run(
            _run(valid_packet, snapshot=tampered_snapshot),
            cast(MarketDataProvider, market_data),
            cast(Clock, _Clock()),
            cast(TradingCalendar, _Calendar()),
        )

    assert market_data.requested_symbols is None


def test_collect_market_data_for_run_rejects_collection_after_cutoff(valid_packet) -> None:
    from finance_research_agent.application.collection_service import (
        collect_market_data_for_run,
    )

    market_data = _MarketData()
    run = _run(valid_packet).model_copy(update={"evidence_cutoff_at": COMPLETED_AT})

    with pytest.raises(ValueError, match="after evidence cutoff"):
        collect_market_data_for_run(
            run,
            cast(MarketDataProvider, market_data),
            cast(Clock, _Clock()),
            cast(TradingCalendar, _Calendar()),
        )

    assert market_data.requested_symbols is None
