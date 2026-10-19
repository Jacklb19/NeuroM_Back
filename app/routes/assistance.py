"""What the language model writes for the person (Table 15, RF-13 and RF-15; ADR-08).

Two endpoints, both outside the control loop (ADR-05): a plan proposed from
the goals the person chose, and a finished session told in plain words. Every
text they return is marked as generated automatically.
"""

from http import HTTPStatus
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.assistance import (
    PlanRequest,
    PlanSuggestion,
    SessionNarrative,
    SummaryRequest,
    suggest_plan,
    summarize_session,
)
from app.constants import PLAN_SUGGESTION_PATH, PROGRESS_SUMMARY_PATH, SESSION_SUMMARY_SUFFIX, SESSIONS_PATH
from app.progress_assistance import ProgressNarrative, ProgressRequest, explain_progress
from app.errors import ApiError, ErrorCode
from app.language_model import LanguageModelGateway
from app.security import Identity, current_identity

router = APIRouter(tags=["language model"])

_SUMMARY_PATH: Final = f"{SESSIONS_PATH}/{{session_id}}{SESSION_SUMMARY_SUFFIX}"

Account = Annotated[Identity, Depends(current_identity)]


def language_model_of(request: Request) -> LanguageModelGateway:
    """Dependency: the application's language model gateway."""
    gateway: LanguageModelGateway = request.app.state.language_model
    return gateway


LanguageModel = Annotated[LanguageModelGateway, Depends(language_model_of)]


@router.post(PROGRESS_SUMMARY_PATH, summary="Explain browser-computed history comparisons")
async def progress_summary(request: ProgressRequest, _identity: Account, model: LanguageModel) -> ProgressNarrative:
    """Choose reviewed wording for at most three consented comparisons (ADR-48).

    Requires an account, following the existing aggregated session summary.
    No history is read or stored, and only the caller's supplied figures are
    described. Free-form text and unknown references from the model are refused.
    """
    if not request.consent:
        raise ApiError(HTTPStatus.FORBIDDEN, ErrorCode.CONSENT_REQUIRED)
    return await explain_progress(request, model)


@router.post(PLAN_SUGGESTION_PATH, summary="Propose a session plan from the chosen goals")
async def propose_plan(plan: PlanRequest, _identity: Account, model: LanguageModel) -> PlanSuggestion:
    """A session length, among the allowed ones, for the goals the person chose.

    Needs an account, so the language model cannot be used anonymously, but
    not the second factor: goals and an hour of the day are not health data.
    Nothing is stored. The person can always keep their own plan.
    """
    return await suggest_plan(plan, model)


@router.post(_SUMMARY_PATH, summary="Tell a session in plain words")
async def summarize(
    session_id: UUID, request: SummaryRequest, _identity: Account, model: LanguageModel
) -> SessionNarrative:
    """A few sentences that describe one session, from the figures the app already shows.

    The browser sends the aggregated figures, each checked here against a
    closed range, so the session does not have to be stored in the cloud and
    nothing but numbers and catalog values reaches the language model. The
    figures are sent only when the request says the person agreed
    (``consent``). Nothing is read or stored: the answer contains no data the
    caller did not send, which is why an account is enough and the second
    factor is not asked for. ``session_id`` only names the session.
    """
    del session_id  # Part of the route of Table 15; the API has no use for it.
    if not request.consent:
        raise ApiError(HTTPStatus.FORBIDDEN, ErrorCode.CONSENT_REQUIRED)
    return await summarize_session(request.figures, request.locale, model)
