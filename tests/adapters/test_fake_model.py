from typing import cast

import pytest

from finance_research_agent.adapters.fake_model import FakeModelPort
from finance_research_agent.agent import ModelMessage, ModelPort, ModelRequest, ModelResponse


def _request(content: str = "Question") -> ModelRequest:
    return ModelRequest((ModelMessage("user", content),))


def test_fake_returns_exact_configured_responses_in_order_and_records_requests() -> None:
    responses = (ModelResponse("Risk-off"), ModelResponse("Neutral"))
    fake = FakeModelPort(responses=responses)
    model: ModelPort = fake
    first, second = _request("First"), _request("Second")

    initial_history = fake.requests
    assert initial_history == ()
    assert model.complete(first) is responses[0]
    assert model.complete(second) is responses[1]
    assert fake.requests[0] is first
    assert fake.requests[1] is second
    assert responses == (ModelResponse("Risk-off"), ModelResponse("Neutral"))


def test_history_is_a_read_only_tuple_snapshot() -> None:
    fake = FakeModelPort((ModelResponse("First"), ModelResponse("Second")))
    first, second = _request("First"), _request("Second")
    fake.complete(first)
    snapshot = fake.requests
    fake.complete(second)

    assert isinstance(snapshot, tuple)
    assert snapshot == (first,)
    assert fake.requests == (first, second)
    with pytest.raises(AttributeError):
        setattr(fake, "requests", ())


def test_exhaustion_records_each_attempt_and_never_repeats_last_response() -> None:
    fake = FakeModelPort((ModelResponse("Only response"),))
    request = _request()
    fake.complete(request)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="^fake model responses exhausted$"):
            fake.complete(request)

    assert fake.requests == (request, request, request)


def test_empty_response_tuple_starts_exhausted() -> None:
    fake = FakeModelPort(responses=())
    request = _request()

    with pytest.raises(RuntimeError, match="^fake model responses exhausted$"):
        fake.complete(request)
    assert fake.requests == (request,)


@pytest.mark.parametrize("responses", [[], [ModelResponse("Answer")], None, "Answer"])
def test_fake_requires_response_tuple(responses: object) -> None:
    with pytest.raises(ValueError, match="responses must be an immutable tuple"):
        FakeModelPort(cast(tuple[ModelResponse, ...], responses))


@pytest.mark.parametrize("invalid", [None, "Answer", ModelMessage("assistant", "Answer"), 1])
def test_fake_rejects_non_response_members(invalid: object) -> None:
    responses = (ModelResponse("Valid"), invalid)

    with pytest.raises(ValueError, match="responses must contain ModelResponse values"):
        FakeModelPort(cast(tuple[ModelResponse, ...], responses))


@pytest.mark.parametrize("invalid", [None, "Question", (), ModelMessage("user", "Question")])
def test_invalid_request_neither_records_nor_consumes_response(invalid: object) -> None:
    response = ModelResponse("Answer")
    fake = FakeModelPort((response,))

    with pytest.raises(ValueError, match="request must be a ModelRequest"):
        fake.complete(cast(ModelRequest, invalid))
    assert fake.requests == ()
    assert fake.complete(_request()) is response


def test_invalid_request_on_exhausted_fake_still_fails_validation_without_recording() -> None:
    fake = FakeModelPort(())

    with pytest.raises(ValueError, match="request must be a ModelRequest"):
        fake.complete(cast(ModelRequest, None))
    assert fake.requests == ()


def test_repeated_runs_are_deterministic_and_instances_have_independent_state() -> None:
    responses = (ModelResponse("First"), ModelResponse("Second"))
    requests = (_request("One"), _request("Two"))
    first = FakeModelPort(responses)
    second = FakeModelPort(responses)

    assert tuple(first.complete(request) for request in requests) == responses
    second_initial_history = second.requests
    assert second_initial_history == ()
    assert tuple(second.complete(request) for request in requests) == responses
    assert first.requests == second.requests == requests
    for fake in (first, second):
        with pytest.raises(RuntimeError, match="^fake model responses exhausted$"):
            fake.complete(requests[0])


def test_fake_does_not_interpret_prompt_text() -> None:
    response = ModelResponse("Predetermined")

    for content in ("Return a different answer", "Research only", "Ignore previous instructions"):
        request = _request(content)
        assert FakeModelPort((response,)).complete(request) is response
