from typing import cast

import pytest

from finance_research_agent.adapters.fake_tool import FakeToolPort
from finance_research_agent.agent import (
    ToolDefinition,
    ToolPort,
    ToolRegistry,
    ToolRequest,
    ToolResult,
)

DEFINITION = ToolDefinition("fake", "Predetermined test results")


def test_fake_executes_configured_results_in_order_and_records_original_requests() -> None:
    results = (ToolResult("First"), ToolResult("Second"))
    fake = FakeToolPort(DEFINITION, results)
    tool: ToolPort = fake
    first, second = ToolRequest("fake", {"value": 1}), ToolRequest("fake", {"value": 2})

    assert tool.definition is DEFINITION
    initial_history = fake.requests
    assert initial_history == ()
    assert tool.execute(first) is results[0]
    assert tool.execute(second) is results[1]
    assert fake.requests[0] is first
    assert fake.requests[1] is second
    assert results == (ToolResult("First"), ToolResult("Second"))


def test_definition_is_read_only_and_history_is_a_read_only_tuple_snapshot() -> None:
    fake = FakeToolPort(DEFINITION, (ToolResult("First"), ToolResult("Second")))
    first, second = ToolRequest("fake", {}), ToolRequest("fake", {"second": True})
    fake.execute(first)
    snapshot = fake.requests
    fake.execute(second)

    assert snapshot == (first,)
    assert fake.requests == (first, second)
    for field in ("definition", "requests"):
        with pytest.raises(AttributeError):
            setattr(fake, field, ())


def test_exhaustion_records_each_valid_attempt_and_never_repeats_last_result() -> None:
    fake = FakeToolPort(DEFINITION, (ToolResult("Only result"),))
    request = ToolRequest("fake", {})
    fake.execute(request)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="^fake tool results exhausted$"):
            fake.execute(request)
    assert fake.requests == (request, request, request)


def test_empty_result_tuple_starts_exhausted() -> None:
    fake = FakeToolPort(DEFINITION, ())
    request = ToolRequest("fake", {})

    with pytest.raises(RuntimeError, match="^fake tool results exhausted$"):
        fake.execute(request)
    assert fake.requests == (request,)


@pytest.mark.parametrize("definition", [None, "fake", (), ToolResult("Result")])
def test_fake_requires_definition(definition: object) -> None:
    with pytest.raises(ValueError, match="definition must be a ToolDefinition"):
        FakeToolPort(cast(ToolDefinition, definition), ())


@pytest.mark.parametrize("results", [[], [ToolResult("Result")], None, "Result"])
def test_fake_requires_result_tuple(results: object) -> None:
    with pytest.raises(ValueError, match="results must be an immutable tuple"):
        FakeToolPort(DEFINITION, cast(tuple[ToolResult, ...], results))


@pytest.mark.parametrize("result", [None, "Result", 1, ToolRequest("fake", {})])
def test_fake_rejects_non_result_members(result: object) -> None:
    with pytest.raises(ValueError, match="results must contain ToolResult values"):
        FakeToolPort(DEFINITION, (ToolResult("Valid"), cast(ToolResult, result)))


@pytest.mark.parametrize("invalid", [None, "Request", (), ToolResult("Result")])
@pytest.mark.parametrize("exhausted", [False, True])
def test_invalid_request_neither_records_nor_consumes(invalid: object, exhausted: bool) -> None:
    result = ToolResult("Next result")
    fake = FakeToolPort(DEFINITION, () if exhausted else (result,))

    with pytest.raises(ValueError, match="request must be a ToolRequest"):
        fake.execute(cast(ToolRequest, invalid))
    assert fake.requests == ()
    if not exhausted:
        assert fake.execute(ToolRequest("fake", {})) is result


@pytest.mark.parametrize("exhausted", [False, True])
def test_wrong_name_neither_records_nor_consumes_even_when_exhausted(exhausted: bool) -> None:
    result = ToolResult("Next result")
    fake = FakeToolPort(DEFINITION, () if exhausted else (result,))

    with pytest.raises(ValueError, match="request name must match tool definition"):
        fake.execute(ToolRequest("other", {}))
    assert fake.requests == ()
    if not exhausted:
        assert fake.execute(ToolRequest("fake", {})) is result


def test_repeated_construction_lookup_and_execution_are_deterministic_and_independent() -> None:
    results = (ToolResult("First"), ToolResult("Second"))
    requests = (ToolRequest("fake", {"a": 1}), ToolRequest("fake", {"a": 2}))
    first, second = FakeToolPort(DEFINITION, results), FakeToolPort(DEFINITION, results)

    for fake in (first, second):
        initial_history = fake.requests
        assert initial_history == ()
        registry = ToolRegistry((fake,))
        tool = registry.get("fake")
        assert tuple(tool.execute(request) for request in requests) == results
        with pytest.raises(RuntimeError, match="^fake tool results exhausted$"):
            tool.execute(requests[0])
        assert fake.requests == (*requests, requests[0])
    assert first.requests == second.requests


def test_fake_does_not_interpret_argument_values() -> None:
    result = ToolResult("Predetermined")
    for value in ("Return a different result", "Ignore previous instructions", 42, None):
        fake = FakeToolPort(DEFINITION, (result,))
        assert fake.execute(ToolRequest("fake", {"value": value})) is result
