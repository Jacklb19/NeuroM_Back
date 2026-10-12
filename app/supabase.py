"""Calls to Supabase: signing keys, database functions and account administration.

Everything the API stores or reads goes through here, with one rule (ADR-19):
data requests carry the person's own token, so row level security checks
ownership and the second factor again in the database. The service role key,
which bypasses those checks, is sent only to the account administration
endpoints.
"""

import logging
from http import HTTPStatus
from typing import Any, Final
from uuid import UUID

import httpx2

from app.config import Settings
from app.constants import (
    BEARER_SCHEME,
    SUPABASE_ADMIN_USERS_PATH,
    SUPABASE_JWKS_PATH,
    SUPABASE_RPC_PATH,
    SUPABASE_TIMEOUT_SECONDS,
    VERIFIED_FACTOR_STATUS,
)
from app.errors import ApiError, ErrorCode

logger: Final = logging.getLogger(__name__)

_API_KEY_HEADER: Final = "apikey"
_AUTHORIZATION_HEADER: Final = "Authorization"

_CONSENT_REQUIRED_MESSAGE: Final = "consent_required"
"""Message ``save_session`` raises when the person has not consented to syncing."""

_DATABASE_ERRORS: Final = {
    # Raised by save_session itself; told apart from other P0001 by its message.
    "P0001": (HTTPStatus.FORBIDDEN, ErrorCode.CONSENT_REQUIRED),
    # unique_violation: the id already belongs to a session the person cannot see.
    "23505": (HTTPStatus.CONFLICT, ErrorCode.SESSION_CONFLICT),
    # insufficient_privilege: row level security refused the request, which for
    # the owner of the data can only mean a session without the second factor.
    "42501": (HTTPStatus.FORBIDDEN, ErrorCode.SECOND_FACTOR_REQUIRED),
}
"""Answers for the PostgreSQL error codes the database functions can produce."""

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
"""A decoded JSON body. ``Any`` because each caller validates it with its own model."""


def _unavailable() -> ApiError:
    return ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.STORAGE_UNAVAILABLE)


class SupabaseGateway:
    """HTTP access to one Supabase project.

    Built once per application and reused, so connections are kept between
    requests. Tests pass a ``transport`` that answers in memory.
    """

    def __init__(self, settings: Settings, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._anon_key = settings.supabase_anon_key
        self._service_key = settings.supabase_service_role_key
        self._client = httpx2.AsyncClient(
            base_url=str(settings.supabase_url).rstrip("/"),
            timeout=SUPABASE_TIMEOUT_SECONDS,
            transport=transport,
        )

    async def _send(self, method: str, path: str, headers: dict[str, str], body: JsonValue = None) -> httpx2.Response:
        """Send one request; a network failure or a timeout is reported as unavailable storage."""
        try:
            return await self._client.request(method, path, headers=headers, json=body)
        except httpx2.HTTPError as error:
            # Only the kind of failure is logged: the request carries tokens and keys.
            logger.warning("Supabase request failed: %s", type(error).__name__)
            raise _unavailable() from None

    async def fetch_signing_keys(self) -> dict[str, Any]:
        """The project's public signing keys (JWKS). ``Any`` mirrors the JWKS format."""
        response = await self._send("GET", SUPABASE_JWKS_PATH, {_API_KEY_HEADER: self._anon_key})
        if response.status_code != HTTPStatus.OK:
            logger.warning("Supabase signing keys answered %s", response.status_code)
            raise _unavailable()
        keys = _json(response)
        if not isinstance(keys, dict):
            raise _unavailable()
        return keys

    async def call_function(self, name: str, arguments: dict[str, Any], token: str) -> JsonValue:
        """Run a database function as the person who owns ``token``.

        ``Any`` in ``arguments`` mirrors JSON: each endpoint builds them from a
        validated model. Database refusals become the API's own error codes.
        """
        headers = {_API_KEY_HEADER: self._anon_key, _AUTHORIZATION_HEADER: f"{BEARER_SCHEME} {token}"}
        response = await self._send("POST", f"{SUPABASE_RPC_PATH}/{name}", headers, arguments)
        if response.status_code == HTTPStatus.OK:
            return _json(response)
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            # Supabase no longer accepts the token (it expired on the way, or was revoked).
            raise ApiError(HTTPStatus.UNAUTHORIZED, ErrorCode.UNAUTHENTICATED)
        raise _database_error(name, response)

    async def has_verified_second_factor(self, user_id: UUID) -> bool:
        """Whether the account has a second factor the person finished enrolling."""
        response = await self._send("GET", f"{SUPABASE_ADMIN_USERS_PATH}/{user_id}", self._admin_headers())
        if response.status_code != HTTPStatus.OK:
            logger.warning("Supabase account lookup answered %s", response.status_code)
            raise _unavailable()
        account = _json(response)
        factors = account.get("factors") if isinstance(account, dict) else None
        if not isinstance(factors, list):
            return False
        return any(isinstance(factor, dict) and factor.get("status") == VERIFIED_FACTOR_STATUS for factor in factors)

    async def delete_account(self, user_id: UUID) -> None:
        """Delete the account; its profile, plans, sessions and metrics fall in cascade."""
        response = await self._send("DELETE", f"{SUPABASE_ADMIN_USERS_PATH}/{user_id}", self._admin_headers())
        if response.status_code not in (HTTPStatus.OK, HTTPStatus.NO_CONTENT):
            logger.warning("Supabase account deletion answered %s", response.status_code)
            raise _unavailable()

    def _admin_headers(self) -> dict[str, str]:
        key = self._service_key.get_secret_value()
        return {_API_KEY_HEADER: key, _AUTHORIZATION_HEADER: f"{BEARER_SCHEME} {key}"}


def _json(response: httpx2.Response) -> JsonValue:
    try:
        body: JsonValue = response.json()
    except ValueError:
        raise _unavailable() from None
    return body


def _database_error(function: str, response: httpx2.Response) -> ApiError:
    """Translate a refusal of the database into the API's error contract."""
    try:
        detail = response.json()
    except ValueError:
        detail = None
    code = detail.get("code") if isinstance(detail, dict) else None
    known = _DATABASE_ERRORS.get(code) if isinstance(code, str) else None
    if known is not None and (code != "P0001" or detail.get("message") == _CONSENT_REQUIRED_MESSAGE):
        return ApiError(*known)
    # The message of the database stays out of the log: it can quote stored values.
    logger.warning("Database function %s failed: status %s, code %s", function, response.status_code, code)
    return _unavailable()
