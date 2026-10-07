"""Shared fixtures: an app built from fake settings, so no test needs real secrets."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.factory import create_app
from tests.fakes import FAKE_VALUES


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate(FAKE_VALUES)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _fresh_settings_cache() -> Iterator[None]:
    """Keep the process-wide settings cache from leaking between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
