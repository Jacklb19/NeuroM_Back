"""Sessions of the signed-in person (Table 15 and ADR-18).

Sessions hold physiological indicators, so every endpoint here needs a
session that passed the second factor (ADR-10). The database is reached with
the person's own token: row level security checks again that each session is
theirs, which is why another person's session answers exactly like one that
does not exist.
"""

from http import HTTPStatus
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import TypeAdapter, ValidationError

from app.constants import SESSION_LIST_LIMIT, SESSIONS_PATH
from app.errors import ApiError, ErrorCode
from app.schemas import SavedSession, Session, SessionList, SessionSummary
from app.security import Identity, gateway_of, health_data_identity
from app.supabase import JsonValue, SupabaseGateway

router = APIRouter(tags=["sessions"])

_SESSION_PATH: Final = f"{SESSIONS_PATH}/{{session_id}}"
_SUMMARIES: Final = TypeAdapter(list[SessionSummary])

HealthDataIdentity = Annotated[Identity, Depends(health_data_identity)]
Gateway = Annotated[SupabaseGateway, Depends(gateway_of)]


def _unexpected_storage_answer() -> ApiError:
    """The database answered something the API does not understand; nothing of it is passed on."""
    return ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.STORAGE_UNAVAILABLE)


def _not_found() -> ApiError:
    return ApiError(HTTPStatus.NOT_FOUND, ErrorCode.NOT_FOUND)


@router.post(
    SESSIONS_PATH,
    status_code=HTTPStatus.CREATED,
    summary="Store a finished session",
    response_description="The session was created.",
    responses={HTTPStatus.OK: {"model": SavedSession, "description": "The session was already stored."}},
)
async def save_session(
    session: Session, identity: HealthDataIdentity, gateway: Gateway, response: Response
) -> SavedSession:
    """Store a session with its samples, in one transaction.

    The id is the one the device generated (ADR-18), so sending the same
    session twice never duplicates it: the second time answers 200 and only
    the rating given after listening is updated. It needs the person's
    consent to sync, recorded in their profile.
    """
    payload = session.model_dump(mode="json") | {"final_state": session.final_state}
    created = await gateway.call_function("save_session", {"p_session": payload}, identity.token)
    if not isinstance(created, bool):
        raise _unexpected_storage_answer()
    if not created:
        response.status_code = HTTPStatus.OK
    return SavedSession(id=session.id, created=created)


@router.get(SESSIONS_PATH, summary="List the person's sessions")
async def list_sessions(identity: HealthDataIdentity, gateway: Gateway) -> SessionList:
    """Sessions without their samples, newest first."""
    listed = await gateway.call_function("list_sessions", {"p_limit": SESSION_LIST_LIMIT}, identity.token)
    return SessionList(sessions=_validated(_SUMMARIES, listed))


@router.get(_SESSION_PATH, summary="Read one session with its samples")
async def read_session(session_id: UUID, identity: HealthDataIdentity, gateway: Gateway) -> Session:
    found = await gateway.call_function("get_session", {"p_id": str(session_id)}, identity.token)
    if found is None:
        raise _not_found()
    return _validated(TypeAdapter(Session), found)


@router.delete(
    _SESSION_PATH,
    status_code=HTTPStatus.NO_CONTENT,
    summary="Delete one session",
    response_description="The session and its samples were deleted.",
)
async def delete_session(session_id: UUID, identity: HealthDataIdentity, gateway: Gateway) -> None:
    deleted = await gateway.call_function("delete_session", {"p_id": str(session_id)}, identity.token)
    if not isinstance(deleted, bool):
        raise _unexpected_storage_answer()
    if not deleted:
        raise _not_found()


def _validated[Model](adapter: TypeAdapter[Model], value: JsonValue) -> Model:
    """Check what the database returned before it leaves the API."""
    try:
        return adapter.validate_python(value)
    except ValidationError:
        raise _unexpected_storage_answer() from None
