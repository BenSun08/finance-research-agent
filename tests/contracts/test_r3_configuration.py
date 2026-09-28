import shutil
from pathlib import Path

import pytest

import finance_research_agent.domain.enums as enums
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
from finance_research_agent.domain.policies import SourcePolicy, WatchlistConfig, WatchlistItem

EXAMPLES = Path(__file__).parents[2] / "config" / "examples"


def _source_role_enum() -> type:
    assert hasattr(enums, "SourceRole"), "SourceRole must define the quality role vocabulary"
    return getattr(enums, "SourceRole")


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
    source_role = _source_role_enum()
    first = load_configuration(EXAMPLES)
    second = load_configuration(EXAMPLES)

    assert set(configuration_hashes(first)) == {"watchlist", "risk", "regime", "setup", "source"}
    assert configuration_hashes(first) == configuration_hashes(second)
    assert first.risk.sizing_enabled is False
    assert first.risk.planning_capital_usd is None
    assert first.risk.regime_risk_multipliers["PERMISSIVE"] == 1
    assert first.source.quality_source_roles == (
        source_role.MARKET_DATA,
        source_role.MARKET_CALENDAR,
    )


def test_source_policy_requires_market_data_and_calendar_roles() -> None:
    source_role = _source_role_enum()
    policy_data = load_configuration(EXAMPLES).source.model_dump()
    policy_data.pop("quality_source_roles")
    with pytest.raises(ValueError):
        SourcePolicy.model_validate(policy_data)

    for roles in [(), (source_role.MARKET_DATA,), (source_role.MARKET_CALENDAR,)]:
        policy_data["quality_source_roles"] = roles
        with pytest.raises(ValueError):
            SourcePolicy.model_validate(policy_data)


def test_source_policy_rejects_duplicate_non_tuple_or_unknown_roles() -> None:
    source_role = _source_role_enum()
    policy_data = load_configuration(EXAMPLES).source.model_dump()
    policy_data["quality_source_roles"] = (
        source_role.MARKET_DATA,
        source_role.MARKET_CALENDAR,
        source_role.MARKET_DATA,
    )
    with pytest.raises(ValueError):
        SourcePolicy.model_validate(policy_data)

    policy_data["quality_source_roles"] = [
        source_role.MARKET_DATA,
        source_role.MARKET_CALENDAR,
    ]
    with pytest.raises(ValueError):
        SourcePolicy.model_validate(policy_data)

    for roles in [
        ("unconfigured-role", source_role.MARKET_DATA, source_role.MARKET_CALENDAR),
        (object(), source_role.MARKET_DATA, source_role.MARKET_CALENDAR),
    ]:
        policy_data["quality_source_roles"] = roles
        with pytest.raises(ValueError):
            SourcePolicy.model_validate(policy_data)

    policy = load_configuration(EXAMPLES).source
    with pytest.raises((AttributeError, ValueError)):
        policy.quality_source_roles = ()


def test_quality_source_roles_change_configuration_snapshot_hash(tmp_path: Path) -> None:
    base = tmp_path / "base"
    expanded = tmp_path / "expanded"
    shutil.copytree(EXAMPLES, base)
    shutil.copytree(EXAMPLES, expanded)
    base_policy_path = base / "source-policy.yaml"
    source_path = expanded / "source-policy.yaml"
    base_text = base_policy_path.read_text(encoding="utf-8")
    source_text = source_path.read_text(encoding="utf-8")
    declared_roles = "quality_source_roles: [market-data, market-calendar]"
    assert declared_roles in base_text and declared_roles in source_text
    source_path.write_text(
        source_text.replace(
            declared_roles,
            "quality_source_roles: [market-data, market-calendar, macro-calendar]",
            1,
        ),
        encoding="utf-8",
    )

    base_snapshot = ConfigService(YamlConfigurationRepository(base)).validate_and_snapshot()
    expanded_snapshot = ConfigService(YamlConfigurationRepository(expanded)).validate_and_snapshot()

    assert base_snapshot.policy_hashes is not None
    assert expanded_snapshot.policy_hashes is not None
    assert base_snapshot.policy_hashes["source"] != expanded_snapshot.policy_hashes["source"]
    assert base_snapshot.content_hash_sha256 != expanded_snapshot.content_hash_sha256


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
