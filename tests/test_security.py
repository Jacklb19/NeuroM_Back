"""Identity: who may call the protected endpoints (ADR-10, ADR-19).

Negative cases first: no token, a token that does not verify, a session
without the second factor and an origin CORS does not allow.
"""

import asyncio
from http import HTTPStatus
from typing import Any, Final

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.constants import (
    ACCOUNT_PATH,
    API_PREFIX,
    JWKS_CACHE_SECONDS,
    JWKS_MIN_REFRESH_SECONDS,
    JWT_AUDIENCE,
    SESSIONS_PATH,
)
from app.errors import ApiError, ErrorCode
from app.security import TokenVerifier
from app.supabase import SupabaseGateway
from tests.fakes import ALLOWED_ORIGIN, DISALLOWED_ORIGIN
from tests.supabase_fake import (
    FIRST_FACTOR_ONLY,
    ISSUER,
    KEY_ID,
    USER_ID,
    FakeSupabase,
    SigningKey,
    bearer,
    make_token,
)

SESSIONS_URL: Final = f"{API_PREFIX}{SESSIONS_PATH}"
SESSION_URL: Final = f"{SESSIONS_URL}/017f22e2-79b0-7000-8000-000000000001"
ACCOUNT_URL: Final = f"{API_PREFIX}{ACCOUNT_PATH}"

HEALTH_DATA_ENDPOINTS: Final = (
    ("POST", SESSIONS_URL),
    ("GET", SESSIONS_URL),
    ("GET", SESSION_URL),
    ("DELETE", SESSION_URL),
)
PROTECTED_ENDPOINTS: Final = (*HEALTH_DATA_ENDPOINTS, ("DELETE", ACCOUNT_URL))


def assert_error(response: Any, status: HTTPStatus, code: ErrorCode) -> None:
    assert response.status_code == status
    assert response.json() == {"code": code}


@pytest.mark.parametrize(("method", "url"), PROTECTED_ENDPOINTS)
def test_request_without_a_token_is_rejected(client: TestClient, supabase: FakeSupabase, method: str, url: str) -> None:
    assert_error(client.request(method, url), HTTPStatus.UNAUTHORIZED, ErrorCode.UNAUTHENTICATED)
    assert not supabase.calls and not supabase.admin_requests


@pytest.mark.parametrize("header", ["", "Bearer", "Bearer   ", "Basic abc", "not-a-token", "Bearer not.a.token"])
def test_malformed_authorization_header_is_rejected(client: TestClient, header: str) -> None:
    response = client.get(SESSIONS_URL, headers={"Authorization": header})

    assert_error(response, HTTPStatus.UNAUTHORIZED, ErrorCode.UNAUTHENTICATED)


def forged_tokens(key: SigningKey) -> dict[str, str]:
    """Tokens that look right and must not verify, by what is wrong with each."""
    public_pem = key.public_jwk()
    return {
        "signed by another key": make_token(SigningKey(KEY_ID)),
        "expired": make_token(key, expires_in=-3600),
        "issued for another audience": make_token(key, audience="anon"),
        "issued by another project": make_token(key, issuer="https://other.supabase.example.test/auth/v1"),
        "naming an unknown key": make_token(key, key_id="unknown-key"),
        "without a user id": make_token(key, user_id="not-a-uuid"),
        "unsigned": jwt.encode({"sub": USER_ID, "aud": JWT_AUDIENCE, "iss": ISSUER}, "", algorithm="none", headers={"kid": KEY_ID}),
        # The classic confusion attack: the public key replayed as a shared secret.
        "signed with the public key as a secret": jwt.encode(
            {"sub": USER_ID, "aud": JWT_AUDIENCE, "iss": ISSUER, "exp": 9_999_999_999, "aal": "aal2"},
            str(public_pem),
            algorithm="HS256",
            headers={"kid": KEY_ID},
        ),
    }


@pytest.mark.parametrize(
    "flaw",
    [
        "signed by another key",
        "expired",
        "issued for another audience",
        "issued by another project",
        "naming an unknown key",
        "without a user id",
        "unsigned",
        "signed with the public key as a secret",
    ],
)
def test_token_that_does_not_verify_is_rejected(
    client: TestClient, supabase: FakeSupabase, signing_key: SigningKey, flaw: str
) -> None:
    response = client.get(SESSIONS_URL, headers=bearer(forged_tokens(signing_key)[flaw]))

    assert_error(response, HTTPStatus.UNAUTHORIZED, ErrorCode.UNAUTHENTICATED)
    assert not supabase.calls


@pytest.mark.parametrize(("method", "url"), HEALTH_DATA_ENDPOINTS)
def test_health_data_needs_the_second_factor(
    client: TestClient, supabase: FakeSupabase, signing_key: SigningKey, method: str, url: str
) -> None:
    token = make_token(signing_key, assurance=FIRST_FACTOR_ONLY)

    response = client.request(method, url, headers=bearer(token))

    assert_error(response, HTTPStatus.FORBIDDEN, ErrorCode.SECOND_FACTOR_REQUIRED)
    assert not supabase.calls


def test_preflight_for_sessions_follows_the_allowed_origins(client: TestClient) -> None:
    def preflight(origin: str) -> Any:
        return client.options(
            SESSION_URL,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "Authorization",
            },
        )

    assert preflight(ALLOWED_ORIGIN).status_code == HTTPStatus.OK
    rejected = preflight(DISALLOWED_ORIGIN)
    assert rejected.status_code == HTTPStatus.BAD_REQUEST
    assert "access-control-allow-origin" not in rejected.headers


def test_signing_keys_are_downloaded_once_and_reused(
    client: TestClient, supabase: FakeSupabase, signing_key: SigningKey
) -> None:
    supabase.results["list_sessions"] = []
    headers = bearer(make_token(signing_key))

    for _ in range(3):
        assert client.get(SESSIONS_URL, headers=headers).status_code == HTTPStatus.OK

    assert supabase.key_downloads == 1


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def verifier(settings: Settings, supabase: FakeSupabase, clock: Clock) -> TokenVerifier:
    return TokenVerifier(SupabaseGateway(settings, supabase.transport), ISSUER, clock)


def verifies(verifier: TokenVerifier, token: str) -> bool:
    try:
        asyncio.run(verifier.verify(token))
    except ApiError:
        return False
    return True


def test_rotated_key_is_picked_up_without_flooding_supabase(
    verifier: TokenVerifier, supabase: FakeSupabase, signing_key: SigningKey, clock: Clock
) -> None:
    rotated = SigningKey("test-key-2")
    assert verifies(verifier, make_token(signing_key))

    # Tokens naming a key Supabase does not have yet: asked once, then not again for a while.
    assert not verifies(verifier, make_token(rotated))
    assert not verifies(verifier, make_token(rotated))
    assert supabase.key_downloads == 1

    supabase.keys.append(rotated)
    clock.now += JWKS_MIN_REFRESH_SECONDS
    assert verifies(verifier, make_token(rotated))
    assert supabase.key_downloads == 2


def test_retired_key_stops_verifying_once_the_cache_expires(
    verifier: TokenVerifier, supabase: FakeSupabase, signing_key: SigningKey, clock: Clock
) -> None:
    token = make_token(signing_key)
    assert verifies(verifier, token)

    supabase.keys.clear()
    assert verifies(verifier, token), "still cached"
    clock.now += JWKS_CACHE_SECONDS
    assert not verifies(verifier, token)


def test_unreachable_supabase_is_not_reported_as_a_bad_token(
    client: TestClient, supabase: FakeSupabase, signing_key: SigningKey
) -> None:
    supabase.unreachable = True

    response = client.get(SESSIONS_URL, headers=bearer(make_token(signing_key)))

    assert_error(response, HTTPStatus.BAD_GATEWAY, ErrorCode.STORAGE_UNAVAILABLE)
