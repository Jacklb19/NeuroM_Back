"""Fake configuration shared by the tests: obviously non-secret values, never real ones."""

from collections.abc import Mapping
from typing import Final

from app.config import env_var_name
from app.constants import ALLOWED_ORIGINS_SEPARATOR

ALLOWED_ORIGIN: Final = "https://frontend.example.test"
LOCAL_ORIGIN: Final = "http://localhost:5173"
DISALLOWED_ORIGIN: Final = "https://attacker.example.test"
PREVIEW_ORIGIN: Final = "https://neuromelody-a1b2c3d4e-team-example.vercel.app"
LOOKALIKE_PREVIEW_ORIGINS: Final = (
    "https://neuromelody-a1b2c3d4e-other-team-example.vercel.app",
    "https://neuromelody-git-main-team-example.vercel.app",
    "https://neuromelody-a1b2c3d4e-team-example.vercel.app.attacker.test",
)
"""Preview-shaped origins of another team, a branch alias and a suffix trick."""

FAKE_VALUES: Final[Mapping[str, str]] = {
    "supabase_url": "https://project.supabase.example.test",
    "supabase_service_role_key": "fake-service-role-key-for-tests",
    "groq_api_key": "fake-groq-api-key-for-tests",
    "allowed_origins": ALLOWED_ORIGINS_SEPARATOR.join((ALLOWED_ORIGIN, LOCAL_ORIGIN)),
    "vercel_preview_project": "neuromelody",
    "vercel_preview_team": "team-example",
}
"""Raw values keyed by ``Settings`` field, as they would arrive from the environment."""

SECRET_FIELDS: Final = ("supabase_service_role_key", "groq_api_key")
"""Fields whose values must never appear in output."""


def fake_environ() -> dict[str, str]:
    """A complete environment with the fake values under the real variable names."""
    return {env_var_name(field): value for field, value in FAKE_VALUES.items()}
