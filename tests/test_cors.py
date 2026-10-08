"""CORS admits only the configured frontend origins, without credentials (ADR-19)."""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.constants import API_PREFIX, CORS_ALLOWED_HEADERS, CORS_ALLOWED_METHODS, HEALTH_PATH
from tests.fakes import ALLOWED_ORIGIN, DISALLOWED_ORIGIN, LOCAL_ORIGIN

HEALTH_URL = f"{API_PREFIX}{HEALTH_PATH}"
ALLOW_ORIGIN = "access-control-allow-origin"
ALLOW_CREDENTIALS = "access-control-allow-credentials"
UNLISTED_METHOD = "PATCH"


def preflight_headers(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": CORS_ALLOWED_METHODS[0],
        "Access-Control-Request-Headers": ", ".join(CORS_ALLOWED_HEADERS),
    }


@pytest.mark.parametrize("origin", [ALLOWED_ORIGIN, LOCAL_ORIGIN])
def test_allowed_origin_is_echoed(client: TestClient, origin: str) -> None:
    response = client.get(HEALTH_URL, headers={"Origin": origin})

    assert response.headers.get(ALLOW_ORIGIN) == origin
    assert ALLOW_CREDENTIALS not in response.headers


def test_disallowed_origin_gets_no_cors_header(client: TestClient) -> None:
    response = client.get(HEALTH_URL, headers={"Origin": DISALLOWED_ORIGIN})

    assert ALLOW_ORIGIN not in response.headers


def test_preflight_from_allowed_origin_succeeds(client: TestClient) -> None:
    response = client.options(HEALTH_URL, headers=preflight_headers(ALLOWED_ORIGIN))

    assert response.status_code == HTTPStatus.OK
    assert response.headers.get(ALLOW_ORIGIN) == ALLOWED_ORIGIN
    assert ALLOW_CREDENTIALS not in response.headers


def test_preflight_from_disallowed_origin_is_rejected(client: TestClient) -> None:
    response = client.options(HEALTH_URL, headers=preflight_headers(DISALLOWED_ORIGIN))

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert ALLOW_ORIGIN not in response.headers


def test_preflight_rejects_unlisted_method(client: TestClient) -> None:
    assert UNLISTED_METHOD not in CORS_ALLOWED_METHODS
    headers = preflight_headers(ALLOWED_ORIGIN) | {"Access-Control-Request-Method": UNLISTED_METHOD}

    response = client.options(HEALTH_URL, headers=headers)

    assert response.status_code == HTTPStatus.BAD_REQUEST
