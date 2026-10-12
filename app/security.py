"""Identity of the caller: the Supabase token in the ``Authorization`` header (ADR-19).

The token is verified here, with the project's public keys, before any
endpoint runs. Health data also needs the second factor (ADR-10): the token
must say the session reached ``aal2``. Supabase checks both again in the
database, because every data request is sent with this same token.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Any, Final
from uuid import UUID

import jwt
from fastapi import Depends, Request

from app.constants import (
    BEARER_SCHEME,
    HEALTH_DATA_ASSURANCE,
    JWKS_CACHE_SECONDS,
    JWKS_MIN_REFRESH_SECONDS,
    JWT_ALGORITHMS,
    JWT_AUDIENCE,
    JWT_LEEWAY_SECONDS,
    SUPABASE_AUTH_PATH,
)
from app.errors import ApiError, ErrorCode
from app.supabase import SupabaseGateway

_REQUIRED_CLAIMS: Final = ("exp", "sub", "aud", "iss")
_ASSURANCE_CLAIM: Final = "aal"
_KEY_ID_HEADER: Final = "kid"


@dataclass(frozen=True)
class Identity:
    """Who is calling, as stated by a verified token."""

    user_id: UUID
    assurance: str
    """Authenticator assurance level of the session: ``aal1`` or ``aal2``."""
    token: str
    """The token itself, to act as this person in Supabase and nowhere else."""

    @property
    def has_second_factor(self) -> bool:
        return self.assurance == HEALTH_DATA_ASSURANCE


def _unauthenticated() -> ApiError:
    return ApiError(HTTPStatus.UNAUTHORIZED, ErrorCode.UNAUTHENTICATED)


class TokenVerifier:
    """Verifies Supabase tokens against the project's signing keys.

    The keys are downloaded once and reused; they are asked for again when
    they are old or when a token names a key that is not among them (Supabase
    rotated its keys), but never more often than ``JWKS_MIN_REFRESH_SECONDS``.
    """

    def __init__(self, gateway: SupabaseGateway, issuer: str, clock: Callable[[], float] = time.monotonic) -> None:
        self._gateway = gateway
        self._issuer = issuer
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None

    async def verify(self, token: str) -> Identity:
        """Return who the token belongs to, or raise ``unauthenticated``.

        Every failure gives the same answer on purpose: saying why a token
        was refused would help whoever is forging one.
        """
        try:
            header = jwt.get_unverified_header(token)
            key = await self._signing_key(header.get(_KEY_ID_HEADER))
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=list(JWT_ALGORITHMS),
                audience=JWT_AUDIENCE,
                issuer=self._issuer,
                leeway=JWT_LEEWAY_SECONDS,
                options={"require": list(_REQUIRED_CLAIMS)},
            )
            user_id = UUID(str(claims["sub"]))
        except (jwt.PyJWTError, ValueError):
            raise _unauthenticated() from None
        return Identity(user_id=user_id, assurance=str(claims.get(_ASSURANCE_CLAIM, "")), token=token)

    async def _signing_key(self, key_id: object) -> jwt.PyJWK:
        if not isinstance(key_id, str):
            raise jwt.InvalidTokenError("the token names no signing key")
        age = None if self._fetched_at is None else self._clock() - self._fetched_at
        stale = age is None or age >= JWKS_CACHE_SECONDS
        may_refresh = age is None or age >= JWKS_MIN_REFRESH_SECONDS
        if stale or (key_id not in self._keys and may_refresh):
            await self._refresh()
        key = self._keys.get(key_id)
        if key is None:
            raise jwt.InvalidTokenError("the token names an unknown signing key")
        return key

    async def _refresh(self) -> None:
        document = await self._gateway.fetch_signing_keys()
        try:
            key_set = jwt.PyJWKSet.from_dict(document)
        except jwt.PyJWTError:
            # A project without asymmetric keys publishes an empty set: no token can be verified.
            key_set = None
        self._keys = {} if key_set is None else {key.key_id: key for key in key_set.keys if key.key_id is not None}
        self._fetched_at = self._clock()


def issuer_of(supabase_url: str) -> str:
    """The ``iss`` claim Supabase Auth puts in the tokens of a project."""
    return f"{supabase_url.rstrip('/')}{SUPABASE_AUTH_PATH}"


def _bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != BEARER_SCHEME.lower() or not token.strip():
        raise _unauthenticated()
    return token.strip()


async def current_identity(request: Request) -> Identity:
    """Dependency: the verified caller. Answers 401 ``unauthenticated`` otherwise."""
    verifier: TokenVerifier = request.app.state.token_verifier
    return await verifier.verify(_bearer_token(request))


async def health_data_identity(identity: Annotated[Identity, Depends(current_identity)]) -> Identity:
    """Dependency: a verified caller whose session passed the second factor (ADR-10).

    Answers 403 ``second_factor_required`` when it did not.
    """
    if not identity.has_second_factor:
        raise ApiError(HTTPStatus.FORBIDDEN, ErrorCode.SECOND_FACTOR_REQUIRED)
    return identity


def gateway_of(request: Request) -> SupabaseGateway:
    """Dependency: the application's Supabase gateway."""
    gateway: SupabaseGateway = request.app.state.supabase
    return gateway
