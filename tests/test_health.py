"""GET /v1/health: public check of the service and of Supabase (Table 15)."""

from http import HTTPStatus

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.constants import API_PREFIX, API_VERSION, HEALTH_CHECK_CACHE_SECONDS, HEALTH_PATH
from app.health_probe import SupabaseProbe
from app.routes.health import DependencyStatus, HealthStatus
from tests.fakes import FAKE_VALUES
from tests.supabase_fake import FakeSupabase

HEALTH_URL = f"{API_PREFIX}{HEALTH_PATH}"


def test_health_reports_ok_version_and_supabase_without_credentials(client: TestClient) -> None:
    response = client.get(HEALTH_URL)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": HealthStatus.OK, "version": API_VERSION, "supabase": DependencyStatus.OK}


def test_health_answers_503_while_supabase_cannot_be_reached(client: TestClient, supabase: FakeSupabase) -> None:
    supabase.unreachable = True

    response = client.get(HEALTH_URL)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json() == {
        "status": HealthStatus.DEGRADED,
        "version": API_VERSION,
        "supabase": DependencyStatus.UNREACHABLE,
    }


def test_health_reuses_its_look_at_supabase_for_a_while(app: FastAPI, supabase: FakeSupabase) -> None:
    now = [1000.0]
    app.state.supabase_probe = SupabaseProbe(app.state.supabase, clock=lambda: now[0])
    with TestClient(app) as client:
        for _ in range(5):
            assert client.get(HEALTH_URL).status_code == HTTPStatus.OK
        assert supabase.key_downloads == 1

        now[0] += HEALTH_CHECK_CACHE_SECONDS
        client.get(HEALTH_URL)
        assert supabase.key_downloads == 2


def test_health_sends_only_the_public_key(client: TestClient, supabase: FakeSupabase) -> None:
    client.get(HEALTH_URL)

    (look,) = supabase.key_requests
    assert "authorization" not in look.headers
    assert look.headers["apikey"] == FAKE_VALUES["supabase_anon_key"]


def test_health_is_documented_in_openapi(app: FastAPI) -> None:
    assert "get" in app.openapi()["paths"][HEALTH_URL]
