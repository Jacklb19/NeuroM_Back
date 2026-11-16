"""Static checks on the Supabase migrations.

Row level security is the second barrier that keeps one user from reading
another user's health data (ADR-10), so every table must enable it.
"""

import re
from pathlib import Path

import pytest

from app.constants import (
    MAX_LISTENED_SECONDS,
    MAX_MELODIES_PER_ACCOUNT,
    MAX_MELODY_POSITION_SECONDS,
    MAX_PLANNED_MINUTES,
    MAX_PULSE_BPM,
    MAX_RECORDINGS_PER_ACCOUNT,
    MAX_RR_MS,
    MAX_SESSION_SAMPLES,
    MAX_SESSIONS_PER_ACCOUNT,
    MAX_STAGE_BEATS,
    MAX_STAGE_SECONDS,
    MAX_VARIATION_MS,
    MELODY_NAME_MAX_CHARS,
    RECORDING_STAGES,
    SUMMARY_MAX_CHARS,
)
from app.melodies import Ambience, Instrument, MusicLevel, SoundProfile
from app.schemas import Goal

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
LIMITS_MIGRATION = MIGRATIONS_DIR / "20261102010000_database_limits.sql"
ACCOUNT_LIMITS_MIGRATION = MIGRATIONS_DIR / "20261114000000_account_storage_limits.sql"
MELODIES_MIGRATION = MIGRATIONS_DIR / "20261117000000_melodies.sql"

CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?(\w+)\s*\(", re.IGNORECASE
)
ENABLE_RLS = re.compile(
    r"alter\s+table\s+(?:public\.)?(\w+)\s+enable\s+row\s+level\s+security\s*;", re.IGNORECASE
)


def read_migrations() -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]


def test_there_is_at_least_one_migration() -> None:
    assert read_migrations(), f"No .sql files in {MIGRATIONS_DIR}"


def test_every_table_enables_row_level_security() -> None:
    sql = "\n".join(read_migrations())
    created = {name.lower() for name in CREATE_TABLE.findall(sql)}
    protected = {name.lower() for name in ENABLE_RLS.findall(sql)}

    assert created, "No CREATE TABLE statements found"
    assert created <= protected, f"Tables without RLS: {sorted(created - protected)}"


def test_detects_a_table_without_row_level_security() -> None:
    sql = "create table public.leaky (id int);"
    created = {name.lower() for name in CREATE_TABLE.findall(sql)}
    protected = {name.lower() for name in ENABLE_RLS.findall(sql)}

    assert created - protected == {"leaky"}


@pytest.mark.parametrize(
    "limit",
    [
        MAX_LISTENED_SECONDS,
        MAX_PLANNED_MINUTES,
        MAX_SESSION_SAMPLES,
        MAX_PULSE_BPM,
        MAX_VARIATION_MS,
        SUMMARY_MAX_CHARS,
        MAX_STAGE_SECONDS,
        MAX_STAGE_SECONDS * 1000,
        MAX_STAGE_BEATS,
        int(MAX_RR_MS),
    ],
)
def test_database_repeats_the_limits_of_the_api(limit: int) -> None:
    """Changing a limit of the API needs a migration that changes it in the database too (SEC-P5)."""
    assert re.search(rf"\b{limit}\b", LIMITS_MIGRATION.read_text(encoding="utf-8"))


def test_database_knows_every_goal_and_stage() -> None:
    sql = LIMITS_MIGRATION.read_text(encoding="utf-8")

    assert all(f"'{name}'" in sql for name in [*(goal.value for goal in Goal), *RECORDING_STAGES])
    assert f"cardinality(goals) <= {len(Goal)}" in sql


@pytest.mark.parametrize("limit", [MAX_SESSIONS_PER_ACCOUNT, MAX_RECORDINGS_PER_ACCOUNT])
def test_database_enforces_the_account_limits_with_the_code_the_api_translates(limit: int) -> None:
    """Changing a limit per account needs a migration that changes it in the database too (ADR-63)."""
    text = ACCOUNT_LIMITS_MIGRATION.read_text(encoding="utf-8")
    assert re.search(rf">= {limit}\b", text)
    assert "errcode = '54000'" in text


def test_database_repeats_the_shape_and_the_limit_of_a_melody() -> None:
    """A melody the API accepts is one the table accepts, and the other way round (P-17)."""
    sql = MELODIES_MIGRATION.read_text(encoding="utf-8")

    catalogs = [*Instrument, *Ambience, *SoundProfile, *MusicLevel]
    assert all(f"'{value.value}'" in sql for value in catalogs)
    assert f"between 1 and {MELODY_NAME_MAX_CHARS}" in sql
    assert f"between 0 and {MAX_MELODY_POSITION_SECONDS}" in sql
    assert re.search(rf">= {MAX_MELODIES_PER_ACCOUNT}\b", sql)
    assert "errcode = '54000'" in sql
