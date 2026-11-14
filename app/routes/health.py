"""Health endpoint (Table 15: ``GET /v1/health``, public)."""

from enum import StrEnum
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.constants import API_VERSION, HEALTH_PATH
from app.health_probe import SupabaseProbe

router = APIRouter(tags=["health"])


class HealthStatus(StrEnum):
    """Possible answers of the health check."""

    OK = "ok"
    #: The API runs but cannot reach Supabase: accounts and sessions fail until it can.
    DEGRADED = "degraded"


class DependencyStatus(StrEnum):
    OK = "ok"
    UNREACHABLE = "unreachable"


class HealthResponse(BaseModel):
    """Body of the health check."""

    status: HealthStatus
    version: str
    supabase: DependencyStatus


def supabase_probe_of(request: Request) -> SupabaseProbe:
    """Dependency: the application's look at Supabase."""
    probe: SupabaseProbe = request.app.state.supabase_probe
    return probe


Probe = Annotated[SupabaseProbe, Depends(supabase_probe_of)]


@router.get(
    HEALTH_PATH,
    summary="Check that the service and Supabase are up",
    response_description="The service is running, reports its version and can reach Supabase.",
    responses={HTTPStatus.SERVICE_UNAVAILABLE: {"model": HealthResponse, "description": "Supabase cannot be reached."}},
)
async def read_health(probe: Probe, response: Response) -> HealthResponse:
    """Public: it needs no token and reads no data. It looks at Supabase with
    the public key at most every 30 seconds (``SupabaseProbe``), and answers
    503 while Supabase cannot be reached, so a monitor sees the outage."""
    if await probe.reachable():
        return HealthResponse(status=HealthStatus.OK, version=API_VERSION, supabase=DependencyStatus.OK)
    response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
    return HealthResponse(status=HealthStatus.DEGRADED, version=API_VERSION, supabase=DependencyStatus.UNREACHABLE)
