"""Saved melodies: list, save and delete (P-17), acting as the person in Supabase, without a second factor."""

from http import HTTPStatus
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from app.constants import (
    API_PREFIX,
    MAX_MELODY_POSITION_SECONDS,
    MAX_MELODY_SEED,
    MELODIES_PATH,
    MELODY_NAME_MAX_CHARS,
)
from app.errors import ErrorCode
from tests.fakes import FAKE_VALUES
from tests.supabase_fake import FakeSupabase, SigningKey, bearer, database_error, make_token

MELODIES_URL: Final = f"{API_PREFIX}{MELODIES_PATH}"
MELODY_ID: Final = "019a8c8e-2b10-7000-8000-000000000001"
MELODY_URL: Final = f"{MELODIES_URL}/{MELODY_ID}"


def melody_body(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Bosque de niebla · 16 nov",
        "seed": 123_456,
        "instrument": "bowls",
        "ambience": "wind",
        "profile": "immersive",
        "level": "target",
        "position_seconds": 720,
        "duration_minutes": 30,
        "music_mix": 1,
        "ambience_mix": 0.6,
        "synthesis_version": 1,
        "created_at": "2026-11-16T21:00:00Z",
        "updated_at": "2026-11-16T21:00:00Z",
    } | overrides


def stored(**overrides: Any) -> dict[str, Any]:
    """A melody as the database lists it."""
    return {"id": MELODY_ID} | melody_body(**overrides)


@pytest.fixture
def headers(signing_key: SigningKey) -> dict[str, str]:
    # Melodies are not health data: a session without the second factor is enough.
    return bearer(make_token(signing_key, assurance="aal1"))


def test_saves_a_new_melody_as_the_person_and_never_with_the_service_key(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    supabase.results["save_melody"] = True

    response = client.put(MELODY_URL, json=melody_body(), headers=headers)

    assert response.status_code == HTTPStatus.CREATED
    assert response.json() == {"id": MELODY_ID, "created": True}
    (call,) = supabase.calls
    assert call.name == "save_melody"
    assert call.headers["authorization"] == headers["Authorization"]
    assert FAKE_VALUES["supabase_service_role_key"] not in str(call.headers)
    assert call.arguments["p_melody"] == stored(created_at="2026-11-16T21:00:00Z", updated_at="2026-11-16T21:00:00Z")


def test_saving_a_melody_again_answers_ok_without_a_duplicate(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    supabase.results["save_melody"] = False

    response = client.put(MELODY_URL, json=melody_body(name="Otro nombre"), headers=headers)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"id": MELODY_ID, "created": False}


def test_lists_the_melodies_of_the_account(client: TestClient, supabase: FakeSupabase, headers: dict[str, str]) -> None:
    supabase.results["list_melodies"] = [stored(), stored(name="Otra")]

    response = client.get(MELODIES_URL, headers=headers)

    assert response.status_code == HTTPStatus.OK
    assert [melody["name"] for melody in response.json()["melodies"]] == ["Bosque de niebla · 16 nov", "Otra"]
    assert supabase.calls[0].arguments == {}


def test_deletes_a_melody_and_says_when_there_was_none(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    supabase.results["delete_melody"] = True
    assert client.delete(MELODY_URL, headers=headers).status_code == HTTPStatus.NO_CONTENT
    assert supabase.calls[0].arguments == {"p_id": MELODY_ID}

    # Another person's melody answers like one that does not exist.
    supabase.results["delete_melody"] = False
    response = client.delete(MELODY_URL, headers=headers)
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": ErrorCode.NOT_FOUND}


@pytest.mark.parametrize(
    ("method", "url"),
    [("GET", MELODIES_URL), ("PUT", MELODY_URL), ("DELETE", MELODY_URL)],
)
def test_needs_an_account(client: TestClient, supabase: FakeSupabase, method: str, url: str) -> None:
    response = client.request(method, url, json=melody_body() if method == "PUT" else None)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json() == {"code": ErrorCode.UNAUTHENTICATED}
    assert not supabase.calls


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": " Bosque"},
        {"name": "x" * (MELODY_NAME_MAX_CHARS + 1)},
        {"seed": -1},
        {"seed": MAX_MELODY_SEED + 1},
        {"instrument": "trumpet"},
        {"ambience": "thunder"},
        {"profile": "loud"},
        {"level": "fast"},
        {"position_seconds": MAX_MELODY_POSITION_SECONDS + 1},
        {"music_mix": 1.5},
        {"ambience_mix": -0.1},
        {"synthesis_version": 0},
        {"duration_minutes": 20},
        {"duration_minutes": 15, "position_seconds": 15 * 60 + 1},
        {"created_at": "2026-11-16T21:00:00"},
        {"updated_at": "2026-11-16T20:00:00Z"},
        {"pulse": 72},
    ],
)
def test_refuses_a_melody_outside_its_closed_shape(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str], overrides: dict[str, Any]
) -> None:
    response = client.put(MELODY_URL, json=melody_body(**overrides), headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json() == {"code": ErrorCode.INVALID_REQUEST}
    assert not supabase.calls


def test_refuses_an_id_that_is_not_a_uuid(client: TestClient, supabase: FakeSupabase, headers: dict[str, str]) -> None:
    response = client.put(f"{MELODIES_URL}/not-an-id", json=melody_body(), headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert not supabase.calls


def test_a_full_account_answers_limit_reached(client: TestClient, supabase: FakeSupabase, headers: dict[str, str]) -> None:
    supabase.results["save_melody"] = database_error("54000", "melody_limit_reached")

    response = client.put(MELODY_URL, json=melody_body(), headers=headers)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {"code": ErrorCode.LIMIT_REACHED}


@pytest.mark.parametrize(
    ("function", "method", "url", "answer"),
    [
        ("list_melodies", "GET", MELODIES_URL, [{"id": MELODY_ID}]),
        ("list_melodies", "GET", MELODIES_URL, None),
        ("save_melody", "PUT", MELODY_URL, None),
        ("delete_melody", "DELETE", MELODY_URL, "yes"),
    ],
)
def test_an_unexpected_database_answer_is_not_passed_on(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str], function: str, method: str, url: str, answer: Any
) -> None:
    supabase.results[function] = answer

    response = client.request(method, url, json=melody_body() if method == "PUT" else None, headers=headers)

    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert response.json() == {"code": ErrorCode.STORAGE_UNAVAILABLE}
