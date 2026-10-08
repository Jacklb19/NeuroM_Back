"""Liveness endpoint (Table 15: ``GET /v1/health``, public)."""

from enum import StrEnum

from fastapi import APIRouter
from pydantic import BaseModel

from app.constants import API_VERSION, HEALTH_PATH

router = APIRouter(tags=["health"])


class HealthStatus(StrEnum):
    """Possible answers of the health check."""

    OK = "ok"


class HealthResponse(BaseModel):
    """Body of the health check."""

    status: HealthStatus
    version: str


@router.get(
    HEALTH_PATH,
    summary="Check that the service is up",
    response_description="The service is running and reports its version.",
)
async def read_health() -> HealthResponse:
    """Public and dependency-free on purpose: it needs no token, reads no secret
    and calls no other service, so it is cheap to poll and cannot leak data."""
    return HealthResponse(status=HealthStatus.OK, version=API_VERSION)
