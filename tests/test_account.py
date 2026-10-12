"""DELETE /v1/account (ADR-18): only the caller's own account, and with the second factor when it has one."""

from http import HTTPStatus
from typing import Final

from fastapi.testclient import TestClient

from app.constants import ACCOUNT_PATH, API_PREFIX, VERIFIED_FACTOR_STATUS
from app.errors import ErrorCode
from tests.fakes import FAKE_VALUES
from tests.supabase_fake import FIRST_FACTOR_ONLY, OTHER_USER_ID, USER_ID, FakeSupabase, SigningKey, bearer, make_token

ACCOUNT_URL: Final = f"{API_PREFIX}{ACCOUNT_PATH}"
SERVICE_KEY: Final = FAKE_VALUES["supabase_service_role_key"]


def test_deletes_the_account_of_the_token_with_the_service_key(
    client: TestClient, supabase: FakeSupabase, signing_key: SigningKey
) -> None:
    response = client.delete(ACCOUNT_URL, headers=bearer(make_token(signing_key)))

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert supabase.deleted_accounts == [USER_ID]
    (request,) = supabase.admin_requests
    assert request.headers["apikey"] == SERVICE_KEY
    assert request.headers["authorization"] == f"Bearer {SERVICE_KEY}"
    assert SERVICE_KEY not in response.text


def test_account_without_a_second_factor_can_be_deleted_from_a_first_factor_session(
    client: TestClient, supabase: FakeSupabase, signing_key: SigningKey
) -> None:
    supabase.accounts[USER_ID] = {"id": USER_ID, "factors": [{"status": "unverified"}]}

    response = client.delete(ACCOUNT_URL, headers=bearer(make_token(signing_key, assurance=FIRST_FACTOR_ONLY)))

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert supabase.deleted_accounts == [USER_ID]


def test_account_with_a_second_factor_is_not_deleted_without_passing_it(
    client: TestClient, supabase: FakeSupabase, signing_key: SigningKey
) -> None:
    supabase.accounts[USER_ID] = {"id": USER_ID, "factors": [{"status": VERIFIED_FACTOR_STATUS}]}

    response = client.delete(ACCOUNT_URL, headers=bearer(make_token(signing_key, assurance=FIRST_FACTOR_ONLY)))

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json() == {"code": ErrorCode.SECOND_FACTOR_REQUIRED}
    assert supabase.deleted_accounts == []


def test_request_cannot_name_another_account(client: TestClient, supabase: FakeSupabase, signing_key: SigningKey) -> None:
    headers = bearer(make_token(signing_key))

    by_path = client.delete(f"{ACCOUNT_URL}/{OTHER_USER_ID}", headers=headers)
    by_query = client.delete(ACCOUNT_URL, params={"user_id": OTHER_USER_ID}, headers=headers)

    assert by_path.status_code == HTTPStatus.NOT_FOUND
    assert by_query.status_code == HTTPStatus.NO_CONTENT
    assert supabase.deleted_accounts == [USER_ID], "the query is ignored: only the token says whose account it is"


def test_unreachable_supabase_deletes_nothing_and_says_so(
    client: TestClient, supabase: FakeSupabase, signing_key: SigningKey
) -> None:
    headers = bearer(make_token(signing_key))
    assert client.delete(f"{ACCOUNT_URL}/x", headers=headers).status_code == HTTPStatus.NOT_FOUND
    supabase.unreachable = True

    response = client.delete(ACCOUNT_URL, headers=headers)

    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert response.json() == {"code": ErrorCode.STORAGE_UNAVAILABLE}
    assert supabase.deleted_accounts == []
