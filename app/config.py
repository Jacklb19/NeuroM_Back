"""Runtime configuration, read from environment variables and validated at startup.

The API refuses to start with an incomplete or invalid configuration: failing
while the function boots puts the problem in the deployment logs instead of
behind a confusing error on the first real request (ADR-25). Error messages
only ever name variables, because their values may be secrets.
"""

import os
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Final
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    ValidationError,
    field_validator,
)

from app.constants import ALLOWED_ORIGIN_SCHEMES, ALLOWED_ORIGINS_SEPARATOR

_DEFAULT_PORTS: Final = {"http": 80, "https": 443}
"""Ports a browser omits from the Origin header, so an origin must omit them too."""


class Settings(BaseModel):
    """Validated configuration of one API instance.

    Each field is read from the environment variable with the same name in
    upper case (see ``env_var_name``), and ``.env.example`` lists exactly those
    names. Secrets are ``SecretStr`` so they never show up in ``repr``, logs or
    tracebacks; read them with ``get_secret_value()`` only where they are sent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    supabase_url: HttpUrl
    supabase_service_role_key: SecretStr = Field(min_length=1)
    groq_api_key: SecretStr = Field(min_length=1)
    allowed_origins: tuple[str, ...] = Field(min_length=1)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origin_list(cls, value: object) -> object:
        """Accept the comma-separated form used by ``ALLOWED_ORIGINS``.

        Blank entries are dropped so a trailing separator is harmless.
        """
        if isinstance(value, str):
            entries = (entry.strip() for entry in value.split(ALLOWED_ORIGINS_SEPARATOR))
            return tuple(entry for entry in entries if entry)
        return value

    @field_validator("allowed_origins")
    @classmethod
    def _require_bare_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        """Reject anything that is not exactly an origin a browser would send.

        The message names the entry by position, never by value, to keep
        error messages free of configuration values.
        """
        for position, origin in enumerate(origins, start=1):
            if not is_bare_origin(origin):
                raise ValueError(
                    f"entry {position} must be a bare origin such as https://app.example.com "
                    "(http or https, lowercase, no wildcard, path, trailing slash or default port)"
                )
        return origins


class SettingsError(RuntimeError):
    """The environment does not hold a complete, valid configuration.

    ``missing`` and ``invalid`` hold variable names (and, for ``invalid``, the
    reason), so the whole problem can be fixed in one pass.
    """

    def __init__(self, missing: Sequence[str], invalid: Mapping[str, str]) -> None:
        self.missing: tuple[str, ...] = tuple(missing)
        self.invalid: dict[str, str] = dict(invalid)
        problems: list[str] = []
        if self.missing:
            problems.append(f"missing: {', '.join(self.missing)}")
        if self.invalid:
            reasons = "; ".join(f"{name} ({reason})" for name, reason in self.invalid.items())
            problems.append(f"invalid: {reasons}")
        super().__init__(
            f"Invalid API configuration, {' | '.join(problems)}. "
            "See .env.example for the expected variables."
        )


def env_var_name(field_name: str) -> str:
    """Name of the environment variable that feeds a ``Settings`` field."""
    return field_name.upper()


def is_bare_origin(value: str) -> bool:
    """Whether ``value`` is exactly what a browser sends in the ``Origin`` header.

    CORS matching is an exact string comparison, so a trailing slash, a path,
    upper case or an explicit default port would make an origin silently never
    match; wildcards are rejected because only known frontends may call the API.
    """
    if "*" in value:
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    host = parts.hostname
    if parts.scheme not in ALLOWED_ORIGIN_SCHEMES or not host:
        return False
    if ":" in host:
        # urlsplit drops the brackets around an IPv6 literal; the Origin header keeps them.
        host = f"[{host}]"
    canonical = f"{parts.scheme}://{host}"
    if port is not None and port != _DEFAULT_PORTS.get(parts.scheme):
        canonical = f"{canonical}:{port}"
    return value == canonical


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    """Build ``Settings`` from ``environ``, or raise ``SettingsError`` naming every problem.

    Blank variables count as missing. The pydantic error is not chained
    (``from None``) because its text includes the rejected values, which may
    be secrets.
    """
    values: dict[str, str] = {}
    missing: list[str] = []
    for field_name in Settings.model_fields:
        name = env_var_name(field_name)
        value = environ.get(name, "").strip()
        if value:
            values[field_name] = value
        else:
            missing.append(name)
    try:
        return Settings.model_validate(values)
    except ValidationError as error:
        raise SettingsError(missing, _invalid_reasons(error, skip=missing)) from None


def _invalid_reasons(error: ValidationError, skip: Sequence[str]) -> dict[str, str]:
    """Map each rejected variable to pydantic's reason, leaving out the input values."""
    reasons: dict[str, list[str]] = {}
    for detail in error.errors(include_input=False, include_url=False):
        name = env_var_name(str(detail["loc"][0]))
        if name not in skip:
            reasons.setdefault(name, []).append(detail["msg"])
    return {name: "; ".join(messages) for name, messages in reasons.items()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings from the process environment, parsed once per process.

    Route handlers receive it through ``Depends(get_settings)``, which lets
    tests swap it with ``app.dependency_overrides`` instead of real secrets.
    """
    return load_settings(os.environ)
