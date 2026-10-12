"""Guided recordings a person sends for training, and the published classifier (ADR-32, ADR-33).

The recordings are heartbeats, so sending, listing and withdrawing them
needs a session past the second factor (ADR-10), and the database is reached
with the person's own token: row level security decides again whose they
are. Nothing here trains anything: training runs outside the API, and a new
version only exists once a person publishes it.
"""

import hashlib
import json
from http import HTTPStatus
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import TypeAdapter, ValidationError

from app.constants import CLASSIFIER_PATH, RECORDINGS_PATH
from app.errors import ApiError, ErrorCode
from app.schemas import ClassifierVersion, RecordingList, RecordingSummary, SavedRecording, TrainingRecording
from app.security import Identity, current_identity, gateway_of, health_data_identity
from app.supabase import JsonValue, SupabaseGateway

router = APIRouter(tags=["training"])

_RECORDING_PATH: Final = f"{RECORDINGS_PATH}/{{recording_id}}"
_SUMMARIES: Final = TypeAdapter(list[RecordingSummary])
_VERSION: Final = TypeAdapter(ClassifierVersion)

HealthDataIdentity = Annotated[Identity, Depends(health_data_identity)]
Gateway = Annotated[SupabaseGateway, Depends(gateway_of)]


def _unexpected_storage_answer() -> ApiError:
    """The database answered something the API does not understand; nothing of it is passed on."""
    return ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.STORAGE_UNAVAILABLE)


def model_checksum(model: dict[str, Any]) -> str:
    """SHA-256 of a model file in a fixed spelling, so it does not depend on how the JSON was laid out.

    The training job computes it the same way when it publishes a version
    (``training/publish.py``). ``Any`` mirrors the JSON of the model, which
    the app validates on its own before using it.
    """
    canonical = json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.post(
    RECORDINGS_PATH,
    status_code=HTTPStatus.CREATED,
    summary="Send a guided recording for training",
    response_description="The recording was stored.",
    responses={HTTPStatus.OK: {"model": SavedRecording, "description": "The recording was already stored."}},
)
async def send_recording(
    recording: TrainingRecording, identity: HealthDataIdentity, gateway: Gateway, response: Response
) -> SavedRecording:
    """Store a guided recording the person agreed to send.

    The id is the one the device generated, so sending it twice stores it
    once. Only recordings made with a source that measures a person are
    accepted, and only with ``consent`` set: nothing is stored otherwise.
    """
    if not recording.consent:
        raise ApiError(HTTPStatus.FORBIDDEN, ErrorCode.CONSENT_REQUIRED)
    created = await gateway.call_function(
        "save_training_recording", {"p_recording": recording.model_dump(mode="json")}, identity.token
    )
    if not isinstance(created, bool):
        raise _unexpected_storage_answer()
    if not created:
        response.status_code = HTTPStatus.OK
    return SavedRecording(id=recording.id, created=created)


@router.get(RECORDINGS_PATH, summary="List the recordings the person sent")
async def list_recordings(identity: HealthDataIdentity, gateway: Gateway) -> RecordingList:
    """What the person has sent, newest first, without the heartbeats."""
    listed = await gateway.call_function("list_training_recordings", {}, identity.token)
    return RecordingList(recordings=_validated(_SUMMARIES, listed))


@router.delete(
    _RECORDING_PATH,
    status_code=HTTPStatus.NO_CONTENT,
    summary="Withdraw a recording",
    response_description="The recording was deleted.",
)
async def withdraw_recording(recording_id: UUID, identity: HealthDataIdentity, gateway: Gateway) -> None:
    deleted = await gateway.call_function("delete_training_recording", {"p_id": str(recording_id)}, identity.token)
    if not isinstance(deleted, bool):
        raise _unexpected_storage_answer()
    if not deleted:
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.NOT_FOUND)


@router.get(CLASSIFIER_PATH, summary="Read the latest published version of the classifier")
async def read_classifier(
    identity: Annotated[Identity, Depends(current_identity)], gateway: Gateway
) -> ClassifierVersion:
    """The latest version a person published, with its file (Table 15).

    Answers 404 while none has been published: the app keeps the model it
    ships with. The file is served only if it matches the checksum recorded
    when it was published; the app validates its contents again.
    """
    found = await gateway.call_function("current_classifier", {}, identity.token)
    if found is None:
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.NOT_FOUND)
    version = _validated(_VERSION, found)
    if model_checksum(version.model) != version.sha256:
        raise _unexpected_storage_answer()
    return version


def _validated[Model](adapter: TypeAdapter[Model], value: JsonValue) -> Model:
    """Check what the database returned before it leaves the API."""
    try:
        return adapter.validate_python(value)
    except ValidationError:
        raise _unexpected_storage_answer() from None
