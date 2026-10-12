"""Sessions: store, list, read and delete (Table 15, ADR-18), acting as the person in Supabase."""

from http import HTTPStatus
from typing import Any, Final

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.constants import API_PREFIX, MAX_RATING, MAX_SESSION_SAMPLES, SESSION_LIST_LIMIT, SESSIONS_PATH
from app.errors import ErrorCode
from tests.fakes import FAKE_VALUES
from tests.supabase_fake import FakeSupabase, SigningKey, bearer, database_error, make_token

SESSIONS_URL: Final = f"{API_PREFIX}{SESSIONS_PATH}"
SESSION_ID: Final = "017f22e2-79b0-7000-8000-000000000001"
SESSION_URL: Final = f"{SESSIONS_URL}/{SESSION_ID}"


def sample(second: int, **overrides: Any) -> dict[str, Any]:
    return {
        "second": second,
        "mean_hr": 72.5,
        "rmssd": 30,
        "sdnn": 35,
        "lf_hf_ratio": None,
        "estimated_state": "uncertain",
        "good_quality": True,
    } | overrides


def session_body(**overrides: Any) -> dict[str, Any]:
    return {
        "id": SESSION_ID,
        "started_at": "2026-10-07T10:00:00Z",
        "ended_at": "2026-10-07T10:20:00Z",
        "planned_minutes": 20,
        "goals": ["relax", "slow_breathing"],
        "listened_seconds": 1200,
        "source_kind": "simulator",
        "rating_before": 4,
        "rating_after": None,
        "samples": [sample(5, estimated_state=None), sample(10, estimated_state="low"), sample(15, estimated_state=None)],
    } | overrides


@pytest.fixture
def headers(signing_key: SigningKey) -> dict[str, str]:
    return bearer(make_token(signing_key))


def test_stores_a_session_as_the_person_and_never_with_the_service_key(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    supabase.results["save_session"] = True

    response = client.post(SESSIONS_URL, json=session_body(), headers=headers)

    assert response.status_code == HTTPStatus.CREATED
    assert response.json() == {"id": SESSION_ID, "created": True}
    (call,) = supabase.calls
    assert call.name == "save_session"
    # Row level security can only check ownership if the request carries the person's own token.
    assert call.headers["authorization"] == headers["Authorization"]
    assert call.headers["apikey"] == FAKE_VALUES["supabase_anon_key"]
    assert FAKE_VALUES["supabase_service_role_key"] not in str(call.headers)
    stored = call.arguments["p_session"]
    assert stored["id"] == SESSION_ID
    assert stored["final_state"] == "low", "the last state estimated, skipping samples without one"
    assert [item["second"] for item in stored["samples"]] == [5, 10, 15]
    assert not supabase.admin_requests


def test_sending_a_stored_session_again_answers_ok_without_creating(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    supabase.results["save_session"] = False

    response = client.post(SESSIONS_URL, json=session_body(rating_after=8), headers=headers)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"id": SESSION_ID, "created": False}


@pytest.mark.parametrize(
    ("database_answer", "status", "code"),
    [
        (database_error("P0001", "consent_required"), HTTPStatus.FORBIDDEN, ErrorCode.CONSENT_REQUIRED),
        (database_error("23505", status=HTTPStatus.CONFLICT), HTTPStatus.CONFLICT, ErrorCode.SESSION_CONFLICT),
        (database_error("42501", status=HTTPStatus.FORBIDDEN), HTTPStatus.FORBIDDEN, ErrorCode.SECOND_FACTOR_REQUIRED),
        (database_error("PGRST301", status=HTTPStatus.UNAUTHORIZED), HTTPStatus.UNAUTHORIZED, ErrorCode.UNAUTHENTICATED),
        # Anything else the database says stays inside: no message of it reaches the client.
        (database_error("P0001", "something else"), HTTPStatus.BAD_GATEWAY, ErrorCode.STORAGE_UNAVAILABLE),
        (database_error("23514", "check violated: 4.2"), HTTPStatus.BAD_GATEWAY, ErrorCode.STORAGE_UNAVAILABLE),
        (httpx2.Response(HTTPStatus.INTERNAL_SERVER_ERROR, text="boom"), HTTPStatus.BAD_GATEWAY, ErrorCode.STORAGE_UNAVAILABLE),
        ("not a boolean", HTTPStatus.BAD_GATEWAY, ErrorCode.STORAGE_UNAVAILABLE),
    ],
)
def test_database_refusals_become_error_codes(
    client: TestClient,
    supabase: FakeSupabase,
    headers: dict[str, str],
    database_answer: Any,
    status: HTTPStatus,
    code: ErrorCode,
) -> None:
    supabase.results["save_session"] = database_answer

    response = client.post(SESSIONS_URL, json=session_body(), headers=headers)

    assert response.status_code == status
    assert response.json() == {"code": code}


def test_unreachable_database_answers_storage_unavailable(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    supabase.results["list_sessions"] = []
    assert client.get(SESSIONS_URL, headers=headers).status_code == HTTPStatus.OK  # signing keys now cached
    supabase.unreachable = True

    response = client.post(SESSIONS_URL, json=session_body(), headers=headers)

    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert response.json() == {"code": ErrorCode.STORAGE_UNAVAILABLE}


@pytest.mark.parametrize(
    "invalid",
    [
        {"ended_at": "2026-10-07T09:00:00Z"},
        {"started_at": "2026-10-07T10:00:00"},
        {"id": "not-a-uuid"},
        {"planned_minutes": 0},
        {"goals": ["relax", "relax"]},
        {"goals": ["become_invincible"]},
        {"source_kind": "telepathy"},
        {"rating_after": MAX_RATING + 1},
        {"listened_seconds": -1},
        {"samples": [sample(5), sample(5)]},
        {"samples": [sample(5, mean_hr=-3)]},
        {"samples": [sample(5, estimated_state="alarming")]},
        {"samples": [sample(second) for second in range(MAX_SESSION_SAMPLES + 1)]},
        {"owner_id": "7d3e2a10-58b2-4c8a-b6f4-2a9c1e0d3f77"},
    ],
)
def test_invalid_session_is_rejected_before_reaching_the_database(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str], invalid: dict[str, Any]
) -> None:
    response = client.post(SESSIONS_URL, json=session_body(**invalid), headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    assert response.json() == {"code": ErrorCode.INVALID_REQUEST}
    assert not supabase.calls


def test_lists_the_sessions_the_database_returns_for_the_person(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    listed = {key: value for key, value in session_body().items() if key != "samples"}
    supabase.results["list_sessions"] = [listed | {"started_at": "2026-10-07T10:00:00+00:00", "ended_at": "2026-10-07T10:20:00+00:00"}]

    response = client.get(SESSIONS_URL, headers=headers)

    assert response.status_code == HTTPStatus.OK
    (returned,) = response.json()["sessions"]
    assert returned["id"] == SESSION_ID
    assert returned["goals"] == ["relax", "slow_breathing"]
    assert "samples" not in returned
    assert supabase.calls[0].arguments == {"p_limit": SESSION_LIST_LIMIT}


def test_unexpected_list_from_the_database_is_not_passed_on(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    supabase.results["list_sessions"] = [{"id": SESSION_ID, "rating_after": "very good"}]

    response = client.get(SESSIONS_URL, headers=headers)

    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert response.json() == {"code": ErrorCode.STORAGE_UNAVAILABLE}


def test_reads_one_session_with_its_samples(client: TestClient, supabase: FakeSupabase, headers: dict[str, str]) -> None:
    supabase.results["get_session"] = session_body(rating_after=8)

    response = client.get(SESSION_URL, headers=headers)

    assert response.status_code == HTTPStatus.OK
    assert response.json()["rating_after"] == 8
    assert len(response.json()["samples"]) == 3
    assert supabase.calls[0].arguments == {"p_id": SESSION_ID}


def test_session_of_another_person_answers_like_one_that_does_not_exist(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    # Row level security hides it, so the database answers null and false, as for any unknown id.
    supabase.results |= {"get_session": None, "delete_session": False}

    for response in (client.get(SESSION_URL, headers=headers), client.delete(SESSION_URL, headers=headers)):
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert response.json() == {"code": ErrorCode.NOT_FOUND}


def test_deletes_a_session(client: TestClient, supabase: FakeSupabase, headers: dict[str, str]) -> None:
    supabase.results["delete_session"] = True

    response = client.delete(SESSION_URL, headers=headers)

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert response.content == b""
    assert supabase.calls[0].arguments == {"p_id": SESSION_ID}


def test_malformed_session_id_never_reaches_the_database(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    response = client.get(f"{SESSIONS_URL}/1;drop table sessions", headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    assert not supabase.calls
