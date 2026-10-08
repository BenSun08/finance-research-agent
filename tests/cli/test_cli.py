"""Tests for the diagnostic and frozen-replay command-line boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr

from finance_research_agent.adapters import cli
from finance_research_agent.adapters.cli import main
from finance_research_agent.adapters.yaml_config import YamlConfigurationRepository
from finance_research_agent.application.config_service import ConfigService
from finance_research_agent.application.operations import ProductAOperation, SystemStatusResult
from finance_research_agent.application.replay_service import ArtifactNotFoundError
from finance_research_agent.domain.models import ConfigurationSnapshot, PublishedRunBundle
from finance_research_agent.domain.types import FrozenMap
from finance_research_agent.settings import Settings


class _Services:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def dispatch(self, operation: str, arguments_json: str) -> object:
        self.calls.append((operation, arguments_json))
        if operation == ProductAOperation.GET_SYSTEM_STATUS:
            return SystemStatusResult(
                configuration_ready=True,
                market_data_ready=False,
                market_calendar_ready=True,
                current_market_date=date(2026, 9, 30),
                diagnostics=(),
            )
        return {"safe": "configuration snapshot"}


def test_help_lists_only_diagnostic_and_frozen_replay_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"], services=_Services())

    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "status" in output
    assert "config" in output
    assert "replay" in output
    assert "{status,config,replay}" in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["mystery"],
        ["status", "/tmp/secret.yml"],
        ["config", "--data-dir", "/tmp/secret"],
        ["replay", "premarket-2026-09-30-r1", "--market-date", "2026-09-29"],
        ["prepare"],
        ["publish", "premarket-2026-09-30-r1"],
        ["watchlist", "add", "MSFT"],
    ],
)
def test_unknown_paths_overrides_and_mutations_fail_before_dispatch(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    services = _Services()

    with pytest.raises(SystemExit) as error:
        main(arguments, services=services)

    assert error.value.code == 2
    assert services.calls == []
    assert capsys.readouterr().err


def test_status_dispatches_exact_read_only_operation_and_emits_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = _Services()

    result = main(["status"], services=services)

    assert result == 0
    assert services.calls == [(ProductAOperation.GET_SYSTEM_STATUS.value, "{}")]
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": "0.1",
        "configuration_ready": True,
        "market_data_ready": False,
        "market_calendar_ready": True,
        "current_market_date": "2026-09-30",
        "diagnostics": [],
    }


def test_config_dispatches_validation_without_accepting_or_echoing_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = _Services()

    result = main(["config"], services=services)

    assert result == 0
    assert services.calls == [(ProductAOperation.VALIDATE_CONFIGURATION.value, "{}")]
    assert json.loads(capsys.readouterr().out) == {"safe": "configuration snapshot"}


def test_replay_passes_only_run_id_to_frozen_artifact_callback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = _Services()
    replay_calls: list[str] = []

    def replay(run_id: str) -> dict[str, Any]:
        replay_calls.append(run_id)
        return {"run_id": run_id, "json_matches": True, "markdown_matches": True}

    result = main(
        ["replay", "premarket-2026-09-30-r1"],
        services=services,
        replay=replay,
    )

    assert result == 0
    assert replay_calls == ["premarket-2026-09-30-r1"]
    assert services.calls == []
    assert json.loads(capsys.readouterr().out) == {
        "run_id": "premarket-2026-09-30-r1",
        "json_matches": True,
        "markdown_matches": True,
    }


def test_replay_rejects_malformed_run_id_before_loading_artifacts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = _Services()
    replay_calls: list[str] = []

    with pytest.raises(SystemExit) as error:
        main(
            ["replay", "../../private-file"],
            services=services,
            replay=replay_calls.append,
        )

    assert error.value.code == 2
    assert services.calls == []
    assert replay_calls == []
    assert capsys.readouterr().err


def test_replay_fails_closed_if_the_default_runtime_has_no_replay_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _Services()
    monkeypatch.setattr(
        cli,
        "_default_replay",
        lambda: cast(cli.Replay, None),
    )

    with pytest.raises(SystemExit) as error:
        main(["replay", "premarket-2026-09-30-r1"], services=services)

    assert error.value.code == 2
    assert services.calls == []


def test_default_status_runtime_uses_local_configuration_and_no_provider_requests(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copytree(Path(__file__).parents[2] / "config" / "examples", tmp_path / "config")
    ConfigService(YamlConfigurationRepository(tmp_path / "config")).validate_and_snapshot()
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    captured: dict[str, Any] = {}
    original_factory = cli._default_runtime

    def record_runtime() -> tuple[object, cli.Replay]:
        services, replay = original_factory()
        captured["replay"] = replay
        return services, replay

    monkeypatch.setattr(cli, "_default_runtime", record_runtime)

    result = main(["status"])

    assert result == 0
    status = json.loads(capsys.readouterr().out)
    assert status["configuration_ready"] is True
    assert status["market_data_ready"] is False
    assert status["market_calendar_ready"] is True
    with pytest.raises(ArtifactNotFoundError):
        captured["replay"]("premarket-2026-09-30-r1")

    result = main(["config"])

    assert result == 0
    configuration = json.loads(capsys.readouterr().out)
    assert "content_hash_sha256" in configuration
    assert "policy_versions" in configuration
    assert "policies" not in configuration
    assert "radar_universe" not in configuration


def test_default_replay_uses_the_stored_bundle_without_loading_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "premarket-2026-09-30-r1"
    snapshot = ConfigurationSnapshot(
        content_hash_sha256="a" * 64,
        file_hashes=FrozenMap({"watchlist.yaml": "b" * 64}),
        watchlist_version="1",
        regime_policy_version="1",
        setup_policy_version="1",
        risk_policy_version="1",
        source_policy_version="1",
    )
    bundle = SimpleNamespace(
        run=SimpleNamespace(configuration_snapshot=snapshot),
    )

    class _Reader:
        def __init__(self, root: Path) -> None:
            self.root = root
            self.calls: list[str] = []

        def load_published_bundle(self, requested_run_id: str) -> object:
            self.calls.append(requested_run_id)
            return bundle

    class _Configuration:
        def __init__(self, root: Path) -> None:
            self.root = root

        def load(self) -> object:
            raise AssertionError("frozen replay must not read current configuration")

    reader = _Reader(tmp_path)
    replay_inputs: list[tuple[str, object]] = []

    def fake_replay(repository: object, requested_run_id: str, versions: object) -> dict[str, str]:
        assert repository is reader
        replay_inputs.append((requested_run_id, versions))
        return {"result": "replayed"}

    monkeypatch.setattr(cli, "Settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "YamlConfigurationRepository", _Configuration)
    monkeypatch.setattr(cli, "FileSystemRunRepository", lambda root, **kwargs: reader)
    monkeypatch.setattr(cli, "replay_published_artifact", fake_replay)

    result = main(["replay", run_id])

    assert result == 0
    assert reader.calls == [run_id]
    assert replay_inputs[0][0] == run_id
    assert replay_inputs[0][1].watchlist_version == "1"
    assert json.loads(capsys.readouterr().out) == {"result": "replayed"}


def test_market_readiness_requires_both_credentials() -> None:
    configured = cli._ConfiguredMarketDataReadiness(
        Settings(
            alpaca_api_key=SecretStr("key"),
            alpaca_api_secret=SecretStr("secret"),
        )
    )
    missing_secret = cli._ConfiguredMarketDataReadiness(
        Settings(alpaca_api_key=SecretStr("key"))
    )

    assert configured.readiness().available is True
    assert missing_secret.readiness().available is False


def test_json_value_converts_dataclass_values() -> None:
    @dataclass(frozen=True)
    class Summary:
        ready: bool

    assert cli._json_value(Summary(ready=True)) == {"ready": True}


def test_replay_summary_prints_hashes_and_versions_without_bundle_or_markdown() -> None:
    result = cli.ArtifactReplayResult(
        run_id="premarket-2026-09-30-r1",
        bundle=PublishedRunBundle.model_construct(),
        report_markdown="private full report content",
        json_matches=True,
        markdown_matches=False,
        stored_json_sha256="a" * 64,
        replayed_json_sha256="a" * 64,
        stored_markdown_sha256="b" * 64,
        replayed_markdown_sha256=None,
        component_version_mismatches=("skill_version",),
    )

    output = cli._replay_summary(result)

    assert output["json_matches"] is True
    assert output["component_version_mismatches"] == ["skill_version"]
    assert output["current_policy_config_compared"] is False
    assert "bundle" not in output
    assert "report_markdown" not in output


@pytest.mark.parametrize("command", ["status", "config", "replay"])
def test_service_failures_return_redacted_json_without_tracebacks(command, capsys) -> None:
    class Broken:
        def dispatch(self, *args):
            raise RuntimeError("ALPACA-secret-canary /private/config.yaml")

    def broken_replay(run_id):
        raise RuntimeError("ALPACA-secret-canary /private/bundle.json")

    arguments = [command] + (["premarket-2026-09-30-r1"] if command == "replay" else [])
    assert main(arguments, services=Broken(), replay=broken_replay) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["code"] == "INTERNAL_ERROR"
    assert output.err == ""
    assert "canary" not in output.out
    assert "/private/" not in output.out


def test_bootstrap_and_serialization_failures_are_redacted(monkeypatch, capsys) -> None:
    def broken_bootstrap():
        raise RuntimeError("secret-canary /private/config")

    monkeypatch.setattr(cli, "_default_runtime", broken_bootstrap)
    assert main(["config"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "INTERNAL_ERROR"

    class Unserializable:
        def dispatch(self, *args):
            return object()

    assert main(["config"], services=Unserializable()) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "INTERNAL_ERROR"


def test_frozen_replay_never_constructs_settings_or_diagnostic_services(
    monkeypatch, tmp_path, capsys
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("credentials and current configuration must stay unread")

    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "Settings", forbidden)
    monkeypatch.setattr(cli, "ExchangeCalendarAdapter", forbidden)
    snapshot = ConfigurationSnapshot(
        content_hash_sha256="a" * 64,
        file_hashes=FrozenMap({"watchlist.yaml": "b" * 64}),
        watchlist_version="1", regime_policy_version="1", setup_policy_version="1",
        risk_policy_version="1", source_policy_version="1",
    )
    class Reader:
        def load_published_bundle(self, run_id):
            return SimpleNamespace(run=SimpleNamespace(configuration_snapshot=snapshot))

    def reader(root, **kwargs):
        assert root == tmp_path
        return Reader()

    monkeypatch.setattr(cli, "FileSystemRunRepository", reader)
    monkeypatch.setattr(cli, "replay_published_artifact", lambda *args: {"replayed": True})
    assert main(["replay", "premarket-2026-09-30-r1"]) == 0
    assert json.loads(capsys.readouterr().out) == {"replayed": True}


def test_injected_replay_does_not_bootstrap_diagnostic_services(monkeypatch, capsys) -> None:
    def forbidden():
        raise AssertionError("diagnostic runtime must not be constructed")
    monkeypatch.setattr(cli, "_default_runtime", forbidden)
    assert main(["replay", "premarket-2026-09-30-r1"], replay=lambda _: {}) == 0
    assert json.loads(capsys.readouterr().out) == {}


@pytest.mark.parametrize("command", ["status", "config", "replay"])
def test_read_only_commands_never_create_storage_layout(command, monkeypatch, tmp_path, capsys):
    root = tmp_path / "missing"
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(root))
    monkeypatch.setattr(cli, "Settings", lambda: Settings(data_dir=root))
    arguments = [command] + (["premarket-2026-09-30-r1"] if command == "replay" else [])
    main(arguments)
    capsys.readouterr()
    assert not root.exists()


def test_real_console_redacts_invalid_configuration_and_leaves_tree_unchanged(tmp_path):
    project = Path(__file__).parents[2]
    copytree(project / "config" / "examples", tmp_path / "config")
    risk_path = tmp_path / "config" / "risk-policy.yaml"
    # Locate the canonical risk policy without relying on operator-specific paths.
    if not risk_path.exists():
        risk_path = next((tmp_path / "config").glob("*risk*.yaml"))
    risk_path.write_text(risk_path.read_text() + "\nprivate-canary-field: credential-canary\n")
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    result = subprocess.run(
        [sys.executable, "-c", "from finance_research_agent.adapters.cli import main; "
         "raise SystemExit(main())", "config"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(project / "src"),
             "AI_MARKET_RESEARCH_DATA_DIR": str(tmp_path),
             "ALPACA_API_KEY": "offline-canary", "ALPACA_API_SECRET": "offline-canary"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "INTERNAL_ERROR"
    assert result.stderr == ""
    assert "canary" not in result.stdout
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before
