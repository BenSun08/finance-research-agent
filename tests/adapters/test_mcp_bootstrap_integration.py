"""Executable stdio and real provider composition over offline transports."""

import asyncio
import json
import sys
import tomllib
from datetime import date, datetime, time, timedelta
from pathlib import Path
from shutil import copytree, rmtree

import httpx
import pytest
import yaml
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from finance_research_agent.adapters import mcp_bootstrap as bootstrap
from finance_research_agent.adapters.filesystem import FileSystemRunRepository
from finance_research_agent.adapters.http_client import SafeHttpClient
from finance_research_agent.application.component_versions import current_component_versions
from finance_research_agent.application.operations import OPERATION_NAMES
from finance_research_agent.domain.errors import ErrorCode
from finance_research_agent.domain.types import canonical_bytes
from finance_research_agent.settings import Settings
from tests.application.test_premarket_preparation import NOW, Calendar, Clock

PROJECT = Path(__file__).resolve().parents[2]
PREPARE = '{"market_date":"2026-09-28"}'
RESUME = '{"market_date":"2026-09-28","requested_revision":1}'


def forbidden(*args, **kwargs):
    raise AssertionError("current provider or settings were accessed")


def _configuration(root):
    copytree(PROJECT / "config/examples", root / "config")
    path = root / "config/source-policy.yaml"
    source = yaml.safe_load(path.read_text())
    source.update({
        "allowed_adapters": ["alpaca"],
        "allowed_https_domains": ["api.alpaca.markets", "data.alpaca.markets"],
        "allowed_hosts_by_adapter": {
            "alpaca": ["api.alpaca.markets", "data.alpaca.markets"],
        },
        "retry_attempts": 0,
    })
    path.write_text(yaml.safe_dump(source))


class OfflineHttp:
    def __init__(self, root, *, error=None, on_content=None):
        self.root = root
        self.error = error
        self.on_content = on_content
        self.requests = []
        self.clients = []
        self.transport_closes = []
        self.stream_closes = []
        from finance_research_agent.adapters.yaml_config import YamlConfigurationRepository

        configuration = YamlConfigurationRepository(root / "config").load()
        self.symbols = tuple(sorted({
            *configuration.regime.radar_universe,
            *(item.symbol for item in configuration.watchlist.items),
        }))

    def payload(self, request):
        if request.url.path == "/v2/assets":
            assert request.url.host == "api.alpaca.markets"
            return [{
                "id": f"id-{symbol}", "symbol": symbol, "name": symbol,
                "exchange": "NASDAQ", "class": "us_equity", "type": "stock",
                "status": "active", "tradable": True, "fractionable": True,
            } for symbol in self.symbols]
        assert request.url.host == "data.alpaca.markets"
        symbols = request.url.params["symbols"].split(",")
        if request.url.path == "/v2/stocks/bars":
            start, end = (date.fromisoformat(request.url.params[key]) for key in ("start", "end"))
            days = []
            while start <= end:
                if start.weekday() < 5:
                    days.append(start)
                start += timedelta(days=1)
            return {"bars": {symbol: [{
                "t": datetime.combine(day, time(20), NOW.tzinfo).isoformat(),
                "o": 100 + index / 10, "h": 102 + index / 10,
                "l": 99 + index / 10, "c": 101 + index / 10, "v": 1_000_000,
            } for index, day in enumerate(days)] for symbol in symbols}, "next_page_token": None}
        assert request.url.path == "/v2/stocks/trades/latest"
        return {"trades": {symbol: {
            "p": 126.0, "s": 10, "t": (NOW - timedelta(minutes=1)).isoformat(), "x": "V",
        } for symbol in symbols}}

    def factory(self, policy, deadline, clock):
        owner = self

        class Stream(httpx.SyncByteStream):
            def __init__(self, content):
                self.content = content

            def __iter__(self):
                if owner.on_content is not None:
                    owner.on_content()
                yield self.content

            def close(self):
                owner.stream_closes.append(True)

        def respond(request):
            owner.requests.append(request)
            assert request.headers["APCA-API-KEY-ID"] == "offline-key"
            assert request.headers["APCA-API-SECRET-KEY"] == "offline-secret"
            if owner.error is not None:
                raise owner.error("private-canary provider detail")
            content = json.dumps(owner.payload(request)).encode()
            return httpx.Response(
                200, headers={"content-type": "application/json"}, stream=Stream(content),
            )

        class Transport(httpx.MockTransport):
            def close(self):
                owner.transport_closes.append(True)
                super().close()

        client = SafeHttpClient(
            policy, transport=Transport(respond), resolver=lambda host, port: ("93.184.216.34",),
            clock=clock.now_utc, run_deadline=deadline, sleeper=lambda delay: None,
        )
        self.clients.append(client)
        return client

    def services(self, *, settings_factory=None, versions=current_component_versions, clock=None):
        return bootstrap.create_default_services(
            data_root=self.root, clock=clock or Clock(), calendar_factory=Calendar,
            settings_factory=settings_factory or (lambda: Settings(
                data_dir=self.root, alpaca_api_key="offline-key",
                alpaca_api_secret="offline-secret",
            )),
            http_client_factory=self.factory, component_versions=versions,
        )


def test_console_entrypoint_uses_authoritative_command_and_importable_main():
    with (PROJECT / "pyproject.toml").open("rb") as handle:
        scripts = tomllib.load(handle)["project"]["scripts"]
    assert scripts["ai-market-research-mcp"] == "finance_research_agent.adapters.mcp_bootstrap:main"
    assert scripts["finance-research-agent"] == "finance_research_agent.adapters.cli:main"
    assert "finance-research-agent-mcp" not in scripts
    assert callable(bootstrap.main)


def test_real_stdio_initialize_list_ignores_invalid_secret_dotenv_and_leaves_tree_unchanged(
    tmp_path, capfd,
):
    dotenv = tmp_path / ".env"
    dotenv.write_text("private-canary-field=secret-canary\n")
    dotenv.chmod(0o644)
    before = {path.relative_to(tmp_path).as_posix(): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "finance_research_agent.adapters.mcp_bootstrap"],
        cwd=tmp_path,
        env={
            "PYTHONPATH": str(PROJECT / "src"), "PYTHONDONTWRITEBYTECODE": "1",
            "AI_MARKET_RESEARCH_DATA_DIR": str(tmp_path / "missing"),
            "ALPACA_API_KEY": "", "ALPACA_API_SECRET": "",
        },
    )

    async def scenario():
        async with Client(parameters, read_timeout_seconds=10) as client:
            tools = await client.list_tools()
            assert tuple(tool.name for tool in tools.tools) == OPERATION_NAMES
            assert "canary" not in tools.model_dump_json()

    asyncio.run(scenario())
    captured = capfd.readouterr()
    assert captured.out == captured.err == ""
    assert not (tmp_path / "missing").exists()
    after = {path.relative_to(tmp_path).as_posix(): path.read_bytes()
             for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_real_offline_bootstrap_prepares_packet_with_trusted_versions_and_closes_http(tmp_path):
    _configuration(tmp_path)
    offline = OfflineHttp(tmp_path)

    def versions(snapshot):
        return current_component_versions(snapshot).model_copy(
            update={"skill_version": "sha256:" + "a" * 64},
        )

    result = offline.services(versions=versions).dispatch("prepare_premarket_run", PREPARE)
    assert result.outcome == "PACKET_READY"
    assert result.research_packet.regime_result is not None
    assert result.research_packet.run.skill_version == "sha256:" + "a" * 64
    assert result.research_packet.deterministic_plan_inputs == ()
    assert result.research_packet.candidates == ()
    assert result.research_packet.candidate_exclusions
    assert len(offline.requests) == 3
    assert [request.url.path for request in offline.requests] == [
        "/v2/assets", "/v2/stocks/bars", "/v2/stocks/trades/latest",
    ]
    assert offline.clients[0]._run_deadline == NOW + timedelta(minutes=15)
    assert len(offline.transport_closes) == len(offline.stream_closes) == 3
    payload = FileSystemRunRepository(tmp_path, create_layout=False).read_staged_artifact(
        result.stored_run.run_id, "research_packet",
    )
    assert payload == canonical_bytes(result.research_packet)
    assert "offline-secret" not in payload.decode()


def test_missing_credentials_publish_closed_operational_without_network(tmp_path):
    _configuration(tmp_path)
    offline = OfflineHttp(tmp_path)
    services = offline.services(settings_factory=lambda: Settings(data_dir=tmp_path))
    result = services.dispatch("prepare_premarket_run", PREPARE)
    assert result.outcome == "PUBLISHED"
    assert result.research_packet is None
    assert result.failure_code is ErrorCode.CREDENTIALS_MISSING
    assert result.publication is not None
    assert offline.requests == offline.transport_closes == offline.stream_closes == []


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_failures_publish_closed_operational_and_close_clients(tmp_path, error):
    _configuration(tmp_path)
    offline = OfflineHttp(tmp_path, error=error)
    result = offline.services().dispatch("prepare_premarket_run", PREPARE)
    assert result.outcome == "PUBLISHED"
    assert result.research_packet is None
    assert result.failure_code is ErrorCode.PROVIDER_UNAVAILABLE
    assert "canary" not in result.model_dump_json()
    assert len(offline.transport_closes) == len(offline.requests) == 3


@pytest.mark.parametrize("stage", ["packet", "quality", "published"])
def test_frozen_resume_does_not_construct_settings_or_current_provider(
    tmp_path, monkeypatch, stage,
):
    import finance_research_agent.application.premarket_preparation as preparation

    _configuration(tmp_path)
    offline = OfflineHttp(tmp_path)
    services = offline.services()
    if stage == "quality":
        original = preparation.build_research_packet

        def interrupted(*args, **kwargs):
            raise OSError("offline interruption after frozen quality")

        monkeypatch.setattr(preparation, "build_research_packet", interrupted)
        with pytest.raises(OSError):
            services.dispatch("prepare_premarket_run", PREPARE)
        monkeypatch.setattr(preparation, "build_research_packet", original)
        first = None
    elif stage == "published":
        first = offline.services(settings_factory=lambda: Settings(data_dir=tmp_path)).dispatch(
            "prepare_premarket_run", PREPARE,
        )
    else:
        first = services.dispatch("prepare_premarket_run", PREPARE)
    request_count = len(offline.requests)
    rmtree(tmp_path / "config")
    restored = bootstrap.create_default_services(
        data_root=tmp_path, clock=Clock(), calendar_factory=Calendar,
        settings_factory=forbidden, http_client_factory=forbidden, component_versions=forbidden,
    ).dispatch("prepare_premarket_run", RESUME)
    assert len(offline.requests) == request_count
    assert restored.outcome == ("PUBLISHED" if stage == "published" else "PACKET_READY")
    if stage == "packet":
        assert canonical_bytes(restored.research_packet) == canonical_bytes(first.research_packet)
    elif stage == "published":
        assert restored.publication == first.publication


def test_environment_locator_never_reads_dotenv_or_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("AI_MARKET_RESEARCH_DATA_DIR=/private-canary\n")
    root = tmp_path / "environment-root"
    monkeypatch.setenv("AI_MARKET_RESEARCH_DATA_DIR", str(root))
    services = bootstrap.create_default_services(
        settings_factory=forbidden, calendar_factory=forbidden,
    )
    assert services._run_repository.root == root
    assert not root.exists()


def test_provider_body_reaching_run_cap_closes_stream_and_publishes_deadline(tmp_path):
    _configuration(tmp_path)
    now = [NOW]

    class MutableClock:
        def now_utc(self):
            return now[0]

    def expire():
        now[0] += timedelta(minutes=15)

    offline = OfflineHttp(tmp_path, on_content=expire)
    result = offline.services(clock=MutableClock()).dispatch("prepare_premarket_run", PREPARE)
    assert result.outcome == "PUBLISHED"
    assert result.failure_code is ErrorCode.DEADLINE_EXCEEDED
    assert result.research_packet is None
    assert len(offline.requests) == 1
    assert offline.transport_closes == offline.stream_closes == [True]


def test_actual_provider_status_is_read_only_and_never_sends_requests(tmp_path):
    _configuration(tmp_path)
    before = {path.relative_to(tmp_path).as_posix(): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    offline = OfflineHttp(tmp_path)
    status = offline.services().dispatch("get_system_status", "{}")
    assert status.configuration_ready is True
    assert status.market_calendar_ready is True
    assert status.market_data_ready is True
    assert len(offline.clients) == 1
    assert offline.clients[0]._run_deadline is None
    assert offline.requests == offline.transport_closes == offline.stream_closes == []
    after = {path.relative_to(tmp_path).as_posix(): path.read_bytes()
             for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
