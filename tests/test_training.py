"""Guided recordings sent for training and the published classifier (ADR-32, ADR-33)."""

from http import HTTPStatus
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from app.constants import API_PREFIX, CLASSIFIER_PATH, RECORDINGS_PATH
from app.errors import ErrorCode
from app.routes.training import model_checksum
from tests.supabase_fake import FIRST_FACTOR_ONLY, FakeSupabase, SigningKey, bearer, database_error, make_token

RECORDINGS_URL: Final = f"{API_PREFIX}{RECORDINGS_PATH}"
RECORDING_ID: Final = "017f22e2-79b0-7000-8000-0000000000c1"
RECORDING_URL: Final = f"{RECORDINGS_URL}/{RECORDING_ID}"
CLASSIFIER_URL: Final = f"{API_PREFIX}{CLASSIFIER_PATH}"
MODEL: Final = {"schemaVersion": 1, "kind": "multinomial_logistic_regression", "temperature": 1.5, "weights": [[0.1, -0.2]]}


def stage(label: str, seconds: int, **overrides: Any) -> dict[str, Any]:
    return {"label": label, "planned_seconds": seconds, "beat_times_ms": [800, 1600.5], "rr_ms": [800, 800.5]} | overrides


def recording(**overrides: Any) -> dict[str, Any]:
    return {
        "id": RECORDING_ID,
        "participant": "5f0c1d4e-1111-4222-8333-444455556666",
        "recorded_at": "2026-10-12T10:00:00Z",
        "source_kind": "ble",
        "segments": [stage("rest", 300), stage("slow_breathing", 180), stage("demanding_task", 180)],
        "consent": True,
    } | overrides


def published(model: dict[str, Any] = MODEL, **overrides: Any) -> dict[str, Any]:
    return {
        "version": "2026-10-20",
        "sha256": model_checksum(model),
        "published_at": "2026-10-20T00:00:00+00:00",
        "model": model,
    } | overrides


@pytest.fixture
def headers(signing_key: SigningKey) -> dict[str, str]:
    return bearer(make_token(signing_key))


# --- Recordings ---


def test_stores_a_recording_as_the_person_and_once(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    supabase.results["save_training_recording"] = True
    first = client.post(RECORDINGS_URL, json=recording(), headers=headers)
    supabase.results["save_training_recording"] = False
    again = client.post(RECORDINGS_URL, json=recording(), headers=headers)

    assert first.status_code == HTTPStatus.CREATED
    assert first.json() == {"id": RECORDING_ID, "created": True}
    assert again.status_code == HTTPStatus.OK
    assert again.json() == {"id": RECORDING_ID, "created": False}
    call = supabase.calls[0]
    assert call.name == "save_training_recording"
    # Acting as the person: row level security decides whose the recording is.
    assert call.headers["Authorization"] == headers["Authorization"]
    sent = call.arguments["p_recording"]
    assert sent["consent"] is True
    assert [segment["label"] for segment in sent["segments"]] == ["rest", "slow_breathing", "demanding_task"]
    assert sent["segments"][0]["rr_ms"] == [800, 800.5]


def test_heartbeats_need_the_second_factor(
    client: TestClient, supabase: FakeSupabase, signing_key: SigningKey
) -> None:
    first_factor = bearer(make_token(signing_key, assurance=FIRST_FACTOR_ONLY))

    responses = [
        client.post(RECORDINGS_URL, json=recording()),
        client.post(RECORDINGS_URL, json=recording(), headers=first_factor),
        client.get(RECORDINGS_URL, headers=first_factor),
        client.delete(RECORDING_URL, headers=first_factor),
    ]

    assert responses[0].status_code == HTTPStatus.UNAUTHORIZED
    assert [response.status_code for response in responses[1:]] == [HTTPStatus.FORBIDDEN] * 3
    assert all(response.json() == {"code": ErrorCode.SECOND_FACTOR_REQUIRED} for response in responses[1:])
    assert supabase.calls == []


def test_without_consent_nothing_is_stored(client: TestClient, supabase: FakeSupabase, headers: dict[str, str]) -> None:
    refused = client.post(RECORDINGS_URL, json=recording(consent=False), headers=headers)
    # The database refuses on its own too, should a request ever get that far.
    supabase.results["save_training_recording"] = database_error("P0001", "consent_required")
    by_database = client.post(RECORDINGS_URL, json=recording(), headers=headers)

    assert refused.status_code == by_database.status_code == HTTPStatus.FORBIDDEN
    assert refused.json() == by_database.json() == {"code": ErrorCode.CONSENT_REQUIRED}
    assert len(supabase.calls) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        # Beats that are nobody's teach the model nothing.
        {"source_kind": "simulator"},
        {"source_kind": "recording"},
        {"participant": "ana@example.test"},
        {"segments": [stage("rest", 300), stage("slow_breathing", 180)]},
        {"segments": [stage("slow_breathing", 180), stage("rest", 300), stage("demanding_task", 180)]},
        {"segments": [stage("rest", 300, rr_ms=[800]), stage("slow_breathing", 180), stage("demanding_task", 180)]},
        {"segments": [stage("rest", 300, beat_times_ms=[1600, 800]), stage("slow_breathing", 180), stage("demanding_task", 180)]},
        {"segments": [stage("rest", 300, rr_ms=[800, -1]), stage("slow_breathing", 180), stage("demanding_task", 180)]},
        {"segments": [stage("rest", 1, beat_times_ms=[800, 1600]), stage("slow_breathing", 180), stage("demanding_task", 180)]},
        {"name": "Ana"},
    ],
)
def test_a_recording_outside_the_protocol_is_refused(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str], invalid: dict[str, Any]
) -> None:
    response = client.post(RECORDINGS_URL, json=recording(**invalid), headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    assert response.json() == {"code": ErrorCode.INVALID_REQUEST}
    assert supabase.calls == []


def test_lists_what_was_sent_without_the_heartbeats_and_withdraws_it(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    summary = {"id": RECORDING_ID, "recorded_at": "2026-10-12T10:00:00+00:00", "source_kind": "ble", "beats": 742}
    supabase.results["list_training_recordings"] = [summary]
    supabase.results["delete_training_recording"] = True

    listed = client.get(RECORDINGS_URL, headers=headers)
    withdrawn = client.delete(RECORDING_URL, headers=headers)
    # Somebody else's recording answers exactly like one that does not exist.
    supabase.results["delete_training_recording"] = False
    missing = client.delete(RECORDING_URL, headers=headers)

    assert listed.status_code == HTTPStatus.OK
    assert listed.json()["recordings"][0]["beats"] == 742
    assert withdrawn.status_code == HTTPStatus.NO_CONTENT
    assert missing.status_code == HTTPStatus.NOT_FOUND
    assert missing.json() == {"code": ErrorCode.NOT_FOUND}
    assert supabase.calls[-1].arguments == {"p_id": RECORDING_ID}


# --- Classifier ---


def test_serves_the_latest_published_classifier_to_any_account(
    client: TestClient, supabase: FakeSupabase, signing_key: SigningKey
) -> None:
    supabase.results["current_classifier"] = published()

    anonymous = client.get(CLASSIFIER_URL)
    response = client.get(CLASSIFIER_URL, headers=bearer(make_token(signing_key, assurance=FIRST_FACTOR_ONLY)))

    assert anonymous.status_code == HTTPStatus.UNAUTHORIZED
    # The model is not health data: an account is enough.
    assert response.status_code == HTTPStatus.OK
    assert response.json()["version"] == "2026-10-20"
    assert response.json()["model"] == MODEL


def test_answers_not_found_while_no_version_is_published(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str]
) -> None:
    supabase.results["current_classifier"] = None

    response = client.get(CLASSIFIER_URL, headers=headers)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"code": ErrorCode.NOT_FOUND}


@pytest.mark.parametrize(
    "stored",
    [
        # A file that is not the one that was published.
        published(MODEL | {"temperature": 9.0}, sha256=model_checksum(MODEL)),
        published(sha256="not-a-checksum"),
        {"version": "2026-10-20"},
        "not a version",
    ],
)
def test_never_serves_a_classifier_that_does_not_match_its_checksum(
    client: TestClient, supabase: FakeSupabase, headers: dict[str, str], stored: Any
) -> None:
    supabase.results["current_classifier"] = stored

    response = client.get(CLASSIFIER_URL, headers=headers)

    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert response.json() == {"code": ErrorCode.STORAGE_UNAVAILABLE}


def test_the_checksum_does_not_depend_on_how_the_file_is_laid_out() -> None:
    reordered = dict(reversed(list(MODEL.items())))

    assert model_checksum(reordered) == model_checksum(MODEL)
    assert model_checksum(MODEL | {"temperature": 1.6}) != model_checksum(MODEL)
