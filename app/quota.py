"""Daily quota of calls to the language model (ADR-54).

Every endpoint that asks the model spends one call of the caller's quota
first, after its own checks (consent, request shape) and before the model.
The count lives in the database, because each serverless function forgets
what it saw once it goes cold.
"""

from http import HTTPStatus

from app.constants import LANGUAGE_MODEL_DAILY_CALLS, LANGUAGE_MODEL_QUOTA_FUNCTION
from app.errors import ApiError, ErrorCode
from app.security import Identity
from app.supabase import SupabaseGateway


async def spend_language_model_call(supabase: SupabaseGateway, identity: Identity) -> None:
    """Count one call of this account, or refuse with ``rate_limited`` once today's limit is reached."""
    allowed = await supabase.call_function(
        LANGUAGE_MODEL_QUOTA_FUNCTION, {"p_daily_limit": LANGUAGE_MODEL_DAILY_CALLS}, identity.token
    )
    if allowed is not True:
        raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, ErrorCode.RATE_LIMITED)
