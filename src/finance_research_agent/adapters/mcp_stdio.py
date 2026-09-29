"""Stdio-only MCP adapter for the typed Product A operation boundary."""

from __future__ import annotations

import json
from typing import Any, Protocol

import anyio
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from pydantic import TypeAdapter

from finance_research_agent import __version__
from finance_research_agent.application.operations import (
    OPERATION_CONTRACTS,
    OPERATION_NAMES,
    OperationError,
    validate_operation_request,
)
from finance_research_agent.domain.errors import ErrorCode

_SERVER_NAME = "finance-research-agent"


class _ApplicationServiceDispatcher(Protocol):
    def dispatch(self, operation: str, arguments_json: str) -> object: ...


def _tools() -> list[Tool]:
    return [
        Tool(
            name=name,
            description=f"Run the typed Product A {name.replace('_', ' ')} operation.",
            input_schema=OPERATION_CONTRACTS[name].request_model.model_json_schema(),
            output_schema=TypeAdapter(
                OPERATION_CONTRACTS[name].result_model
            ).json_schema(),
        )
        for name in OPERATION_NAMES
    ]


def _error_result(code: ErrorCode) -> CallToolResult:
    payload = OperationError(code=code).model_dump(mode="json")
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, sort_keys=True))],
        is_error=True,
    )


def create_mcp_server(application_services: _ApplicationServiceDispatcher) -> Server[Any]:
    """Create an MCP server exposing only the fixed Product A operation set."""
    tools = _tools()
    tools_by_name = {tool.name: tool for tool in tools}

    async def list_tools(
        context: ServerRequestContext[Any],
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        del context, params
        return ListToolsResult(tools=tools)

    async def call_tool(
        context: ServerRequestContext[Any], params: CallToolRequestParams
    ) -> CallToolResult:
        del context
        name = params.name
        if name not in tools_by_name or name not in OPERATION_CONTRACTS:
            return _error_result(ErrorCode.INVALID_REQUEST)

        arguments = params.arguments
        if arguments is None:
            arguments = {}

        try:
            arguments_json = json.dumps(
                dict(arguments), separators=(",", ":"), sort_keys=True, ensure_ascii=False
            )
            validate_operation_request(name, arguments_json)
        except Exception:
            return _error_result(ErrorCode.INVALID_REQUEST)

        try:
            result = application_services.dispatch(name, arguments_json)
            result_model = OPERATION_CONTRACTS[name].result_model
            result_adapter = TypeAdapter(result_model)
            validated_result = result_adapter.validate_python(result, strict=True)
            result_data = result_adapter.dump_python(validated_result, mode="json")
        except LookupError:
            return _error_result(ErrorCode.INVALID_RUN_STATE)
        except Exception:
            return _error_result(ErrorCode.INTERNAL_ERROR)

        result_text = json.dumps(result_data, sort_keys=True, ensure_ascii=False)
        return CallToolResult(
            content=[TextContent(type="text", text=result_text)],
            structured_content=result_data,
        )

    return Server(
        _SERVER_NAME,
        version=__version__,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def run_stdio(server: Server[Any]) -> None:
    """Run the server using MCP's stdio transport without opening a listener."""

    async def serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    anyio.run(serve, backend="asyncio")


__all__ = ["create_mcp_server", "run_stdio"]
