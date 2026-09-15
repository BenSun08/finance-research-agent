from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from finance_research_agent.adapters.fake_model import FakeModelPort
from finance_research_agent.adapters.fake_tool import FakeToolPort
from finance_research_agent.agent import (
    AgentRunResult,
    AgentRuntime,
    FinalAnswer,
    ModelMessage,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ToolArgumentValue,
    ToolCall,
    ToolDefinition,
    ToolObservation,
    ToolRegistry,
    ToolRequest,
    ToolResult,
)


def _request() -> ModelRequest:
    return ModelRequest((ModelMessage("user", "  Research\nRésumé 市场\t "),))


def _tool(name: str, *contents: str) -> FakeToolPort:
    return FakeToolPort(
        ToolDefinition(name, "Predetermined test capability"),
        tuple(ToolResult(content) for content in contents),
    )


@pytest.mark.parametrize("with_tool", [False, True])
def test_immediate_answer_stops_after_one_model_call_without_executing_tools(
    with_tool: bool,
) -> None:
    answer = "  done\nRésumé 市场\t "
    model = FakeModelPort(
        (
            ModelResponse(FinalAnswer(answer)),
            ModelResponse(ToolCall("unused", {})),
        )
    )
    tool = _tool("unused")
    request = _request()

    result = AgentRuntime(model, ToolRegistry((tool,) if with_tool else ())).run(
        request, max_steps=3
    )

    assert result == AgentRunResult(answer, 1)
    assert result.final_answer is answer
    assert model.requests == (request,)
    assert model.requests[0] is request
    assert tool.requests == ()


def test_one_tool_call_translates_execution_intent_and_appends_paired_observation() -> None:
    call = ToolCall("market_regime", {})
    model = FakeModelPort((ModelResponse(call), ModelResponse(FinalAnswer("Risk-off"))))
    tool = _tool("market_regime", "risk_off")
    request = _request()

    result = AgentRuntime(model, ToolRegistry((tool,))).run(request, max_steps=2)

    assert result == AgentRunResult("Risk-off", 2)
    assert tool.requests == (ToolRequest("market_regime", {}),)
    assert not isinstance(tool.requests[0], ToolCall)
    assert len(model.requests) == 2
    observation = model.requests[1].messages[-1]
    assert isinstance(observation, ToolObservation)
    assert observation.call is call
    assert observation.result == ToolResult("risk_off")
    assert model.requests[1] == ModelRequest((*request.messages, observation))


def test_multiple_calls_preserve_execution_order_arguments_results_and_original_history() -> None:
    arguments: dict[str, ToolArgumentValue] = {
        "z": "  Preserve\n市场 ",
        "a": 3,
        "f": -0.0,
        "flag": True,
        "missing": None,
    }
    calls = (ToolCall("second", arguments), ToolCall("first", {"label": "B"}))
    arguments["z"] = "Changed after call construction"
    results = (ToolResult("  A\n "), ToolResult(" B\t "))
    second = FakeToolPort(ToolDefinition("second", "A"), (results[0],))
    first = FakeToolPort(ToolDefinition("first", "B"), (results[1],))
    model = FakeModelPort(
        (
            ModelResponse(calls[0]),
            ModelResponse(calls[1]),
            ModelResponse(FinalAnswer("done")),
        )
    )
    message = ModelMessage("user", "  Unchanged\n ")
    prior = ToolObservation(ToolCall("historical_unregistered", {}), ToolResult("Prior"))
    messages = (ModelMessage("assistant", "Earlier"), message, prior, message)
    request = ModelRequest(messages)

    result = AgentRuntime(model, ToolRegistry((first, second))).run(request, max_steps=3)

    assert result == AgentRunResult("done", 3)
    observations = tuple(ToolObservation(call, value) for call, value in zip(calls, results))
    assert model.requests == (
        request,
        ModelRequest((*messages, observations[0])),
        ModelRequest((*messages, *observations)),
    )
    assert second.requests == (ToolRequest(calls[0].name, calls[0].arguments),)
    assert first.requests == (ToolRequest(calls[1].name, calls[1].arguments),)
    actual_arguments = second.requests[0].arguments
    assert tuple(actual_arguments) == tuple(calls[0].arguments)
    assert actual_arguments["z"] == "  Preserve\n市场 "
    for key, value in calls[0].arguments.items():
        assert type(actual_arguments[key]) is type(value)
    with pytest.raises(TypeError):
        cast(MutableMapping[str, ToolArgumentValue], actual_arguments)["z"] = "No"
    assert request.messages is messages
    for recorded in model.requests:
        assert all(actual is original for actual, original in zip(recorded.messages, messages))
    for actual, expected in zip(model.requests[-1].messages[len(messages) :], observations):
        assert isinstance(actual, ToolObservation)
        assert actual.call is expected.call
        assert actual.result is expected.result


@pytest.mark.parametrize("max_steps", [1, 3])
def test_unknown_tool_propagates_key_error_and_never_executes_unrelated_tool(
    max_steps: int,
) -> None:
    model = FakeModelPort(
        (
            ModelResponse(ToolCall("missing", {})),
            ModelResponse(FinalAnswer("Unreachable")),
        )
    )
    tool = _tool("registered", "Unused")
    request = _request()

    with pytest.raises(KeyError, match="unknown tool 'missing'"):
        AgentRuntime(model, ToolRegistry((tool,))).run(request, max_steps=max_steps)

    assert model.requests == (request,)
    assert tool.requests == ()


@pytest.mark.parametrize("max_steps", [1, 3])
def test_fake_tool_failure_propagates_without_an_observation_or_next_model_call(
    max_steps: int,
) -> None:
    model = FakeModelPort(
        (
            ModelResponse(ToolCall("empty", {})),
            ModelResponse(FinalAnswer("Unreachable")),
        )
    )
    tool = _tool("empty")
    request = _request()

    with pytest.raises(RuntimeError, match="^fake tool results exhausted$"):
        AgentRuntime(model, ToolRegistry((tool,))).run(request, max_steps=max_steps)

    assert model.requests == (request,)
    assert tool.requests == (ToolRequest("empty", {}),)
    assert request == _request()


def test_fake_model_failure_after_tool_execution_propagates_without_retry() -> None:
    call = ToolCall("test", {})
    model = FakeModelPort((ModelResponse(call),))
    tool = _tool("test", "Observed")
    request = _request()

    with pytest.raises(RuntimeError, match="^fake model responses exhausted$"):
        AgentRuntime(model, ToolRegistry((tool,))).run(request, max_steps=3)

    assert len(model.requests) == 2
    assert model.requests[1].messages == (
        *request.messages,
        ToolObservation(call, ToolResult("Observed")),
    )
    assert tool.requests == (ToolRequest("test", {}),)


class _FailingModel:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        raise self.failure


class _FailingTool:
    definition = ToolDefinition("failing", "Raises a supplied exception")

    def __init__(self, failure: Exception) -> None:
        self.failure = failure
        self.requests: list[ToolRequest] = []

    def execute(self, request: ToolRequest) -> ToolResult:
        self.requests.append(request)
        raise self.failure


def test_model_exception_object_is_preserved_and_no_tool_is_executed() -> None:
    failure = ValueError("Model failed")
    model = _FailingModel(failure)
    tool = _tool("unused")
    request = _request()

    with pytest.raises(ValueError) as caught:
        AgentRuntime(model, ToolRegistry((tool,))).run(request, max_steps=3)

    assert caught.value is failure
    assert model.requests == [request]
    assert tool.requests == ()


def test_tool_exception_object_is_preserved_and_no_observation_is_sent() -> None:
    failure = ValueError("Tool failed")
    tool = _FailingTool(failure)
    model = FakeModelPort((ModelResponse(ToolCall("failing", {})),))
    request = _request()

    with pytest.raises(ValueError) as caught:
        AgentRuntime(model, ToolRegistry((tool,))).run(request, max_steps=3)

    assert caught.value is failure
    assert model.requests == (request,)
    assert tool.requests == [ToolRequest("failing", {})]


@pytest.mark.parametrize("max_steps", [1, 2, 4])
def test_step_exhaustion_executes_final_tool_but_never_completes_an_extra_turn(
    max_steps: int,
) -> None:
    call = ToolCall("repeat", {})
    model = FakeModelPort(
        (ModelResponse(call),) * max_steps + (ModelResponse(FinalAnswer("Beyond the bound")),)
    )
    tool = _tool("repeat", *["Observed"] * max_steps)
    request = _request()

    with pytest.raises(RuntimeError, match="^agent max_steps exhausted$"):
        AgentRuntime(model, ToolRegistry((tool,))).run(request, max_steps=max_steps)

    assert len(model.requests) == max_steps
    assert len(tool.requests) == max_steps
    observation = ToolObservation(call, ToolResult("Observed"))
    for index, recorded in enumerate(model.requests):
        assert recorded.messages == (*request.messages, *(observation,) * index)
    assert request == _request()


def test_identically_configured_fakes_reproduce_result_and_complete_history() -> None:
    def run() -> tuple[AgentRunResult, tuple[ModelRequest, ...], tuple[ToolRequest, ...]]:
        model = FakeModelPort(
            (
                ModelResponse(ToolCall("test", {"a": 1})),
                ModelResponse(ToolCall("test", {"a": 2})),
                ModelResponse(FinalAnswer("done")),
            )
        )
        tool = _tool("test", "First", "Second")
        result = AgentRuntime(model, ToolRegistry((tool,))).run(_request(), max_steps=3)
        return result, model.requests, tool.requests

    assert run() == run()


def test_runtime_reuse_starts_fresh_history_and_counter_without_resetting_injected_fakes() -> None:
    responses = (ModelResponse(ToolCall("test", {})), ModelResponse(FinalAnswer("done")))
    model = FakeModelPort(responses * 2)
    tool = _tool("test", "First run", "Second run")
    runtime = AgentRuntime(model, ToolRegistry((tool,)))
    first = _request()
    second = ModelRequest((ModelMessage("user", "Different question"),))

    assert runtime.run(first, max_steps=2) == AgentRunResult("done", 2)
    assert runtime.run(second, max_steps=2) == AgentRunResult("done", 2)

    assert model.requests[2] is second
    assert model.requests[3].messages == (
        *second.messages,
        ToolObservation(ToolCall("test", {}), ToolResult("Second run")),
    )


@pytest.mark.parametrize("bound", [0, -1, True, False, 1.0, "2", None])
def test_invalid_bound_is_rejected_before_any_port_call(bound: object) -> None:
    model = FakeModelPort((ModelResponse(FinalAnswer("done")),))
    tool = _tool("test")

    with pytest.raises(ValueError, match="max_steps must be a positive integer"):
        AgentRuntime(model, ToolRegistry((tool,))).run(_request(), max_steps=cast(int, bound))

    assert model.requests == ()
    assert tool.requests == ()


@pytest.mark.parametrize("invalid", [None, "Question", (), ModelMessage("user", "Question")])
def test_invalid_request_is_rejected_before_any_port_call(invalid: object) -> None:
    model = FakeModelPort((ModelResponse(FinalAnswer("done")),))
    tool = _tool("test")

    with pytest.raises(ValueError, match="request must be a ModelRequest"):
        AgentRuntime(model, ToolRegistry((tool,))).run(cast(ModelRequest, invalid), max_steps=1)

    assert model.requests == ()
    assert tool.requests == ()


@pytest.mark.parametrize("model", [None, object(), "model"])
def test_runtime_rejects_missing_model_capability(model: object) -> None:
    with pytest.raises(ValueError, match="model must expose a callable complete"):
        AgentRuntime(cast(ModelPort, model), ToolRegistry(()))


@pytest.mark.parametrize("tools", [None, (), []])
def test_runtime_requires_registry(tools: object) -> None:
    with pytest.raises(ValueError, match="tools must be a ToolRegistry"):
        AgentRuntime(FakeModelPort(()), cast(ToolRegistry, tools))


@pytest.mark.parametrize("steps", [0, -1, True, False, 1.0, "2", None])
def test_run_result_requires_positive_integer_steps(steps: object) -> None:
    with pytest.raises(ValueError, match="steps must be a positive integer"):
        AgentRunResult("done", cast(int, steps))


@pytest.mark.parametrize("answer", ["", "  \n", None, 1])
def test_run_result_requires_nonblank_answer(answer: object) -> None:
    with pytest.raises(ValueError, match="content must be a nonempty string"):
        AgentRunResult(cast(str, answer), 1)


@pytest.mark.parametrize(
    ("contract", "field"),
    [
        (AgentRunResult("done", 1), "final_answer"),
        (AgentRunResult("done", 1), "steps"),
        (AgentRuntime(FakeModelPort(()), ToolRegistry(())), "model"),
        (AgentRuntime(FakeModelPort(()), ToolRegistry(())), "tools"),
    ],
)
def test_runtime_dependencies_and_result_are_frozen_and_slotted(
    contract: object, field: str
) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(contract, field, None)
    assert not hasattr(contract, "__dict__")
