from dataclasses import FrozenInstanceError
from typing import Literal, cast

import pytest

from finance_research_agent.agent import ModelMessage, ModelPort, ModelRequest, ModelResponse


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
def test_invalid_response_content_rejected(content: object) -> None:
    with pytest.raises(ValueError, match="content must be a nonempty string"):
        ModelResponse(cast(str, content))


def test_response_preserves_nonblank_text() -> None:
    content = "  Risk-off\nRésumé 市场\t "

    assert ModelResponse(content).content is content


@pytest.mark.parametrize("messages", [[], [ModelMessage("user", "Text")], None, "Text"])
def test_request_requires_tuple(messages: object) -> None:
    with pytest.raises(ValueError, match="messages must be an immutable tuple"):
        ModelRequest(cast(tuple[ModelMessage, ...], messages))


def test_request_requires_messages() -> None:
    with pytest.raises(ValueError, match="messages must not be empty"):
        ModelRequest(())


@pytest.mark.parametrize("invalid", [None, "Text", ModelResponse("Text"), 1])
def test_request_rejects_non_message_members(invalid: object) -> None:
    messages = (ModelMessage("user", "Text"), invalid)

    with pytest.raises(ValueError, match="messages must contain ModelMessage values"):
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
        (ModelResponse("Answer"), "content", "Changed"),
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
        return ModelResponse(request.messages[-1].content)


def test_protocol_accepts_structural_implementation_without_inheritance() -> None:
    model: ModelPort = _IndependentModel()
    request = ModelRequest((ModelMessage("user", "Caller text"),))

    assert model.complete(request) == ModelResponse("Caller text")
