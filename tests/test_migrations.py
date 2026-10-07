"""Static checks on the Supabase migrations.

Row level security is the second barrier that keeps one user from reading
another user's health data (ADR-10), so every table must enable it.
"""

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"

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
