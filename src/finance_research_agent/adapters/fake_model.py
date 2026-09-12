"""Deterministic model dependency substitution for offline runtime tests."""

from finance_research_agent.agent.model import ModelRequest, ModelResponse

__all__ = ["FakeModelPort"]


class FakeModelPort:
    """Consume predetermined responses without interpreting request text.

    Each instance owns its call history and response cursor. An empty response
    tuple is valid and starts exhausted. All valid requests are recorded,
    including exhausted calls; invalid requests leave state unchanged.
    """

    def __init__(self, responses: tuple[ModelResponse, ...]) -> None:
        if not isinstance(responses, tuple):
            raise ValueError("responses must be an immutable tuple")
        if any(not isinstance(response, ModelResponse) for response in responses):
            raise ValueError("responses must contain ModelResponse values")
        self._responses = responses
        self._next_response = 0
        self._requests: list[ModelRequest] = []

    @property
    def requests(self) -> tuple[ModelRequest, ...]:
        """Return an immutable snapshot of received requests in call order."""

        return tuple(self._requests)

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return the next response or raise RuntimeError on exhaustion."""

        if not isinstance(request, ModelRequest):
            raise ValueError("request must be a ModelRequest")
        self._requests.append(request)
        if self._next_response >= len(self._responses):
            raise RuntimeError("fake model responses exhausted")
        response = self._responses[self._next_response]
        self._next_response += 1
        return response
