"""Application wiring: the entry point Vercel serves and the settings endpoints receive."""

import importlib
import sys
from http import HTTPStatus
from collections.abc import Iterator
from typing import Annotated, Final

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, SettingsError, get_settings
from app.constants import API_PREFIX, HEALTH_PATH, PRODUCTION_ENVIRONMENT, SESSIONS_PATH
from app.factory import create_app
from tests.fakes import fake_environ
from tests.language_model_fake import FakeLanguageModel
from tests.supabase_fake import FakeSupabase

ENTRYPOINT_MODULE: Final = "app.main"
PROBE_PATH: Final = "/test-only/settings"


@pytest.fixture
def fresh_entrypoint() -> Iterator[None]:
    """Import the entry point from scratch and forget it afterwards."""
    sys.modules.pop(ENTRYPOINT_MODULE, None)
    yield
    sys.modules.pop(ENTRYPOINT_MODULE, None)


@pytest.mark.usefixtures("fresh_entrypoint")
def test_entrypoint_exposes_an_app_built_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in fake_environ().items():
        monkeypatch.setenv(name, value)

    entrypoint = importlib.import_module(ENTRYPOINT_MODULE)

    assert isinstance(entrypoint.app, FastAPI)


@pytest.mark.usefixtures("fresh_entrypoint")
def test_entrypoint_refuses_to_start_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in fake_environ():
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SettingsError):
        importlib.import_module(ENTRYPOINT_MODULE)


def test_endpoints_receive_the_settings_the_app_was_built_with(
    app: FastAPI, client: TestClient, settings: Settings
) -> None:
    @app.get(PROBE_PATH)
    async def probe(current: Annotated[Settings, Depends(get_settings)]) -> bool:
        return current is settings

    assert client.get(PROBE_PATH).json() is True


# --- Documentation and response headers (ADR-54) ---

DOCUMENTATION_PATHS: Final = ("/docs", "/redoc", "/openapi.json")


def test_documentation_is_served_outside_production(client: TestClient) -> None:
    for path in DOCUMENTATION_PATHS:
        assert client.get(path).status_code == HTTPStatus.OK


def test_production_does_not_publish_its_documentation(
    settings: Settings, supabase: FakeSupabase, language_model: FakeLanguageModel
) -> None:
    production = settings.model_copy(update={"vercel_env": PRODUCTION_ENVIRONMENT})
    with TestClient(create_app(production, supabase.transport, language_model.transport)) as client:
        for path in DOCUMENTATION_PATHS:
            assert client.get(path).status_code == HTTPStatus.NOT_FOUND
        # The API itself is unchanged.
        assert client.get(f"{API_PREFIX}{HEALTH_PATH}").status_code == HTTPStatus.OK


def test_every_answer_forbids_caching_and_sniffing(client: TestClient) -> None:
    answers = (
        client.get(f"{API_PREFIX}{HEALTH_PATH}"),
        client.get(f"{API_PREFIX}{SESSIONS_PATH}"),
        client.get("/no-such-route"),
    )
    assert [answer.status_code for answer in answers] == [HTTPStatus.OK, HTTPStatus.UNAUTHORIZED, HTTPStatus.NOT_FOUND]
    for answer in answers:
        assert answer.headers["Cache-Control"] == "no-store"
        assert answer.headers["X-Content-Type-Options"] == "nosniff"
