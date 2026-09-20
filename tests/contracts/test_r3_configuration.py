import shutil
from pathlib import Path

from finance_research_agent.adapters.yaml_config import (
    YamlConfigurationRepository,
    YamlWatchlistRepository,
    configuration_hashes,
    load_configuration,
)
from finance_research_agent.application.config_service import ConfigService
from finance_research_agent.application.watchlist_service import (
    ConfigurationVersionConflict,
    WatchlistService,
)
from finance_research_agent.domain.policies import WatchlistConfig, WatchlistItem

EXAMPLES = Path(__file__).parents[2] / "config" / "examples"


def test_watchlist_rejects_more_than_thirty_unique_research_names() -> None:
    items = tuple(
        WatchlistItem(
            symbol=f"A{index:02d}",
            role="RESEARCH_ONLY",
            research_rationale="Synthetic research example.",
        )
        for index in range(31)
    )

    try:
        WatchlistConfig(version="1", items=items)
    except ValueError as error:
        assert "30" in str(error)
    else:
        raise AssertionError("watchlist with 31 items must be rejected")


def test_all_shipped_policy_examples_load_with_stable_hashes() -> None:
    first = load_configuration(EXAMPLES)
    second = load_configuration(EXAMPLES)

    assert set(configuration_hashes(first)) == {"watchlist", "risk", "regime", "setup", "source"}
    assert configuration_hashes(first) == configuration_hashes(second)
    assert first.risk.sizing_enabled is False
    assert first.risk.planning_capital_usd is None
    assert first.risk.regime_risk_multipliers["PERMISSIVE"] == 1


def test_watchlist_service_increments_version_and_rejects_stale_mutation(tmp_path: Path) -> None:
    root = tmp_path / "config"
    shutil.copytree(EXAMPLES, root)
    service = WatchlistService(YamlWatchlistRepository(root, tmp_path / "staging"))
    item = WatchlistItem(
        symbol="MSFT", role="RESEARCH_ONLY", research_rationale="Synthetic addition."
    )

    change = service.upsert("1", item)
    assert (change.before_version, change.after_version) == ("1", "2")
    assert service.list().items[-1] == item
    try:
        service.remove("1", "MSFT")
    except ConfigurationVersionConflict:
        pass
    else:
        raise AssertionError("stale mutation must not be accepted")
    assert service.list().version == "2"


def test_configuration_snapshot_contains_detached_policy_identity() -> None:
    snapshot = ConfigService(YamlConfigurationRepository(EXAMPLES)).validate_and_snapshot()

    assert snapshot.policy_hashes is not None
    assert snapshot.radar_universe == tuple(sorted(snapshot.radar_universe))
    assert snapshot.policies is not None
    assert snapshot.policies["risk"]["sizing_enabled"] is False
