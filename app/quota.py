"""Daily quota of calls to the language model (ADR-54).

Every endpoint that asks the model spends one call of the caller's quota
after its own checks (consent, request shape), and only when the answer is
not already in the gateway's cache, right before the model is asked. A
cached answer costs the provider nothing, so it costs the person nothing.
The count lives in the database, because each serverless function forgets
what it saw once it goes cold.
"""

from http import HTTPStatus

from app.constants import LANGUAGE_MODEL_DAILY_CALLS, LANGUAGE_MODEL_QUOTA_FUNCTION
from app.errors import ApiError, ErrorCode
from app.language_model import BeforeCall
from app.security import Identity
from app.supabase import SupabaseGateway


async def spend_language_model_call(supabase: SupabaseGateway, identity: Identity) -> None:
    """Count one call of this account, or refuse with ``rate_limited`` once today's limit is reached."""
    allowed = await supabase.call_function(
        LANGUAGE_MODEL_QUOTA_FUNCTION, {"p_daily_limit": LANGUAGE_MODEL_DAILY_CALLS}, identity.token
    )
    if allowed is not True:
        raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, ErrorCode.RATE_LIMITED)


def language_model_quota(supabase: SupabaseGateway, identity: Identity) -> BeforeCall:
    """What the gateway runs right before asking the model: spend one call of this account."""

    async def spend() -> None:
        await spend_language_model_call(supabase, identity)

    return spend
