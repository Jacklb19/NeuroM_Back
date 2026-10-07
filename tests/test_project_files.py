"""Project files that must agree with each other and with the code."""

import json
import tomllib
from pathlib import Path
from typing import Final

from app.constants import API_VERSION

ROOT: Final = Path(__file__).resolve().parent.parent
ENTRYPOINT: Final = "app/main.py"
REQUIREMENT_FILES: Final = ("requirements.txt", "requirements-dev.txt")
EXCLUDED_DIRS: Final = ("tests", ".venv", "supabase")
"""Directories that must stay out of the function bundle: test code, the local
environment and the SQL migrations, none of which the API runs."""


def read_pyproject() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def project_table() -> dict[str, object]:
    project = read_pyproject()["project"]
    assert isinstance(project, dict)
    return project


def test_api_version_matches_pyproject() -> None:
    assert project_table()["version"] == API_VERSION


def test_python_version_file_matches_requires_python() -> None:
    python_version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()

    assert project_table()["requires-python"] == f">={python_version}"


def test_vercel_function_points_to_the_entrypoint_with_explicit_limits() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    function = config["functions"][ENTRYPOINT]

    assert (ROOT / ENTRYPOINT).is_file()
    assert isinstance(function["maxDuration"], int)
    assert function["maxDuration"] > 0
    # Vercel takes a single glob here, so the directories are joined in a brace group.
    assert function["excludeFiles"] == "{" + ",".join(EXCLUDED_DIRS) + "}/**"


def test_every_requirement_is_pinned() -> None:
    for file_name in REQUIREMENT_FILES:
        for raw_line in (ROOT / file_name).read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or line.startswith("-r "):
                continue
            requirement = line.split(";", 1)[0].strip()
            assert "==" in requirement, f"{file_name}: '{requirement}' is not pinned"
