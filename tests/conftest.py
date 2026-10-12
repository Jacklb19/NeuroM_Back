"""Shared fixtures: an app built from fake settings, so no test needs real secrets."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.factory import create_app
from tests.fakes import FAKE_VALUES
from tests.language_model_fake import FakeLanguageModel
from tests.supabase_fake import KEY_ID, FakeSupabase, SigningKey


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate(FAKE_VALUES)


@pytest.fixture(scope="session")
def signing_key() -> SigningKey:
    """Generated once: creating a key pair is the slow part of signing a token."""
    return SigningKey(KEY_ID)


@pytest.fixture
def supabase(signing_key: SigningKey) -> FakeSupabase:
    return FakeSupabase(signing_key)


@pytest.fixture
def language_model() -> FakeLanguageModel:
    return FakeLanguageModel()


@pytest.fixture
def app(settings: Settings, supabase: FakeSupabase, language_model: FakeLanguageModel) -> FastAPI:
    return create_app(settings, supabase.transport, language_model.transport)


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
