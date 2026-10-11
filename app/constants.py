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
