"""GET /v1/health: public liveness check (Table 15)."""

from http import HTTPStatus

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.constants import API_PREFIX, API_VERSION, HEALTH_PATH
from app.routes.health import HealthStatus

HEALTH_URL = f"{API_PREFIX}{HEALTH_PATH}"


def test_health_reports_ok_and_version_without_credentials(client: TestClient) -> None:
    response = client.get(HEALTH_URL)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": HealthStatus.OK, "version": API_VERSION}


def test_health_is_documented_in_openapi(app: FastAPI) -> None:
    assert "get" in app.openapi()["paths"][HEALTH_URL]
