"""HTTP endpoints, one module per resource."""

from typing import Final

from fastapi import APIRouter

from app.routes import account, assistance, health, sessions

API_ROUTERS: Final[tuple[APIRouter, ...]] = (health.router, sessions.router, account.router, assistance.router)
"""Routers mounted under ``API_PREFIX``; add a resource's router here to publish it."""
