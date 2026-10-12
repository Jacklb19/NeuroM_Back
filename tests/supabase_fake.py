"""An in-memory Supabase for the tests: signing keys, database functions and account administration.

Tokens are signed here with throwaway keys generated for each test run, so no
test needs a real project or a real secret.
"""

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Final

import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from app.constants import (
    HEALTH_DATA_ASSURANCE,
    JWT_ALGORITHMS,
    JWT_AUDIENCE,
    SUPABASE_ADMIN_USERS_PATH,
    SUPABASE_JWKS_PATH,
    SUPABASE_RPC_PATH,
)
from app.security import issuer_of
from tests.fakes import FAKE_VALUES

ISSUER: Final = issuer_of(FAKE_VALUES["supabase_url"])
KEY_ID: Final = "test-key-1"
USER_ID: Final = "0b9f6c1e-4c1d-4f6e-9a39-6f1d2c3b4a5e"
OTHER_USER_ID: Final = "7d3e2a10-58b2-4c8a-b6f4-2a9c1e0d3f77"
FIRST_FACTOR_ONLY: Final = "aal1"
TOKEN_LIFETIME_SECONDS: Final = 3600


@dataclass(frozen=True)
class SigningKey:
    """A key pair like the one a Supabase project signs its tokens with."""

    key_id: str
    private: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))

    def public_jwk(self) -> dict[str, Any]:
        jwk: dict[str, Any] = jwt.algorithms.ECAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        return jwk | {"kid": self.key_id, "alg": JWT_ALGORITHMS[0], "use": "sig"}


def make_token(
    key: SigningKey,
    *,
    user_id: str = USER_ID,
    assurance: str = HEALTH_DATA_ASSURANCE,
    audience: str = JWT_AUDIENCE,
    issuer: str = ISSUER,
    expires_in: int = TOKEN_LIFETIME_SECONDS,
    key_id: str | None = None,
) -> str:
    """A token as Supabase Auth would issue it; each argument breaks one thing for the negative tests."""
    now = int(time.time())
    claims = {
        "sub": user_id,
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now + expires_in,
        "aal": assurance,
        "role": "authenticated",
    }
    return jwt.encode(claims, key.private, algorithm=JWT_ALGORITHMS[0], headers={"kid": key_id or key.key_id})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def database_error(code: str, message: str = "", status: int = HTTPStatus.BAD_REQUEST) -> httpx2.Response:
    """An error as PostgREST reports it."""
    return httpx2.Response(status, json={"code": code, "message": message, "details": None, "hint": None})


@dataclass
class FunctionCall:
    """One call to a database function, as Supabase received it."""

    name: str
    arguments: dict[str, Any]
    headers: Mapping[str, str]


class FakeSupabase:
    """Answers the requests the API sends to Supabase and remembers them."""

    def __init__(self, key: SigningKey) -> None:
        self.keys: list[SigningKey] = [key]
        self.key_downloads = 0
        self.results: dict[str, Any] = {}
        """What each database function answers: a JSON value, or a whole response for errors."""
        self.calls: list[FunctionCall] = []
        self.accounts: dict[str, dict[str, Any]] = {}
        self.admin_requests: list[httpx2.Request] = []
        self.deleted_accounts: list[str] = []
        self.unreachable = False

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.unreachable:
            raise httpx2.ConnectError("Supabase is unreachable", request=request)
        path = request.url.path
        if path == SUPABASE_JWKS_PATH:
            self.key_downloads += 1
            return httpx2.Response(HTTPStatus.OK, json={"keys": [key.public_jwk() for key in self.keys]})
        if path.startswith(f"{SUPABASE_RPC_PATH}/"):
            name = path.removeprefix(f"{SUPABASE_RPC_PATH}/")
            self.calls.append(FunctionCall(name, json.loads(request.content), request.headers))
            result = self.results[name]
            if isinstance(result, httpx2.Response):
                return result
            # Written by hand so a function that returns nothing answers `null`, as PostgREST does.
            return httpx2.Response(
                HTTPStatus.OK, content=json.dumps(result), headers={"Content-Type": "application/json"}
            )
        if path.startswith(f"{SUPABASE_ADMIN_USERS_PATH}/"):
            self.admin_requests.append(request)
            user_id = path.removeprefix(f"{SUPABASE_ADMIN_USERS_PATH}/")
            if request.method == "DELETE":
                self.deleted_accounts.append(user_id)
                return httpx2.Response(HTTPStatus.OK, json={})
            return httpx2.Response(HTTPStatus.OK, json=self.accounts.get(user_id, {"id": user_id}))
        return httpx2.Response(HTTPStatus.NOT_FOUND)
