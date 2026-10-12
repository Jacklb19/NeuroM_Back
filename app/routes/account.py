"""The signed-in person's account (ADR-18: ``DELETE /v1/account``)."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends

from app.constants import ACCOUNT_PATH
from app.errors import ApiError, ErrorCode
from app.security import Identity, current_identity, gateway_of
from app.supabase import SupabaseGateway

router = APIRouter(tags=["account"])


@router.delete(
    ACCOUNT_PATH,
    status_code=HTTPStatus.NO_CONTENT,
    summary="Delete the account and everything stored for it",
    response_description="The account, its sessions and its indicators were deleted.",
)
async def delete_account(
    identity: Annotated[Identity, Depends(current_identity)],
    gateway: Annotated[SupabaseGateway, Depends(gateway_of)],
) -> None:
    """Delete the caller's own account; plans, sessions and samples fall in cascade.

    The account to delete is always the one of the token, never one named in
    the request. An account protected by a second factor can only be deleted
    from a session that passed it, so a stolen password is not enough. This
    is the one place where the service role key is used.
    """
    if not identity.has_second_factor and await gateway.has_verified_second_factor(identity.user_id):
        raise ApiError(HTTPStatus.FORBIDDEN, ErrorCode.SECOND_FACTOR_REQUIRED)
    await gateway.delete_account(identity.user_id)
