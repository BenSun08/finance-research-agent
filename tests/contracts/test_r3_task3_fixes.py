from __future__ import annotations

import concurrent.futures
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from finance_research_agent.adapters import yaml_config
from finance_research_agent.adapters.yaml_config import (
    FIXED_FILES,
    YamlWatchlistRepository,
    load_configuration,
)
from finance_research_agent.application.config_service import ConfigService
from finance_research_agent.application.watchlist_service import (
    ConfigurationVersionConflict,
    WatchlistService,
)
from finance_research_agent.domain.policies import (
    SetupPolicy,
    SourcePolicy,
    WatchlistConfig,
    WatchlistItem,
    canonical_model_hash,
)

EXAMPLES = Path(__file__).parents[2] / "config" / "examples"


def _item(**overrides: Any) -> WatchlistItem:
    value: dict[str, Any] = {
        "symbol": "MSFT",
        "role": "RESEARCH_ONLY",
        "research_rationale": "Synthetic research addition.",
        "tags": ("synthetic",),
        "official_sources": ("https://example.test/ir",),
    }
    value.update(overrides)
    return WatchlistItem(**value)


def _copy_examples(tmp_path: Path) -> Path:
    root = tmp_path / "config"
    shutil.copytree(EXAMPLES, root)
    return root


def test_config_service_from_directories_projects_all_fixed_files_and_radar(tmp_path: Path) -> None:
    service = ConfigService.from_directories(EXAMPLES, tmp_path / "staging")

    snapshot = service.validate_and_snapshot()

    assert set(snapshot.file_hashes) == set(FIXED_FILES.values())
    assert set(snapshot.policy_hashes or {}) == set(FIXED_FILES)
    assert {"SPY", "QQQ", "IWM", "DIA", "HYG", "LQD"}.issubset(
        set(snapshot.radar_universe)
    )
    assert {"TLT", "UUP", "GLD", "USO", "VIX"}.issubset(set(snapshot.radar_universe))


def test_application_only_public_import_bootstraps_directory_service() -> None:
    source = str(EXAMPLES)
    package_root = str(EXAMPLES.parents[1] / "src")
    script = f"""
import sys
from pathlib import Path
from finance_research_agent.application.config_service import ConfigService

assert not any(name.startswith('finance_research_agent.adapters') for name in sys.modules)
service = ConfigService.from_directories(Path({source!r}), Path('/tmp/r3-application-only'))
assert service.validate_and_snapshot().watchlist_version == '1'
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = package_root
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=EXAMPLES.parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_yaml_formatting_does_not_change_canonical_policy_hash(tmp_path: Path) -> None:
    first_root = _copy_examples(tmp_path / "first")
    second_root = _copy_examples(tmp_path / "second")
    regime = yaml.safe_load((second_root / "regime-policy.yaml").read_text(encoding="utf-8"))
    (second_root / "regime-policy.yaml").write_text(
        yaml.safe_dump(regime, sort_keys=True),
        encoding="utf-8",
    )

    first = ConfigService.from_directories(first_root, tmp_path / "stage-1").validate_and_snapshot()
    second = ConfigService.from_directories(
        second_root, tmp_path / "stage-2"
    ).validate_and_snapshot()

    assert first.file_hashes["regime-policy.yaml"] == second.file_hashes["regime-policy.yaml"]


def test_snapshot_nested_policy_values_are_immutable() -> None:
    snapshot = ConfigService.from_directories(
        EXAMPLES, Path("/tmp/r3-unused")
    ).validate_and_snapshot()

    assert snapshot.policies is not None
    with pytest.raises(TypeError):
        snapshot.policies["risk"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.policies["risk"]["sizing_enabled"] = True  # type: ignore[index]


def test_policy_maps_are_deeply_immutable_without_changing_json_serialization() -> None:
    configuration = load_configuration(EXAMPLES)
    risk = configuration.risk
    source = configuration.source
    risk_hash = canonical_model_hash(risk)
    source_hash = canonical_model_hash(source)

    with pytest.raises(TypeError):
        risk.regime_risk_multipliers["PERMISSIVE"] = 0  # type: ignore[index]
    with pytest.raises(TypeError):
        source.freshness_by_data_type["market_daily_bars"] = 0  # type: ignore[index]
    with pytest.raises(TypeError):
        source.per_run_request_budgets["official_sources"] = 0  # type: ignore[index]
    with pytest.raises(TypeError):
        source.excerpt_limits["official_sources"] = 0  # type: ignore[index]

    assert canonical_model_hash(risk) == risk_hash
    assert canonical_model_hash(source) == source_hash
    assert isinstance(risk.model_dump(mode="json")["regime_risk_multipliers"], dict)
    assert isinstance(source.model_dump(mode="json")["freshness_by_data_type"], dict)


def test_configuration_rejects_unknown_fields_duplicate_symbols_and_caller_paths(
    tmp_path: Path,
) -> None:
    unknown = {"version": "1", "items": [], "unexpected": True}
    with pytest.raises(ValidationError):
        WatchlistConfig.model_validate(unknown)

    with pytest.raises(ValidationError):
        WatchlistConfig(
            version="1",
            items=(_item(symbol="AAPL"), _item(symbol="AAPL")),
        )

    with pytest.raises(ValueError, match="fixed|directory|caller"):
        ConfigService.from_directories(EXAMPLES / "watchlist.yaml", tmp_path)


@pytest.mark.parametrize(
    "value",
    ["A" * 17, "AAPL\u0430", "../AAPL", "AAPL;rm", "AAPL\u200b"],
)
def test_ticker_grammar_rejects_bounded_path_shell_confusable_and_zero_width_values(
    value: str,
) -> None:
    with pytest.raises(ValueError):
        _item(symbol=value)


@pytest.mark.parametrize("value", ["a" * 33, "../bad", "tag;rm", "tag\u202e"])
def test_tag_grammar_is_bounded_ascii_and_shell_safe(value: str) -> None:
    with pytest.raises(ValueError):
        _item(tags=(value,))


@pytest.mark.parametrize("value", ["研究笔记", "note\nwith newline", "note\u200b"])
def test_stored_prose_requires_printable_english(value: str) -> None:
    with pytest.raises(ValueError, match="English normalization required"):
        _item(research_rationale=value)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/ir",
        "https://user:pass@example.test/ir",
        "https://example.test/ir#fragment",
        "https://example.test:bad/ir",
        "https://example.test/%2e%2e/private",
        "https://example.test/%252e%252e/private",
        "https://example.test/%252525252e%252525252e/private",
        "https://example.test/%5c..%5cprivate",
        "https://example.test/ir\u200b",
    ],
)
def test_official_source_url_security_grammar(url: str) -> None:
    with pytest.raises(ValueError):
        _item(official_sources=(url,))


def test_policy_collections_ranges_weights_and_penalties_are_strict() -> None:
    with pytest.raises(ValidationError):
        SetupPolicy.model_validate(
            {
                "version": "1",
                "required_historical_sessions": 1,
                "minimum_price": "1",
                "minimum_median_dollar_volume": "1",
                "moving_average_windows": [1],
                "trend_slope_windows": [1],
                "breakout_lookback": 1,
                "atr_window": 1,
                "entry_zone_atr_buffers": ["0.1"],
                "extension_limits": ["0.1"],
                "pullback_support_tolerances": ["0.1"],
                "restrengthening_conditions": ["condition"],
                "minimum_reward_to_risk": "1",
                "plan_lifetime_sessions": 1,
                "earnings_blackout_sessions": 0,
                "score_weights": ["1", "0", "1", "1", "1", "1"],
                "penalty_names": [
                    "EXTENSION_PENALTY",
                    "EVENT_UNCERTAINTY_PENALTY",
                    "CORRELATION_CONCENTRATION_PENALTY",
                    "DATA_QUALITY_PENALTY",
                ],
            }
        )

    source = load_configuration(EXAMPLES).source.model_dump(mode="python")
    source["freshness_by_data_type"]["market_daily_bars"] = -1
    with pytest.raises(ValidationError):
        SourcePolicy.model_validate(source)


def test_watchlist_replace_is_same_directory_and_orders_reread_fsync_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_examples(tmp_path)
    repository = YamlWatchlistRepository(root, tmp_path / "different-staging-root")
    events: list[tuple[str, Path | None, Path | None]] = []
    original_read = yaml_config._read
    original_replace = yaml_config.os.replace

    def traced_read(read_root: Path, filename: str) -> dict[str, Any]:
        events.append(("read", read_root, None))
        return original_read(read_root, filename)

    def traced_fsync(fd: int) -> None:
        events.append(("fsync", None, None))

    def traced_replace(source: str | bytes | Path, target: str | bytes | Path) -> None:
        events.append(("replace", Path(source).parent, Path(target).parent))
        original_replace(source, target)

    monkeypatch.setattr(yaml_config, "_read", traced_read)
    monkeypatch.setattr(yaml_config.os, "fsync", traced_fsync)
    monkeypatch.setattr(yaml_config.os, "replace", traced_replace)

    current = repository.load()
    repository.replace(WatchlistConfig(version="2", items=current.items))

    assert events[0][0] == "read"
    assert [event[0] for event in events].count("replace") == 1
    replace_index = next(index for index, event in enumerate(events) if event[0] == "replace")
    assert any(event[0] == "fsync" for event in events[:replace_index])
    assert events[replace_index][1] == root
    assert events[replace_index][2] == root
    assert not (tmp_path / "different-staging-root" / ".watchlist.yaml.staging").exists()


def test_concurrent_same_version_mutations_have_one_winner(tmp_path: Path) -> None:
    root = _copy_examples(tmp_path)
    service = WatchlistService(YamlWatchlistRepository(root))
    items = (_item(symbol="MSFT"), _item(symbol="NVDA"))

    def mutate(item: WatchlistItem) -> str:
        try:
            return service.upsert("1", item).after_version
        except ConfigurationVersionConflict:
            return "conflict"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(mutate, items))

    assert sorted(results) == ["2", "conflict"]
    assert service.list().version == "2"
    assert len(service.list().items) == 2


def test_remove_validates_symbol_before_versioned_mutation(tmp_path: Path) -> None:
    root = _copy_examples(tmp_path)
    service = WatchlistService(YamlWatchlistRepository(root))

    with pytest.raises(ValueError):
        service.remove("1", "../AAPL")

    assert service.list().version == "1"


def test_setup_and_source_hashes_are_canonical_model_hashes() -> None:
    configuration = load_configuration(EXAMPLES)

    assert canonical_model_hash(configuration.setup) == canonical_model_hash(
        SetupPolicy.model_validate(configuration.setup.model_dump(mode="json"))
    )
