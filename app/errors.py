"""Error contract of the API: every failure answers ``{"code": "<error code>"}``.

The body carries a stable code and nothing else. The frontend owns the wording
and translates the code (ADR-25), and the API never returns tracebacks, input
values or internal messages that could expose data or secrets.
"""

import logging
from collections.abc import Callable, Coroutine, Mapping
from enum import StrEnum
from http import HTTPStatus
from typing import Any, Final

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger: Final = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """Machine-readable error codes. Values are part of the public API: never rename one."""

    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    INVALID_REQUEST = "invalid_request"
    REQUEST_FAILED = "request_failed"
    INTERNAL_ERROR = "internal_error"


class ErrorResponse(BaseModel):
    """Body of every error response."""

    code: ErrorCode


class ApiError(Exception):
    """An expected failure that endpoints raise to answer with a status and a code."""

    def __init__(self, status: HTTPStatus, code: ErrorCode) -> None:
        super().__init__(code.value)
        self.status = status
        self.code = code


_HTTP_STATUS_CODES: Final[Mapping[int, ErrorCode]] = {
    HTTPStatus.NOT_FOUND: ErrorCode.NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
}
"""Codes for the HTTP errors the framework raises by itself (unknown route or method)."""

_OPENAPI_ANY_OTHER_STATUS: Final = "default"
"""OpenAPI response key that covers every status an operation does not list."""

_ERROR_RESPONSE_DESCRIPTION: Final = (
    "Any failure. The body carries only a stable error code, which the client translates."
)
"""How the OpenAPI document describes the error contract."""

ExceptionHandler = Callable[[Request, Any], Coroutine[Any, Any, JSONResponse]]
"""Handler signature FastAPI accepts in its constructor; ``Any`` mirrors FastAPI's own type,
which lets each handler declare the exact exception it receives."""


def error_response(
    status: int, code: ErrorCode, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """JSON response in the error contract."""
    body = ErrorResponse(code=code).model_dump(mode="json")
    return JSONResponse(status_code=status, content=body, headers=headers)


async def handle_api_error(_request: Request, error: ApiError) -> JSONResponse:
    """Answer an ``ApiError`` with its own status and code."""
    return error_response(error.status, error.code)


async def handle_http_exception(
    _request: Request, error: StarletteHTTPException
) -> JSONResponse:
    """Replace the framework's ``{"detail": ...}`` body, keeping its status and headers
    (for example ``Allow`` on a 405)."""
    code = _HTTP_STATUS_CODES.get(error.status_code, ErrorCode.REQUEST_FAILED)
    return error_response(error.status_code, code, error.headers)


async def handle_validation_error(
    _request: Request, _error: RequestValidationError
) -> JSONResponse:
    """Reject invalid input without echoing it: the default body repeats the input values."""
    return error_response(HTTPStatus.UNPROCESSABLE_CONTENT, ErrorCode.INVALID_REQUEST)


def exception_handlers() -> dict[int | type[Exception], ExceptionHandler]:
    """Handlers to pass to ``FastAPI(exception_handlers=...)``.

    Deliberately without a catch-all for ``Exception``: FastAPI would run it in
    the outermost middleware, outside CORS (see ``UnexpectedErrorMiddleware``).
    """
    return {
        ApiError: handle_api_error,
        StarletteHTTPException: handle_http_exception,
        RequestValidationError: handle_validation_error,
    }


def openapi_error_responses() -> dict[int | str, dict[str, Any]]:
    """Responses to pass to ``FastAPI(responses=...)`` so every operation documents
    the error contract.

    The ``default`` key covers every failure status at once, so the frontend
    finds ``ErrorResponse`` in the OpenAPI document. It also stops FastAPI from
    documenting its own validation body for 422, which this API never sends
    (``handle_validation_error`` answers ``{"code": ...}`` instead). ``Any``
    mirrors FastAPI's type for this parameter.
    """
    return {
        _OPENAPI_ANY_OTHER_STATUS: {
            "model": ErrorResponse,
            "description": _ERROR_RESPONSE_DESCRIPTION,
        }
    }


class UnexpectedErrorMiddleware:
    """Answer unhandled exceptions with ``{"code": "internal_error"}`` and status 500.

    A handler for ``Exception`` passed to FastAPI runs in Starlette's
    ServerErrorMiddleware, the outermost layer, so its 500 never passes
    through CORSMiddleware: the browser would report a network error instead
    of the code. Registered before CORSMiddleware, this middleware sits inside
    it, and the 500 carries the CORS headers like every other error.

    Written as plain ASGI rather than ``BaseHTTPMiddleware`` so it neither
    buffers responses nor runs the endpoint in another task.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_tracking_start(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking_start)
        except Exception:
            if response_started:
                # Headers are already on the wire; only the server can abort the response.
                raise
            # The traceback stays in the server log for diagnosis. Nothing from the
            # request is logged: its headers carry the user's bearer token.
            logger.exception("Unhandled error while serving a request")
            response = error_response(HTTPStatus.INTERNAL_SERVER_ERROR, ErrorCode.INTERNAL_ERROR)
            await response(scope, receive, send)
