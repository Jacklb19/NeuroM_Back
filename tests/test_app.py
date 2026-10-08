"""Application wiring: the entry point Vercel serves and the settings endpoints receive."""

import importlib
import sys
from collections.abc import Iterator
from typing import Annotated, Final

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, SettingsError, get_settings
from tests.fakes import fake_environ

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
