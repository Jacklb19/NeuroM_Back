"""HTTP endpoints, one module per resource."""

from typing import Final

from fastapi import APIRouter

from app.routes import health

API_ROUTERS: Final[tuple[APIRouter, ...]] = (health.router,)
"""Routers mounted under ``API_PREFIX``; add a resource's router here to publish it."""
