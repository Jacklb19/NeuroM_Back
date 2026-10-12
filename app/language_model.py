"""Calls to the language model (ADR-08): Groq's OpenAI-compatible chat API.

The model never takes part in the control loop (ADR-05). It is asked two
closed questions, each answered with a small JSON object that the caller
validates before anything leaves the API. The key lives only here and is
never logged; nothing of what the model answers is logged either.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any, Final

import httpx2

from app.config import Settings
from app.constants import (
    BEARER_SCHEME,
    LANGUAGE_MODEL,
    LANGUAGE_MODEL_BASE_URL,
    LANGUAGE_MODEL_CACHE_ENTRIES,
    LANGUAGE_MODEL_CACHE_SECONDS,
    LANGUAGE_MODEL_CHAT_PATH,
    LANGUAGE_MODEL_MAX_OUTPUT_TOKENS,
    LANGUAGE_MODEL_MAX_RETRY_WAIT_SECONDS,
    LANGUAGE_MODEL_REASONING_EFFORT,
    LANGUAGE_MODEL_TEMPERATURE,
    LANGUAGE_MODEL_TIMEOUT_SECONDS,
)
from app.errors import ApiError, ErrorCode

logger: Final = logging.getLogger(__name__)

_BUSY_STATUSES: Final = (HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.SERVICE_UNAVAILABLE)
"""Answers that mean "try again shortly": the rate limit and a model that is loading."""

_RETRY_AFTER_HEADER: Final = "retry-after"
_ATTEMPTS: Final = 2
"""The first try and one retry (ADR-08)."""


class InvalidModelOutput(ValueError):
    """The model answered something outside the closed schema of the question."""


def unavailable() -> ApiError:
    """The single answer for every way the language model can fail. The app then keeps
    its manual plan and its summary of indicators (ADR-08)."""
    return ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.LANGUAGE_MODEL_UNAVAILABLE)


class LanguageModelGateway:
    """HTTP access to the language model, with one bounded retry and a small cache.

    Built once per application. Tests pass a ``transport`` that answers in
    memory and a ``sleep`` that does not wait.
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key = settings.groq_api_key
        self._sleep = sleep
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._client = httpx2.AsyncClient(
            base_url=LANGUAGE_MODEL_BASE_URL, timeout=LANGUAGE_MODEL_TIMEOUT_SECONDS, transport=transport
        )

    @property
    def model(self) -> str:
        """Name of the model, returned with every generated text."""
        return LANGUAGE_MODEL

    async def ask[Answer](self, system: str, user: str, validate: Callable[[dict[str, Any]], Answer]) -> Answer:
        """Ask one question and return its validated answer.

        ``validate`` turns the model's JSON object into the answer or raises
        ``InvalidModelOutput``; only validated answers are returned or cached.
        ``Any`` mirrors JSON. Every failure is the same ``ApiError``.
        """
        cache_key = hashlib.sha256(f"{system}\x00{user}".encode()).hexdigest()
        cached = self._cached(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        try:
            answer = validate(await self._complete(system, user))
        except InvalidModelOutput as error:
            # Only the reason, written by this code: the text of the model stays out of the log.
            logger.warning("Language model answer rejected: %s", error)
            raise unavailable() from None
        self._remember(cache_key, answer)
        return answer

    async def _complete(self, system: str, user: str) -> dict[str, Any]:
        body = {
            "model": LANGUAGE_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": LANGUAGE_MODEL_TEMPERATURE,
            "max_completion_tokens": LANGUAGE_MODEL_MAX_OUTPUT_TOKENS,
            "reasoning_effort": LANGUAGE_MODEL_REASONING_EFFORT,
            "include_reasoning": False,
        }
        headers = {"Authorization": f"{BEARER_SCHEME} {self._key.get_secret_value()}"}
        for attempt in range(_ATTEMPTS):
            try:
                response = await self._client.post(LANGUAGE_MODEL_CHAT_PATH, headers=headers, json=body)
            except httpx2.HTTPError as error:
                # Only the kind of failure: the request carries the key.
                logger.warning("Language model request failed: %s", type(error).__name__)
                raise unavailable() from None
            if response.status_code == HTTPStatus.OK:
                return _json_object(response)
            wait = _retry_wait(response)
            if attempt + 1 < _ATTEMPTS and wait is not None:
                await self._sleep(wait)
                continue
            logger.warning("Language model answered %s", response.status_code)
            raise unavailable()
        raise unavailable()

    def _cached(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored_at, answer = entry
        if self._clock() - stored_at >= LANGUAGE_MODEL_CACHE_SECONDS:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return answer

    def _remember(self, key: str, answer: Any) -> None:
        self._cache[key] = (self._clock(), answer)
        self._cache.move_to_end(key)
        while len(self._cache) > LANGUAGE_MODEL_CACHE_ENTRIES:
            self._cache.popitem(last=False)


def _retry_wait(response: httpx2.Response) -> float | None:
    """Seconds to wait before the retry, or None when the answer is not worth retrying.

    The wait the model asks for is respected (ADR-08), up to
    ``LANGUAGE_MODEL_MAX_RETRY_WAIT_SECONDS``: a longer one would outlast the function.
    """
    if response.status_code not in _BUSY_STATUSES:
        return None
    try:
        wait = float(response.headers.get(_RETRY_AFTER_HEADER, "0"))
    except ValueError:
        return None
    return wait if 0 <= wait <= LANGUAGE_MODEL_MAX_RETRY_WAIT_SECONDS else None


def _json_object(response: httpx2.Response) -> dict[str, Any]:
    """The JSON object the model wrote, taken out of the chat envelope."""
    try:
        content = response.json()["choices"][0]["message"]["content"]
        answer = json.loads(content)
    except (ValueError, LookupError, TypeError):
        logger.warning("Language model answer could not be read")
        raise unavailable() from None
    if not isinstance(answer, dict):
        logger.warning("Language model answer is not an object")
        raise unavailable()
    return answer
