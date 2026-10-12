"""Named values the API is built from.

Every value a maintainer may want to change lives here, documented, instead of
inside the module that uses it (ADR-25). Secrets and per-environment values do
not belong here: they come from environment variables (see ``app.config``).
"""

from typing import Final

API_TITLE: Final = "NeuroMelody API"
"""Name shown in the OpenAPI document."""

API_VERSION: Final = "0.1.0"
"""Released version of the API. Must match ``version`` in pyproject.toml (a test enforces it)."""

API_PREFIX: Final = "/v1"
"""Prefix of every endpoint (Table 15 of the specification uses ``/v1``)."""

HEALTH_PATH: Final = "/health"
"""Public liveness endpoint, relative to ``API_PREFIX``."""

SESSIONS_PATH: Final = "/sessions"
"""Sessions of the signed-in person (Table 15 and ADR-18), relative to ``API_PREFIX``."""

ACCOUNT_PATH: Final = "/account"
"""The signed-in person's account (ADR-18: ``DELETE /v1/account``)."""

BEARER_SCHEME: Final = "Bearer"
"""Scheme of the ``Authorization`` header that carries the Supabase token (ADR-19)."""

JWT_ALGORITHMS: Final = ("ES256",)
"""Signature algorithms accepted for a token: the asymmetric one the Supabase project
signs with. Symmetric algorithms are left out on purpose, so a public key can never
be replayed as a shared secret."""

JWT_AUDIENCE: Final = "authenticated"
"""Audience Supabase gives the tokens of signed-in people."""

JWT_LEEWAY_SECONDS: Final = 10
"""Clock difference tolerated between Supabase and this server when checking expiry."""

HEALTH_DATA_ASSURANCE: Final = "aal2"
"""Assurance level a session needs to read or write health data: the second factor (ADR-10)."""

VERIFIED_FACTOR_STATUS: Final = "verified"
"""Status Supabase gives a second factor the person finished enrolling."""

SUPABASE_AUTH_PATH: Final = "/auth/v1"
"""Path of Supabase Auth under the project URL; it is also the issuer of its tokens."""

SUPABASE_JWKS_PATH: Final = "/auth/v1/.well-known/jwks.json"
"""Public keys the project signs its tokens with."""

SUPABASE_RPC_PATH: Final = "/rest/v1/rpc"
"""Database functions exposed by Supabase; the name of the function follows."""

SUPABASE_ADMIN_USERS_PATH: Final = "/auth/v1/admin/users"
"""Account administration, reachable only with the service role key."""

SUPABASE_TIMEOUT_SECONDS: Final = 6.0
"""Longest wait for one answer from Supabase. Below the function's ``maxDuration``
(vercel.json), so a hung call ends as an error code instead of a killed function."""

JWKS_CACHE_SECONDS: Final = 600
"""How long the signing keys are reused before asking Supabase again."""

JWKS_MIN_REFRESH_SECONDS: Final = 30
"""Shortest time between two key downloads, so tokens naming unknown keys cannot
make the API flood Supabase with requests."""

SESSION_LIST_LIMIT: Final = 500
"""Most sessions returned by ``GET /v1/sessions``, newest first."""

MAX_SESSION_SAMPLES: Final = 2880
"""Most samples accepted for one session: four hours at one every five seconds,
well above the longest plan and far below the 4.5 MB body Vercel accepts."""

MAX_PLANNED_MINUTES: Final = 240
"""Longest plan accepted, in minutes; the app offers up to 60."""

MAX_LISTENED_SECONDS: Final = 24 * 60 * 60
"""Longest listening time accepted for one session: a day."""

MIN_RATING: Final = 0
MAX_RATING: Final = 10
"""Bounds of the optional self-rating before and after a session (ADR-23)."""

ALLOWED_ORIGINS_SEPARATOR: Final = ","
"""Separator between origins in the ``ALLOWED_ORIGINS`` environment variable."""

VERCEL_PREVIEW_SLUG_PATTERN: Final = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
"""Shape of a Vercel project name or team slug, as it appears in a preview URL."""

VERCEL_PREVIEW_HASH_PATTERN: Final = r"[a-z0-9]{9}"
"""Deployment hash Vercel puts between the project and the team in a preview URL."""

VERCEL_PREVIEW_DOMAIN: Final = "vercel.app"
"""Domain of Vercel's preview deployments."""

ALLOWED_ORIGIN_SCHEMES: Final = frozenset({"http", "https"})
"""Schemes accepted for a CORS origin: https in production, http for local development."""

CORS_ALLOWED_METHODS: Final = ("GET", "POST", "DELETE")
"""Methods the frontend may use cross-origin: Table 15 plus the DELETE endpoints of ADR-18."""

CORS_ALLOWED_HEADERS: Final = ("Authorization", "Content-Type")
"""Request headers the frontend may send: the Supabase JWT (ADR-19) and JSON bodies."""

CORS_PREFLIGHT_MAX_AGE_SECONDS: Final = 600
"""How long browsers may cache a preflight answer. Ten minutes keeps preflights rare
while a change to the allowed origins still reaches every browser quickly."""
