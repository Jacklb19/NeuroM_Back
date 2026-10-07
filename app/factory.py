"""Builds the FastAPI application.

Kept apart from ``app.main`` so tests can build an app from explicit
``Settings`` without the module-level instance reading the real environment.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.constants import (
    API_PREFIX,
    API_TITLE,
    API_VERSION,
    CORS_ALLOWED_HEADERS,
    CORS_ALLOWED_METHODS,
    CORS_PREFLIGHT_MAX_AGE_SECONDS,
)
from app.errors import UnexpectedErrorMiddleware, exception_handlers, openapi_error_responses
from app.routes import API_ROUTERS


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the API configured by ``settings`` (the environment when omitted).

    Raises ``SettingsError`` when the environment is incomplete, so a broken
    deployment fails at boot instead of on its first request.
    """
    resolved = settings if settings is not None else get_settings()
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        exception_handlers=exception_handlers(),
        responses=openapi_error_responses(),
    )

    def pinned_settings() -> Settings:
        return resolved

    # Endpoints that need configuration depend on get_settings; pinning it here
    # makes them see the same settings the CORS middleware was built with.
    app.dependency_overrides[get_settings] = pinned_settings
    # The middleware added last is the outermost. The catch-all goes first so
    # CORSMiddleware wraps it and an unexpected 500 still reaches the browser.
    app.add_middleware(UnexpectedErrorMiddleware)
    # Identity travels as a bearer token, never as a cookie (ADR-19), so
    # credentials stay off and only the known frontends are allowed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.allowed_origins,
        allow_credentials=False,
        allow_methods=CORS_ALLOWED_METHODS,
        allow_headers=CORS_ALLOWED_HEADERS,
        max_age=CORS_PREFLIGHT_MAX_AGE_SECONDS,
    )
    for router in API_ROUTERS:
        app.include_router(router, prefix=API_PREFIX)
    return app
