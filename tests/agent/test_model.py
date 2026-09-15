from dataclasses import FrozenInstanceError
from typing import Literal, assert_type, cast

import pytest

from finance_research_agent.agent import (
    AssistantAction,
    FinalAnswer,
    ModelMessage,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolObservation,
    ToolRequest,
    ToolResult,
)


@pytest.mark.parametrize("role", ["system", "user", "assistant"])
def test_supported_roles_preserve_text(role: Literal["system", "user", "assistant"]) -> None:
    content = "  Research only.\nRésumé 市场\t "

    message = ModelMessage(role, content)

    assert message.role == role
    assert message.content is content


@pytest.mark.parametrize("role", ["tool", "SYSTEM", " user ", "", None, 1, []])
def test_unsupported_role_rejected(role: object) -> None:
    with pytest.raises(ValueError, match="role must be system, user, or assistant"):
        ModelMessage(cast(Literal["system", "user", "assistant"], role), "Text")


@pytest.mark.parametrize("content", ["", " ", "\t\n\r", "\u2003", None, 1, b"Text", []])
def test_invalid_message_content_rejected(content: object) -> None:
    with pytest.raises(ValueError, match="content must be a nonempty string"):
        ModelMessage("user", cast(str, content))


@pytest.mark.parametrize("content", ["", " ", "\t\n\r", "\u2003", None, 1, b"Text", []])
def test_invalid_final_answer_content_rejected(content: object) -> None:
    with pytest.raises(ValueError, match="content must be a nonempty string"):
        FinalAnswer(cast(str, content))


def test_final_answer_preserves_nonblank_text() -> None:
    content = "  Risk-off\nRésumé 市场\t "

    assert FinalAnswer(content).content is content


@pytest.mark.parametrize("messages", [[], [ModelMessage("user", "Text")], None, "Text"])
def test_request_requires_tuple(messages: object) -> None:
    with pytest.raises(ValueError, match="messages must be an immutable tuple"):
        ModelRequest(cast(tuple[ModelMessage, ...], messages))


def test_request_requires_messages() -> None:
    with pytest.raises(ValueError, match="messages must not be empty"):
        ModelRequest(())


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "Text",
        ModelResponse(action=FinalAnswer("Text")),
        1,
        ToolCall("test", {}),
        ToolResult("Result"),
    ],
)
def test_request_rejects_non_message_members(invalid: object) -> None:
    messages = (ModelMessage("user", "Text"), invalid)

    with pytest.raises(ValueError, match="messages must contain ModelMessage or ToolObservation"):
        ModelRequest(cast(tuple[ModelMessage, ...], messages))


def test_request_preserves_tuple_order_duplicates_and_unrestricted_role_sequence() -> None:
    assistant = ModelMessage("assistant", "Earlier reply")
    user = ModelMessage("user", "Question")
    system = ModelMessage("system", "Research only")
    messages = (assistant, user, user, system)

    request = ModelRequest(messages)

    assert request.messages is messages
    assert all(actual is expected for actual, expected in zip(request.messages, messages))


def test_request_does_not_add_system_prompt() -> None:
    message = ModelMessage("user", "Question")

    assert ModelRequest((message,)).messages == (message,)


@pytest.mark.parametrize(
    ("contract", "field", "replacement"),
    [
        (ModelMessage("user", "Question"), "role", "system"),
        (ModelMessage("user", "Question"), "content", "Changed"),
        (ModelRequest((ModelMessage("user", "Question"),)), "messages", ()),
        (FinalAnswer("Answer"), "content", "Changed"),
        (ToolCall("test", {}), "name", "changed"),
        (ToolCall("test", {}), "arguments", {}),
        (ModelResponse(action=FinalAnswer("Answer")), "action", ToolCall("test", {})),
        (ModelResponse(action=ToolCall("test", {})), "action", FinalAnswer("Answer")),
        (ToolObservation(ToolCall("test", {}), ToolResult("Result")), "call", None),
        (ToolObservation(ToolCall("test", {}), ToolResult("Result")), "result", None),
    ],
)
def test_contracts_are_frozen_and_slotted(
    contract: object, field: str, replacement: object
) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(contract, field, replacement)
    assert not hasattr(contract, "__dict__")


class _IndependentModel:
    def complete(self, request: ModelRequest) -> ModelResponse:
        last = request.messages[-1]
        content = last.content if isinstance(last, ModelMessage) else last.result.content
        return ModelResponse(action=FinalAnswer(content))


def test_protocol_accepts_structural_implementation_without_inheritance() -> None:
    model: ModelPort = _IndependentModel()
    request = ModelRequest((ModelMessage("user", "Caller text"),))

    assert model.complete(request) == ModelResponse(action=FinalAnswer("Caller text"))


@pytest.mark.parametrize("action", [FinalAnswer("Answer"), ToolCall("unregistered", {})])
def test_response_accepts_and_preserves_each_action(action: AssistantAction) -> None:
    response = ModelResponse(action=action)

    assert response.action is action
    assert not hasattr(response, "content")


@pytest.mark.parametrize(
    "action",
    [
        None,
        "Answer",
        1,
        {},
        (),
        object(),
        ToolRequest("test", {}),
        ModelMessage("assistant", "Answer"),
    ],
)
def test_response_rejects_unsupported_actions(action: object) -> None:
    with pytest.raises(ValueError, match="action must be a FinalAnswer or ToolCall"):
        ModelResponse(action=cast(AssistantAction, action))


def test_assistant_action_union_supports_both_variants_and_narrowing() -> None:
    actions: tuple[AssistantAction, ...] = (FinalAnswer("Answer"), ToolCall("test", {}))
    for action in actions:
        assert_type(action, AssistantAction)
        if isinstance(action, FinalAnswer):
            assert_type(action, FinalAnswer)
            assert action.content == "Answer"
        else:
            assert_type(action, ToolCall)
            assert action.name == "test"


def test_tool_call_and_execution_request_are_distinct_protocol_stages() -> None:
    call = ToolCall("test", {"limit": 2})
    request = ToolRequest("test", {"limit": 2})

    assert not isinstance(call, ToolRequest)
    assert not isinstance(request, ToolCall)
    assert not hasattr(call, "execute")


def test_observation_retains_original_call_and_result_without_conversion() -> None:
    call = ToolCall("test", {"label": "  Résumé 市场\n", "limit": 2})
    result = ToolResult("  Unchanged result\t\n")

    observation = ToolObservation(call, result)

    assert observation.call is call
    assert observation.result is result


@pytest.mark.parametrize("call", [None, "test", ToolRequest("test", {}), FinalAnswer("Done")])
def test_observation_rejects_invalid_call(call: object) -> None:
    with pytest.raises(ValueError, match="call must be a ToolCall"):
        ToolObservation(cast(ToolCall, call), ToolResult("Result"))


@pytest.mark.parametrize(
    "result", [None, "Result", FinalAnswer("Done"), ModelMessage("user", "Text")]
)
def test_observation_rejects_invalid_result(result: object) -> None:
    with pytest.raises(ValueError, match="result must be a ToolResult"):
        ToolObservation(ToolCall("test", {}), cast(ToolResult, result))


def test_request_preserves_mixed_history_tuple_order_identity_and_duplicates() -> None:
    observation = ToolObservation(ToolCall("test", {}), ToolResult("Result"))
    message = ModelMessage("user", "Follow up")
    messages = (observation, message, observation)

    request = ModelRequest(messages)

    assert request.messages is messages
    assert ModelRequest((observation,)).messages == (observation,)
    for entry in request.messages:
        if isinstance(entry, ModelMessage):
            assert_type(entry, ModelMessage)
            assert entry is message
        else:
            assert_type(entry, ToolObservation)
            assert entry is observation
