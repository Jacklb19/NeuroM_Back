"""Every failure answers {"code": ...}, documented in OpenAPI, readable by an allowed
origin, and with nothing that could leak input or internals."""

import logging
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Final

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.constants import API_PREFIX, HEALTH_PATH
from app.errors import ApiError, ErrorCode, ErrorResponse
from tests.fakes import ALLOWED_ORIGIN

PROBE_PATH: Final = "/test-only/probe"
LEAKY_MESSAGE: Final = "internal detail that must stay on the server"
FAKE_BEARER_TOKEN: Final = "fake-bearer-token-for-tests"
PARTIAL_BODY: Final = b"first chunk of a streamed response"
ALLOW_ORIGIN: Final = "access-control-allow-origin"
OPENAPI_ANY_OTHER_STATUS: Final = "default"
JSON_MEDIA_TYPE: Final = "application/json"
ERROR_SCHEMA_REF: Final = f"#/components/schemas/{ErrorResponse.__name__}"
FRAMEWORK_VALIDATION_SCHEMA: Final = "HTTPValidationError"


def test_unknown_route_answers_not_found_code(client: TestClient) -> None:
    response = client.get(f"{API_PREFIX}/does-not-exist")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": ErrorCode.NOT_FOUND}


def test_wrong_method_keeps_the_allow_header(client: TestClient) -> None:
    response = client.delete(f"{API_PREFIX}{HEALTH_PATH}")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.json() == {"code": ErrorCode.METHOD_NOT_ALLOWED}
    assert "GET" in response.headers["allow"]


def test_api_error_answers_its_status_and_code(app: FastAPI, client: TestClient) -> None:
    @app.get(PROBE_PATH)
    async def probe() -> None:
        raise ApiError(HTTPStatus.CONFLICT, ErrorCode.REQUEST_FAILED)

    response = client.get(PROBE_PATH)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {"code": ErrorCode.REQUEST_FAILED}


def test_other_http_errors_get_a_generic_code_and_keep_headers(
    app: FastAPI, client: TestClient
) -> None:
    challenge = {"WWW-Authenticate": "Bearer"}

    @app.get(PROBE_PATH)
    async def probe() -> None:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, detail=LEAKY_MESSAGE, headers=challenge)

    response = client.get(PROBE_PATH)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json() == {"code": ErrorCode.REQUEST_FAILED}
    assert response.headers["www-authenticate"] == challenge["WWW-Authenticate"]
    assert LEAKY_MESSAGE not in response.text


def test_invalid_input_is_rejected_without_echoing_it(app: FastAPI, client: TestClient) -> None:
    rejected_input = "not-a-number-with-private-text"

    @app.get(PROBE_PATH)
    async def probe(amount: int) -> int:
        return amount

    response = client.get(PROBE_PATH, params={"amount": rejected_input})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    assert response.json() == {"code": ErrorCode.INVALID_REQUEST}
    assert rejected_input not in response.text


def add_failing_probe(app: FastAPI) -> None:
    @app.get(PROBE_PATH)
    async def probe() -> None:
        raise RuntimeError(LEAKY_MESSAGE)


def test_unexpected_error_hides_its_message(app: FastAPI, client: TestClient) -> None:
    add_failing_probe(app)

    # The client re-raises any exception that reaches the server, so getting a
    # response at all proves the API answered the failure itself.
    response = client.get(PROBE_PATH)

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.json() == {"code": ErrorCode.INTERNAL_ERROR}
    assert LEAKY_MESSAGE not in response.text


def test_unexpected_error_reaches_an_allowed_origin(app: FastAPI, client: TestClient) -> None:
    add_failing_probe(app)

    response = client.get(PROBE_PATH, headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.headers.get(ALLOW_ORIGIN) == ALLOWED_ORIGIN
    assert response.json() == {"code": ErrorCode.INTERNAL_ERROR}


def test_unexpected_error_is_logged_without_request_data(
    app: FastAPI, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    add_failing_probe(app)

    with caplog.at_level(logging.ERROR):
        client.get(PROBE_PATH, headers={"Authorization": f"Bearer {FAKE_BEARER_TOKEN}"})

    [record] = [entry for entry in caplog.records if entry.levelno >= logging.ERROR]
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)
    assert FAKE_BEARER_TOKEN not in caplog.text


def test_failure_after_the_response_started_is_left_to_the_server(
    app: FastAPI, client: TestClient
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield PARTIAL_BODY
        raise RuntimeError(LEAKY_MESSAGE)

    @app.get(PROBE_PATH)
    async def probe() -> StreamingResponse:
        return StreamingResponse(chunks())

    # A second response cannot follow headers already sent; the error must
    # reach the server so it aborts the connection.
    with pytest.raises(RuntimeError):
        client.get(PROBE_PATH)


def test_every_operation_documents_the_error_contract(app: FastAPI) -> None:
    schema = app.openapi()
    operations = [
        operation for path_item in schema["paths"].values() for operation in path_item.values()
    ]

    assert ErrorResponse.__name__ in schema["components"]["schemas"]
    assert operations
    for operation in operations:
        error = operation["responses"][OPENAPI_ANY_OTHER_STATUS]
        assert error["content"][JSON_MEDIA_TYPE]["schema"] == {"$ref": ERROR_SCHEMA_REF}


def test_invalid_input_is_not_documented_with_the_framework_body(app: FastAPI) -> None:
    @app.get(PROBE_PATH)
    async def probe(amount: int) -> int:
        return amount

    schema = app.openapi()

    assert str(HTTPStatus.UNPROCESSABLE_CONTENT.value) not in (
        schema["paths"][PROBE_PATH]["get"]["responses"]
    )
    assert FRAMEWORK_VALIDATION_SCHEMA not in schema["components"]["schemas"]
