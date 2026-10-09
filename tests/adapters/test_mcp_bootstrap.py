"""Offline tests for lazy executable Product A stdio composition."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from shutil import copytree

import pytest
from mcp import Client

from finance_research_agent.adapters import mcp_bootstrap as bootstrap
from finance_research_agent.adapters.alpaca import AlpacaMarketDataProvider
from finance_research_agent.application.config_service import (
    ConfigService,
    DirectoryConfigurationRepository,
)
from finance_research_agent.application.operations import OPERATION_NAMES, SystemStatusResult
from finance_research_agent.settings import Settings

PROJECT = Path(__file__).resolve().parents[2]


class Dispatcher:
    def __init__(self):
        self.calls = []

    def dispatch(self, operation, arguments_json):
        self.calls.append((operation, arguments_json))
        return SystemStatusResult(
            configuration_ready=False,
            market_data_ready=False,
            market_calendar_ready=False,
            current_market_date=None,
            diagnostics=(),
        )


def forbidden(*args, **kwargs):
    raise AssertionError("startup dependency was touched")


def test_initialize_and_list_never_construct_services(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("private-canary invalid dotenv\n")
    monkeypatch.setattr(bootstrap, "create_default_services", forbidden)

    async def scenario():
        async with Client(bootstrap.create_stdio_server()) as client:
            tools = await client.list_tools()
        assert tuple(tool.name for tool in tools.tools) == OPERATION_NAMES

    asyncio.run(scenario())
    assert sorted(path.name for path in tmp_path.iterdir()) == [".env"]


def test_invalid_calls_never_construct_services():
    async def scenario():
        async with Client(bootstrap.create_stdio_server(services_factory=forbidden)) as client:
            result = await client.call_tool("unlisted_operation", {})
            assert result.is_error
            try:
                result = await client.call_tool("get_run_status", {"run_id": "private-path"})
            except Exception:
                pass
            else:
                assert result.is_error

    asyncio.run(scenario())


def test_lazy_dispatcher_validates_before_composition_and_caches_success():
    dispatcher = Dispatcher()
    constructions = []

    def factory():
        constructions.append(True)
        return dispatcher

    lazy = bootstrap.LazyApplicationDispatcher(factory)
    with pytest.raises(ValueError):
        lazy.dispatch("unknown", "{}")
    with pytest.raises(ValueError):
        lazy.dispatch("get_system_status", '{"deadline":"private"}')
    assert constructions == []
    lazy.dispatch("get_system_status", "{}")
    lazy.dispatch("get_system_status", "{}")
    assert constructions == [True]
    assert dispatcher.calls == [("get_system_status", "{}"), ("get_system_status", "{}")]


def test_failed_composition_is_redacted_and_not_cached():
    dispatcher = Dispatcher()
    constructions = []

    def factory():
        constructions.append(True)
        if len(constructions) == 1:
            raise RuntimeError("private-canary /private/config")
        return dispatcher

    async def scenario():
        async with Client(bootstrap.create_stdio_server(services_factory=factory)) as client:
            failed = await client.call_tool("get_system_status", {})
            assert failed.is_error
            text = failed.content[0].text
            assert "INTERNAL_ERROR" in text
            assert "private" not in text
            succeeded = await client.call_tool("get_system_status", {})
            assert not succeeded.is_error

    asyncio.run(scenario())
    assert constructions == [True, True]
    assert dispatcher.calls == [("get_system_status", "{}")]


def test_default_services_construct_without_settings_config_or_storage(monkeypatch, tmp_path):
    from finance_research_agent.application.config_service import DirectoryConfigurationRepository

    class Calendar:
        def readiness(self):
            return None

    monkeypatch.setattr(DirectoryConfigurationRepository, "load", forbidden)
    root = tmp_path / "missing"
    services = bootstrap.create_default_services(
        data_root=root, settings_factory=forbidden, calendar_factory=Calendar,
    )
    with pytest.raises(LookupError):
        services.dispatch("get_report", '{"run_id":"premarket-2026-09-28-r1"}')
    assert not root.exists()


@pytest.mark.parametrize("operation,arguments", [
    ("get_report", '{"run_id":"premarket-2026-09-28-r1"}'),
    ("get_run_status", '{"run_id":"premarket-2026-09-28-r1"}'),
    ("list_watchlist", "{}"),
])
def test_stored_and_watchlist_calls_do_not_require_calendar_or_credentials(
    monkeypatch, tmp_path, operation, arguments,
):
    copytree(PROJECT / "config/examples", tmp_path / "config")
    services = bootstrap.create_default_services(
        data_root=tmp_path, settings_factory=forbidden, calendar_factory=forbidden,
    )
    if operation == "list_watchlist":
        assert services.dispatch(operation, arguments).version == "1"
    else:
        with pytest.raises(LookupError):
            services.dispatch(operation, arguments)


def test_lazy_calendar_forwards_and_retries_failed_construction():
    attempts = []
    calls = []
    day = date(2026, 9, 28)

    class Calendar:
        def readiness(self):
            calls.append("readiness")
            return "ready"

        def is_trading_day(self, value):
            calls.append(value)
            return True

        def session_open_close(self, value):
            calls.append(value)
            return ("open", "close")

    def factory():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("private")
        return Calendar()

    calendar = bootstrap.LazyMarketCalendar(factory)
    assert attempts == []
    with pytest.raises(RuntimeError):
        calendar.readiness()
    assert calendar.readiness() == "ready"
    assert calendar.is_trading_day(day) is True
    assert calendar.session_open_close(day) == ("open", "close")
    assert attempts == [True, True]
    assert calls == ["readiness", day, day]


@pytest.mark.parametrize("stage", ["server", "transport"])
def test_main_redacts_startup_failures_and_keeps_stdout_empty(monkeypatch, capsys, stage):
    def fail(*args, **kwargs):
        raise RuntimeError("private-canary /private/config")

    target = "create_stdio_server" if stage == "server" else "run_stdio"
    monkeypatch.setattr(bootstrap, target, fail)
    assert bootstrap.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "INTERNAL_ERROR"
    assert "private" not in captured.err


def test_main_runs_stdio_without_composing_services(monkeypatch, capsys):
    servers = []
    monkeypatch.setattr(bootstrap, "run_stdio", servers.append)
    assert bootstrap.main(services_factory=forbidden) == 0
    assert len(servers) == 1
    assert capsys.readouterr() == ("", "")


def test_system_clock_uses_aware_utc():
    before = datetime.now(UTC)
    now = bootstrap.SystemClock().now_utc()
    assert before <= now <= datetime.now(UTC)
    assert now.utcoffset().total_seconds() == 0


def test_lazy_provider_forwards_full_port_without_constructing_early():
    calls = []
    constructions = []

    class Provider:
        def readiness(self):
            calls.append(("readiness",))
            return "readiness-result"

        def fetch_instruments(self, *args, **kwargs):
            calls.append(("instruments", args, kwargs))
            return "instrument-result"

        def fetch_daily_bars(self, *args, **kwargs):
            calls.append(("bars", args, kwargs))
            return "bar-result"

        def fetch_premarket_observations(self, *args, **kwargs):
            calls.append(("prices", args, kwargs))
            return "price-result"

    def factory():
        constructions.append(True)
        return Provider()

    provider = bootstrap.LazyMarketDataProvider(factory)
    assert constructions == []
    symbols = ("AAPL",)
    day = date(2026, 9, 28)
    cutoff = datetime(2026, 9, 28, 12, 45, tzinfo=UTC)
    observer = object()
    identities = {}
    assert provider.readiness() == "readiness-result"
    assert provider.fetch_instruments(symbols, telemetry_observer=observer) == "instrument-result"
    assert provider.fetch_daily_bars(
        symbols, day, day, expected_sessions=(day,), completed_through_session=day,
        evidence_cutoff_at=cutoff, instrument_identities=identities,
        telemetry_observer=observer,
    ) == "bar-result"
    assert provider.fetch_premarket_observations(
        symbols, cutoff, instrument_identities=identities, telemetry_observer=observer,
    ) == "price-result"
    assert constructions == [True]
    assert calls == [
        ("readiness",),
        ("instruments", (symbols,), {"telemetry_observer": observer}),
        ("bars", (symbols, day, day), {
            "expected_sessions": (day,), "completed_through_session": day,
            "evidence_cutoff_at": cutoff, "instrument_identities": identities,
            "telemetry_observer": observer,
        }),
        ("prices", (symbols, cutoff), {
            "instrument_identities": identities, "telemetry_observer": observer,
        }),
    ]


def test_lazy_provider_does_not_cache_failed_factory():
    attempts = []

    def factory():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("private")
        return Dispatcher()

    provider = bootstrap.LazyMarketDataProvider(factory)
    with pytest.raises(RuntimeError):
        provider.readiness()
    assert provider._get() is provider._get()
    assert attempts == [True, True]


def test_run_factory_uses_frozen_policy_and_isolates_exact_deadlines(
    monkeypatch, tmp_path, valid_packet,
):
    copytree(PROJECT / "config/examples", tmp_path / "config")
    repository = DirectoryConfigurationRepository(tmp_path / "config")
    snapshot_a = ConfigService(repository).validate_and_snapshot()
    source_path = tmp_path / "config/source-policy.yaml"
    source_path.write_text(source_path.read_text().replace('version: "1"', 'version: "2"'))
    snapshot_b = ConfigService(repository).validate_and_snapshot()
    now = datetime(2026, 9, 28, 12, 45, tzinfo=UTC)

    class Clock:
        def now_utc(self):
            return now

    loads = []

    def settings():
        loads.append(True)
        return Settings(data_dir=tmp_path, alpaca_api_key="key", alpaca_api_secret="secret")

    services = bootstrap.create_default_services(
        data_root=tmp_path, clock=Clock(), settings_factory=settings,
        calendar_factory=lambda: object(),
    )
    monkeypatch.setattr(DirectoryConfigurationRepository, "load", forbidden)
    run_a = valid_packet.run.model_copy(update={"configuration_snapshot": snapshot_a})
    run_b = valid_packet.run.model_copy(update={"configuration_snapshot": snapshot_b})
    cap_a, cap_b = now + timedelta(seconds=7), now + timedelta(seconds=19)
    provider_a = services._market_data_for_run(run_a, cap_a)
    provider_b = services._market_data_for_run(run_b, cap_b)
    assert isinstance(provider_a, AlpacaMarketDataProvider)
    assert isinstance(provider_b, AlpacaMarketDataProvider)
    assert provider_a is not provider_b
    assert provider_a._identity_cache is not provider_b._identity_cache
    client_a, client_b = provider_a._http_client, provider_b._http_client
    assert client_a is not client_b
    assert client_a._policy.version == "1"
    assert client_b._policy.version == "2"
    assert client_a._run_deadline is cap_a
    assert client_b._run_deadline is cap_b
    assert client_a._clock() == provider_a._clock() == now
    assert loads == [True]


def test_default_http_factory_injects_real_sleep(monkeypatch, tmp_path):
    policy = DirectoryConfigurationRepository(PROJECT / "config/examples").load().source
    sleeps = []
    monkeypatch.setattr(bootstrap, "sleep", sleeps.append)
    client = bootstrap.create_http_client(policy, None, bootstrap.SystemClock())
    client._sleeper(Decimal("0.125"))
    assert sleeps == [0.125]


def test_expired_run_client_construction_uses_application_timeout_boundary(tmp_path, valid_packet):
    snapshot = ConfigService(
        DirectoryConfigurationRepository(PROJECT / "config/examples")
    ).validate_and_snapshot()
    clock = bootstrap.SystemClock()
    services = bootstrap.create_default_services(
        data_root=tmp_path, clock=clock,
        settings_factory=lambda: Settings(data_dir=tmp_path), calendar_factory=forbidden,
    )
    run = valid_packet.run.model_copy(update={"configuration_snapshot": snapshot})
    with pytest.raises(TimeoutError) as caught:
        services._market_data_for_run(run, clock.now_utc() - timedelta(seconds=1))
    assert caught.value.__suppress_context__ is True
