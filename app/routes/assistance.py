"""What the language model writes for the person (Table 15, RF-13 and RF-15; ADR-08).

Two endpoints, both outside the control loop (ADR-05): a plan proposed from
the goals the person chose, and a finished session told in plain words. Every
text they return is marked as generated automatically.
"""

from http import HTTPStatus
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import TypeAdapter, ValidationError

from app.assistance import (
    PlanRequest,
    PlanSuggestion,
    SessionNarrative,
    SummaryRequest,
    suggest_plan,
    summarize_session,
)
from app.constants import PLAN_SUGGESTION_PATH, SESSION_SUMMARY_SUFFIX, SESSIONS_PATH
from app.errors import ApiError, ErrorCode
from app.language_model import LanguageModelGateway
from app.schemas import Session
from app.security import Identity, current_identity, gateway_of, health_data_identity
from app.supabase import SupabaseGateway

router = APIRouter(tags=["language model"])

_SUMMARY_PATH: Final = f"{SESSIONS_PATH}/{{session_id}}{SESSION_SUMMARY_SUFFIX}"
_SESSION: Final = TypeAdapter(Session)


def language_model_of(request: Request) -> LanguageModelGateway:
    """Dependency: the application's language model gateway."""
    gateway: LanguageModelGateway = request.app.state.language_model
    return gateway


LanguageModel = Annotated[LanguageModelGateway, Depends(language_model_of)]


@router.post(PLAN_SUGGESTION_PATH, summary="Propose a session plan from the chosen goals")
async def propose_plan(
    plan: PlanRequest, _identity: Annotated[Identity, Depends(current_identity)], model: LanguageModel
) -> PlanSuggestion:
    """A session length, among the allowed ones, for the goals the person chose.

    Needs an account, so the language model cannot be used anonymously, but
    not the second factor: goals and an hour of the day are not health data.
    Nothing is stored. The person can always keep their own plan.
    """
    return await suggest_plan(plan, model)


@router.post(_SUMMARY_PATH, summary="Tell a stored session in plain words")
async def summarize(
    session_id: UUID,
    request: SummaryRequest,
    identity: Annotated[Identity, Depends(health_data_identity)],
    gateway: Annotated[SupabaseGateway, Depends(gateway_of)],
    model: LanguageModel,
) -> SessionNarrative:
    """A few sentences that describe one of the person's stored sessions.

    The session is read with the person's own token, so only its owner, past
    the second factor, can ask (ADR-10). The language model receives
    aggregated figures only, and only when the request says the person
    agreed to that (``consent``); nothing is sent otherwise.
    """
    if not request.consent:
        raise ApiError(HTTPStatus.FORBIDDEN, ErrorCode.CONSENT_REQUIRED)
    found = await gateway.call_function("get_session", {"p_id": str(session_id)}, identity.token)
    if found is None:
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.NOT_FOUND)
    try:
        session = _SESSION.validate_python(found)
    except ValidationError:
        raise ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.STORAGE_UNAVAILABLE) from None
    return await summarize_session(session, request.locale, model)
