"""In-process protocol tests for the exact Product A stdio MCP boundary."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp import Client
from pydantic import TypeAdapter

from finance_research_agent.adapters.mcp_stdio import create_mcp_server, run_stdio
from finance_research_agent.application.operations import (
    OPERATION_CONTRACTS,
    OPERATION_NAMES,
    ErrorCode,
)
from finance_research_agent.domain.validation import ResearchBriefDraft


class DispatchSpy:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.result = result
        self.error = error

    def dispatch(self, operation: str, arguments_json: str) -> object:
        self.calls.append((operation, arguments_json))
        if self.error is not None:
            raise self.error
        return self.result


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _valid_arguments(valid_brief_draft: ResearchBriefDraft) -> dict[str, dict[str, object]]:
    draft = valid_brief_draft.model_dump(mode="json")
    return {
        "get_system_status": {},
        "validate_configuration": {},
        "prepare_premarket_run": {},
        "get_run_status": {"run_id": "premarket-2026-09-29-r1"},
        "get_report": {"run_id": "premarket-2026-09-29-r1"},
        "validate_and_publish_brief": {
            "draft": draft
        },
        "publish_reduced_report": {
            "run_id": "premarket-2026-09-29-r1",
            "reason": "SYNTHESIS_UNAVAILABLE",
        },
        "list_watchlist": {},
        "upsert_watchlist_item": {
            "expected_version": "1",
            "item": {
                "symbol": "AAPL",
                "role": "CORE_MONITOR",
                "research_rationale": "Coverage priority",
            },
        },
        "remove_watchlist_item": {"expected_version": "1", "symbol": "AAPL"},
        "record_run_feedback": {
            "run_id": "premarket-2026-09-29-r1",
            "clarity_score": 4,
            "evidence_score": 4,
            "usefulness_score": 4,
        },
    }


def test_tools_list_matches_exact_operation_names_and_typed_schemas() -> None:
    async def scenario() -> None:
        server = create_mcp_server(DispatchSpy())
        async with Client(server) as client:
            listed = await client.list_tools()

        assert tuple(tool.name for tool in listed.tools) == OPERATION_NAMES
        for tool in listed.tools:
            contract = OPERATION_CONTRACTS[tool.name]
            assert tool.input_schema == contract.request_model.model_json_schema()
            assert tool.output_schema == TypeAdapter(contract.result_model).json_schema()

    _run(scenario())


def test_initialize_advertises_only_the_tools_capability() -> None:
    capabilities = create_mcp_server(DispatchSpy()).get_capabilities()

    assert capabilities.tools is not None
    assert capabilities.resources is None
    assert capabilities.prompts is None


def test_each_registered_tool_dispatches_only_to_its_matching_application_operation(
    valid_brief_draft: ResearchBriefDraft,
) -> None:
    arguments_by_operation = _valid_arguments(valid_brief_draft)

    async def scenario() -> None:
        spy = DispatchSpy(error=LookupError("not found"))
        server = create_mcp_server(spy)
        async with Client(server) as client:
            for name in OPERATION_NAMES:
                response = await client.call_tool(name, arguments_by_operation[name])
                assert response.is_error is True

        assert tuple(name for name, _ in spy.calls) == OPERATION_NAMES
        for name, serialized in spy.calls:
            assert json.loads(serialized) == arguments_by_operation[name]

    _run(scenario())


def test_successful_response_is_typed_and_matches_the_declared_output_schema() -> None:
    async def scenario() -> None:
        result_model = OPERATION_CONTRACTS["get_system_status"].result_model
        spy = DispatchSpy(
            result=result_model.model_validate_json(
                '{"configuration_ready":true,"market_data_ready":true,'
                '"market_calendar_ready":true,"current_market_date":"2026-09-29",'
                '"diagnostics":[]}'
            )
        )
        server = create_mcp_server(spy)
        async with Client(server) as client:
            result = await client.call_tool("get_system_status", {})

        assert spy.calls == [("get_system_status", "{}")]
        assert result.is_error is False
        assert result.structured_content == {
            "configuration_ready": True,
            "market_data_ready": True,
            "market_calendar_ready": True,
            "current_market_date": "2026-09-29",
            "diagnostics": [],
            "schema_version": "0.1",
        }

    _run(scenario())


def test_missing_arguments_are_normalized_to_an_empty_object() -> None:
    async def scenario() -> None:
        result_model = OPERATION_CONTRACTS["list_watchlist"].result_model
        spy = DispatchSpy(result=result_model(version="1", items=()))
        server = create_mcp_server(spy)
        async with Client(server) as client:
            result = await client.call_tool("list_watchlist")

        assert result.is_error is False
        assert spy.calls == [("list_watchlist", "{}")]

    _run(scenario())


def test_unknown_and_malformed_operation_arguments_do_not_reach_application_services() -> None:
    async def scenario() -> None:
        spy = DispatchSpy()
        server = create_mcp_server(spy)
        async with Client(server) as client:
            unknown = await client.call_tool("run_anything", {})
            try:
                malformed = await client.call_tool(
                    "get_run_status",
                    {"run_id": "premarket-2026-09-29-r1", "path": "/tmp/private"},
                )
            except Exception:
                malformed = None

        assert unknown.is_error is True
        assert malformed is None or malformed.is_error is True
        assert spy.calls == []

    _run(scenario())


def test_oversized_arguments_are_rejected_before_application_dispatch() -> None:
    async def scenario() -> None:
        spy = DispatchSpy()
        server = create_mcp_server(spy)
        async with Client(server) as client:
            result = await client.call_tool("get_report", {"run_id": "x" * 1_000_001})

        assert result.is_error is True
        assert ErrorCode.INVALID_REQUEST.value in result.content[0].text
        assert spy.calls == []

    _run(scenario())


def test_internal_failure_is_redacted_to_a_closed_error_code() -> None:
    async def scenario() -> None:
        spy = DispatchSpy(
            error=RuntimeError("token=super-secret /private/user/config.json")
        )
        server = create_mcp_server(spy)
        async with Client(server) as client:
            result = await client.call_tool("get_system_status", {})

        assert result.is_error is True
        serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)
        assert "super-secret" not in serialized
        assert "/private/user/config.json" not in serialized
        assert ErrorCode.INTERNAL_ERROR.value in serialized
        assert tuple(name for name, _ in spy.calls) == ("get_system_status",)

    _run(scenario())


def test_known_lookup_failure_uses_the_closed_public_error_without_echoing_details() -> None:
    async def scenario() -> None:
        spy = DispatchSpy(error=LookupError("private run path /Users/ben/private"))
        server = create_mcp_server(spy)
        async with Client(server) as client:
            result = await client.call_tool(
                "get_run_status", {"run_id": "premarket-2026-09-29-r1"}
            )

        assert result.is_error is True
        text = result.content[0].text
        assert ErrorCode.INVALID_RUN_STATE.value in text
        assert "/Users/ben/private" not in text

    _run(scenario())


def test_application_value_error_is_not_misreported_as_invalid_transport_input() -> None:
    async def scenario() -> None:
        spy = DispatchSpy(error=ValueError("internal invariant at /private/path"))
        server = create_mcp_server(spy)
        async with Client(server) as client:
            result = await client.call_tool("get_system_status", {})

        assert result.is_error is True
        text = result.content[0].text
        assert ErrorCode.INTERNAL_ERROR.value in text
        assert ErrorCode.INVALID_REQUEST.value not in text
        assert "/private/path" not in text

    _run(scenario())


def test_stdio_startup_never_requests_a_network_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, object, object]] = []

    class Server:
        def create_initialization_options(self) -> object:
            return "init-options"

        async def run(self, read_stream: object, write_stream: object, options: object) -> None:
            calls.append((read_stream, write_stream, options))

    class StdioContext:
        async def __aenter__(self) -> tuple[str, str]:
            return "stdio-read", "stdio-write"

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        "finance_research_agent.adapters.mcp_stdio.stdio_server",
        lambda: StdioContext(),
    )

    def run_anyio(awaitable: Any, **kwargs: object) -> Any:
        assert kwargs == {"backend": "asyncio"}
        return _run(awaitable())

    monkeypatch.setattr("finance_research_agent.adapters.mcp_stdio.anyio.run", run_anyio)

    run_stdio(Server())

    assert calls == [("stdio-read", "stdio-write", "init-options")]
