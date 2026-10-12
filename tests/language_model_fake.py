"""An in-memory language model for the tests: no test ever calls the real service."""

import json
from http import HTTPStatus
from typing import Any

import httpx2

from app.constants import LANGUAGE_MODEL_CHAT_PATH


def chat_answer(content: Any) -> httpx2.Response:
    """A chat completion whose message is ``content``: a JSON object, or raw text when a string."""
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx2.Response(HTTPStatus.OK, json={"choices": [{"message": {"role": "assistant", "content": text}}]})


def busy(retry_after: str | None = "0", status: int = HTTPStatus.TOO_MANY_REQUESTS) -> httpx2.Response:
    """The model asking to try again, as a rate limit does."""
    headers = {} if retry_after is None else {"retry-after": retry_after}
    return httpx2.Response(status, json={"error": {"message": "rate limit"}}, headers=headers)


class FakeLanguageModel:
    """Answers the requests the API sends to the language model and remembers them."""

    def __init__(self) -> None:
        self.answers: list[httpx2.Response] = []
        """Answers in the order they are given; the last one repeats."""
        self.requests: list[httpx2.Request] = []
        self.unreachable = False

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    @property
    def bodies(self) -> list[dict[str, Any]]:
        """What was sent each time, decoded."""
        return [json.loads(request.content) for request in self.requests]

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.unreachable:
            raise httpx2.ConnectError("the language model is unreachable", request=request)
        self.requests.append(request)
        if request.url.path.endswith(LANGUAGE_MODEL_CHAT_PATH) and self.answers:
            return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return httpx2.Response(HTTPStatus.NOT_FOUND)
