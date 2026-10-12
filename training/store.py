"""Brings from Supabase what the training job needs, and keeps its private files there.

Usage:
    python store.py fetch <export json>   download the heartbeat cache and the recordings people sent
    python store.py upload-cache          upload cache/*.npz (run once, after extract.py)

It uses the service role key, which bypasses row level security: it is the
only way to read the recordings of every person, and the reason this script
runs only on the maintainer's machine or in the training workflow, never in
the API. It needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the
environment, prints neither, and uses only the standard library.

The recordings are written in the format the app exports
(``collected.py`` reads it), so one file can come from a person or from here.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Final

CACHE_DIR: Final = Path(__file__).parent / "cache"
EXPORT_FORMAT: Final = "neuromelody-collection"
SCHEMA_VERSION: Final = 1
_REST: Final = "/rest/v1"
_TIMEOUT_SECONDS: Final = 30
_PAGE: Final = 500
"""Rows asked for at a time; a recording weighs a few kilobytes."""


def _request(method: str, path: str, body: object | None = None, prefer: str | None = None) -> Any:
    """One call to the project's REST API as the service role. ``Any`` mirrors JSON."""
    url = os.environ["SUPABASE_URL"].rstrip("/") + _REST + path
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer is not None:
        headers["Prefer"] = prefer
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310 - the project's own URL
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
        text = response.read().decode()
    return json.loads(text) if text else None


def to_export(rows: list[dict[str, Any]], exported_at: str) -> dict[str, Any]:
    """The rows of ``training_recordings`` in the format the app exports. ``Any`` mirrors JSON."""
    return {
        "format": EXPORT_FORMAT,
        "schemaVersion": SCHEMA_VERSION,
        "exportedAt": exported_at,
        "recordings": [
            {
                "id": row["id"],
                "participant": row["participant"],
                "recordedAt": row["recorded_at"],
                "consentAt": row["consent_at"],
                "sourceKind": row["source_kind"],
                "segments": [
                    {
                        "label": segment["label"],
                        "plannedSeconds": segment["planned_seconds"],
                        "beatTimesMs": segment["beat_times_ms"],
                        "rrMs": segment["rr_ms"],
                    }
                    for segment in row["segments"]
                ],
            }
            for row in rows
        ],
    }


def _all_rows(table: str, columns: str, order: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while True:
        page = _request("GET", f"/{table}?select={columns}&order={order}&limit={_PAGE}&offset={len(rows)}")
        rows.extend(page)
        if len(page) < _PAGE:
            return rows


def fetch(export_path: Path, exported_at: str) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    assets = _all_rows("training_assets", "name,content", "name")
    for asset in assets:
        # PostgREST returns bytea as "\\x" followed by hexadecimal digits.
        (CACHE_DIR / Path(asset["name"]).name).write_bytes(bytes.fromhex(asset["content"].removeprefix("\\x")))
    rows = _all_rows("training_recordings", "id,participant,recorded_at,consent_at,source_kind,segments", "recorded_at")
    export_path.write_text(json.dumps(to_export(rows, exported_at)), encoding="utf-8")
    print(f"cache files: {len(assets)}; recordings: {len(rows)}; people: {len({row['participant'] for row in rows})}")


def upload_cache() -> None:
    files = sorted(CACHE_DIR.glob("*.npz"))
    if not files:
        raise SystemExit("cache/ is empty: run extract.py first")
    rows = [{"name": file.name, "content": "\\x" + file.read_bytes().hex()} for file in files]
    _request("POST", "/training_assets?on_conflict=name", rows, prefer="resolution=merge-duplicates,return=minimal")
    print(f"uploaded {len(rows)} cache files")


def main() -> None:
    from datetime import UTC, datetime

    match sys.argv[1:]:
        case ["fetch", export_path]:
            fetch(Path(export_path), datetime.now(UTC).isoformat())
        case ["upload-cache"]:
            upload_cache()
        case _:
            raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
