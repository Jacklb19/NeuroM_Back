"""Melodies the signed-in person saved from the listening screen (P-17).

They are not health data, so an account is enough: no second factor and no
sync consent, unlike sessions. The database is reached with the person's own
token, so row level security checks again that each melody is theirs, and
another person's melody answers exactly like one that does not exist.
"""

from http import HTTPStatus
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import TypeAdapter, ValidationError

from app.constants import MELODIES_PATH
from app.errors import ApiError, ErrorCode
from app.melodies import Melody, MelodyContent, MelodyList, SavedMelody
from app.security import Identity, current_identity, gateway_of
from app.supabase import SupabaseGateway

router = APIRouter(tags=["melodies"])

_MELODY_PATH: Final = f"{MELODIES_PATH}/{{melody_id}}"
_MELODIES: Final = TypeAdapter(list[Melody])

AccountIdentity = Annotated[Identity, Depends(current_identity)]
Gateway = Annotated[SupabaseGateway, Depends(gateway_of)]


def _unexpected_storage_answer() -> ApiError:
    """The database answered something the API does not understand; nothing of it is passed on."""
    return ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.STORAGE_UNAVAILABLE)


@router.get(MELODIES_PATH, summary="List the person's saved melodies")
async def list_melodies(identity: AccountIdentity, gateway: Gateway) -> MelodyList:
    """Every melody of the account, most recently changed first; the account keeps at most 50."""
    listed = await gateway.call_function("list_melodies", {}, identity.token)
    try:
        return MelodyList(melodies=_MELODIES.validate_python(listed))
    except ValidationError:
        raise _unexpected_storage_answer() from None


@router.put(
    _MELODY_PATH,
    summary="Save a melody, or keep the newest version of one",
    response_description="The melody already existed; the newest version by `updated_at` is kept.",
    responses={HTTPStatus.CREATED: {"model": SavedMelody, "description": "The melody was created."}},
)
async def save_melody(
    melody_id: UUID, melody: MelodyContent, identity: AccountIdentity, gateway: Gateway, response: Response
) -> SavedMelody:
    """Store the melody under the id the device generated, so sending it twice never duplicates it.

    When it exists, only a newer version (by ``updated_at``) replaces its name
    and its mix: two devices end with the same melody whatever the order.
    A full account answers 409 ``limit_reached``.
    """
    payload = melody.model_dump(mode="json") | {"id": str(melody_id)}
    created = await gateway.call_function("save_melody", {"p_melody": payload}, identity.token)
    if not isinstance(created, bool):
        raise _unexpected_storage_answer()
    response.status_code = HTTPStatus.CREATED if created else HTTPStatus.OK
    return SavedMelody(id=melody_id, created=created)


@router.delete(
    _MELODY_PATH,
    status_code=HTTPStatus.NO_CONTENT,
    summary="Delete one saved melody",
    response_description="The melody was deleted.",
)
async def delete_melody(melody_id: UUID, identity: AccountIdentity, gateway: Gateway) -> None:
    deleted = await gateway.call_function("delete_melody", {"p_id": str(melody_id)}, identity.token)
    if not isinstance(deleted, bool):
        raise _unexpected_storage_answer()
    if not deleted:
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.NOT_FOUND)
