import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.core.config import get_settings
from app.core.middleware import JWTAuthMiddleware
from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router

settings = get_settings()

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        (
            structlog.processors.JSONRenderer()
            if settings.environment != "dev"
            else structlog.dev.ConsoleRenderer()
        ),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info(
        "core-api.startup",
        environment=settings.environment,
        version=settings.app_version,
    )
    yield
    log.info("core-api.shutdown")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# JWT auth middleware: estrae token Bearer, popola request.state.token_payload,
# 401 immediato per token invalidi. Vedi app/core/middleware.py.
# NB: ordine importante. Quando aggiungeremo CORS in Sessione 4+, il CORS deve
# stare PRIMA (più esterno) di JWTAuthMiddleware.
app.add_middleware(JWTAuthMiddleware)

# Auth router: /api/v1/auth/{login,refresh,me}
app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])

# Admin router: /api/v1/admin/* (super_admin only — vedi ADR-0006)
app.include_router(admin_router, prefix="/api/v1/admin", tags=["admin"])


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "version": settings.app_version,
    }


def _custom_openapi() -> dict[str, Any]:
    """OpenAPI schema con BearerAuth security scheme (HTTP bearer / JWT)
    registrato globalmente. Swagger UI mostra il bottone "Authorize" che
    permette di incollare un token e usarlo per tutti i request successivi.

    Endpoint pubblici (`/api/v1/auth/login`, `/api/v1/auth/refresh`) fanno
    opt-out con `openapi_extra={"security": []}` nel router.
    Vedi ADR-0003 §"Implementazione" per il flow completo.
    """
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Access token JWT (HS256). Ottenere via POST /api/v1/auth/login.",
        },
    }
    schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi  # type: ignore[method-assign]
