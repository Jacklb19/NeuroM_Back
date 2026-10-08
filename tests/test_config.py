"""Configuration: read from the environment, validated, and never revealing secrets."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import (
    Settings,
    SettingsError,
    env_var_name,
    get_settings,
    is_bare_origin,
    load_settings,
)
from tests.fakes import (
    ALLOWED_ORIGIN,
    FAKE_VALUES,
    LOCAL_ORIGIN,
    SECRET_FIELDS,
    fake_environ,
)

ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"
VARIABLE_NAMES = tuple(env_var_name(field) for field in Settings.model_fields)


def env_example_names() -> set[str]:
    """Variable names declared in .env.example (comments and blank lines skipped)."""
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    return {
        line.split("=", 1)[0].strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }


def test_settings_fields_match_env_example() -> None:
    assert set(VARIABLE_NAMES) == env_example_names()


def test_loads_every_variable() -> None:
    settings = load_settings(fake_environ())

    assert str(settings.supabase_url).startswith(FAKE_VALUES["supabase_url"])
    for field in SECRET_FIELDS:
        assert getattr(settings, field).get_secret_value() == FAKE_VALUES[field]
    assert settings.allowed_origins == (ALLOWED_ORIGIN, LOCAL_ORIGIN)


def test_origin_list_tolerates_spaces_and_a_trailing_separator() -> None:
    environ = fake_environ() | {"ALLOWED_ORIGINS": f" {ALLOWED_ORIGIN} , {LOCAL_ORIGIN} ,"}

    assert load_settings(environ).allowed_origins == (ALLOWED_ORIGIN, LOCAL_ORIGIN)


def test_empty_environment_names_every_variable() -> None:
    with pytest.raises(SettingsError) as raised:
        load_settings({})

    assert raised.value.missing == VARIABLE_NAMES
    assert not raised.value.invalid
    for name in VARIABLE_NAMES:
        assert name in str(raised.value)


@pytest.mark.parametrize("name", VARIABLE_NAMES)
@pytest.mark.parametrize("absent_value", [None, "", "   "])
def test_each_absent_or_blank_variable_is_named(name: str, absent_value: str | None) -> None:
    environ = fake_environ()
    if absent_value is None:
        del environ[name]
    else:
        environ[name] = absent_value

    with pytest.raises(SettingsError) as raised:
        load_settings(environ)

    assert raised.value.missing == (name,)
    assert name in str(raised.value)


def test_invalid_values_are_named_without_echoing_them() -> None:
    bad_url = "not-a-url-but-maybe-a-secret"
    bad_origins = f"{ALLOWED_ORIGIN}/"
    environ = fake_environ() | {"SUPABASE_URL": bad_url, "ALLOWED_ORIGINS": bad_origins}

    with pytest.raises(SettingsError) as raised:
        load_settings(environ)

    error = raised.value
    assert set(error.invalid) == {"SUPABASE_URL", "ALLOWED_ORIGINS"}
    assert not error.missing
    assert bad_url not in str(error)
    assert bad_origins not in str(error)


def test_load_error_does_not_chain_the_validation_error() -> None:
    """A chained pydantic error would print the rejected (possibly secret) values."""
    with pytest.raises(SettingsError) as raised:
        load_settings(fake_environ() | {"SUPABASE_URL": "not-a-url"})

    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_secrets_never_appear_in_repr_str_or_dump() -> None:
    settings = load_settings(fake_environ())
    renderings = (repr(settings), str(settings), settings.model_dump_json())

    for field in SECRET_FIELDS:
        for rendering in renderings:
            assert FAKE_VALUES[field] not in rendering


def test_settings_are_immutable() -> None:
    settings = load_settings(fake_environ())

    with pytest.raises(ValidationError):
        settings.allowed_origins = (ALLOWED_ORIGIN,)  # type: ignore[misc]  # frozen on purpose


def test_get_settings_reads_the_environment_once(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in fake_environ().items():
        monkeypatch.setenv(name, value)

    first = get_settings()
    monkeypatch.setenv("ALLOWED_ORIGINS", LOCAL_ORIGIN)

    assert get_settings() is first


@pytest.mark.parametrize(
    "origin",
    [
        "https://neuromelody.example.app",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://[::1]:5173",
    ],
)
def test_accepts_bare_origins(origin: str) -> None:
    assert is_bare_origin(origin)


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.vercel.app",
        "https://app.example.com/",
        "https://app.example.com/path",
        "https://app.example.com?query=1",
        "https://app.example.com#fragment",
        "https://user@app.example.com",
        "https://App.Example.com",
        "https://app.example.com:443",
        "http://app.example.com:80",
        "https://app.example.com:99999",
        "ftp://app.example.com",
        "app.example.com",
        "null",
    ],
)
def test_rejects_anything_but_a_bare_origin(origin: str) -> None:
    assert not is_bare_origin(origin)
    with pytest.raises(ValidationError):
        Settings.model_validate(dict(FAKE_VALUES) | {"allowed_origins": origin})
