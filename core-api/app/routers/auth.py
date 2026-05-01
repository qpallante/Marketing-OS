"""Authentication router: /login, /refresh, /me.

Mountato in main.py con `prefix="/api/v1/auth"`.

Endpoint /login e /refresh sono **pubblici** (nessuna BearerAuth richiesta) —
opt-out dal global security scheme via `openapi_extra={"security": []}`.

Schemi pydantic inline qui: l'auth router è l'unico con schemi dedicati per
ora. Quando avremo > 1 router con schemi propri, estrarremo in `app/schemas/`.
CLAUDE.md: "Astrai solo quando hai 3 use case concreti."

Vedi ADR-0003 per le decisioni di design (HS256, stateless refresh, etc.).
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_authenticated_session, get_current_user
from app.core.security import (
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    verify_password,
)
from app.db.session import get_unauthenticated_db
from app.models import Client, User

log = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter()

PUBLIC_ROUTE: dict[str, Any] = {"security": []}


# ─── Schemas ─────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class ClientSummary(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str


class MeResponse(BaseModel):
    id: UUID
    email: str
    role: str
    is_active: bool
    client_id: UUID | None
    client: ClientSummary | None


# ─── Helpers ─────────────────────────────────────────────────────────────


def _email_hash(email: str) -> str:
    """SHA-256 hex dell'email (lowercase) per log forensics senza esporre PII (GDPR)."""
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()


def _build_token_pair(user: User) -> LoginResponse:
    return LoginResponse(
        access_token=create_access_token(
            user_id=user.id,
            client_id=user.client_id,
            role=user.role,
        ),
        refresh_token=create_refresh_token(user_id=user.id),
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        refresh_expires_in=settings.refresh_token_ttl_days * 86400,
    )


# ─── Endpoints ───────────────────────────────────────────────────────────


@router.post(
    "/login",
    response_model=LoginResponse,
    openapi_extra=PUBLIC_ROUTE,
    summary="Login with email + password",
)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_unauthenticated_db)],
) -> LoginResponse:
    """Login con email (case-insensitive) + password.

    Risposta uniforme per qualunque tipo di fallimento (utente non trovato,
    disabilitato, password sbagliata) per prevenire user enumeration via
    timing o messaggi differenti.

    Usa `get_unauthenticated_db` perché l'utente non è ancora identificato —
    RLS bloccherebbe il SELECT (vedi commento CRITICAL in db/session.py).
    """
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid credentials",
    )

    user = (
        await db.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()

    valid_credentials = (
        user is not None
        and user.is_active
        and verify_password(body.password, user.hashed_password)
    )
    if not valid_credentials:
        log.warning(
            "auth.login_failed",
            reason="invalid_credentials",
            attempted_email_hash=_email_hash(body.email),
        )
        raise invalid
    assert user is not None  # narrow type for the rest of the function

    log.info(
        "auth.login_success",
        user_id=str(user.id),
        role=user.role,
        client_id=str(user.client_id) if user.client_id is not None else None,
    )
    return _build_token_pair(user)


@router.post(
    "/refresh",
    response_model=LoginResponse,
    openapi_extra=PUBLIC_ROUTE,
    summary="Exchange refresh token for new access + refresh tokens",
)
async def refresh(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_unauthenticated_db)],
) -> LoginResponse:
    """Token rotation: ritorna NUOVI access + refresh tokens.

    Stateless: il vecchio refresh token resta tecnicamente valido fino alla
    scadenza naturale. Per invalidazione effettiva servirà la tabella
    `refresh_tokens` (Sessione 5/6) o un claim `tv` (token_version) sul user
    — vedi ADR-0003 §"Exit strategy".

    Risposta uniforme per qualunque rifiuto: 401 "refresh failed, please
    re-login" — non differenziamo i casi (token scaduto / user disattivato /
    role cambiato) per ridurre l'info disclosure.
    """
    fail = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="refresh failed, please re-login",
    )

    try:
        payload = decode_refresh_token(body.refresh_token)
    except InvalidTokenError as exc:
        log.warning("auth.refresh_failed", reason=str(exc))
        raise fail from None

    sub = payload.get("sub")
    if not isinstance(sub, str):
        log.warning("auth.refresh_failed", reason="missing_sub_claim")
        raise fail

    try:
        user_id = UUID(sub)
    except (ValueError, TypeError):
        log.warning("auth.refresh_failed", reason="invalid_sub_claim", user_id_from_token=sub)
        raise fail from None

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    if user is None:
        log.warning("auth.refresh_failed", reason="user_not_found", user_id_from_token=str(user_id))
        raise fail
    if not user.is_active:
        log.warning(
            "auth.refresh_failed",
            reason="account_disabled",
            user_id_from_token=str(user_id),
        )
        raise fail

    log.info("auth.refresh_success", user_id=str(user.id), role=user.role)
    return _build_token_pair(user)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Current user info + client (RLS-checked)",
)
async def me(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_authenticated_session)],
) -> MeResponse:
    """Info dell'utente corrente + client (se applicabile).

    Usa `get_authenticated_session` per esercitare end-to-end il contratto
    ADR-0002 nel router: il lookup del client passa per RLS, quindi un user
    di Monoloco non può vedere altri client anche se la query venisse
    manomessa per cercare un altro UUID.
    """
    log.debug("auth.me", user_id=str(user.id), role=user.role)

    client_summary: ClientSummary | None = None
    if user.client_id is not None:
        client = (
            await db.execute(select(Client).where(Client.id == user.client_id))
        ).scalar_one_or_none()
        if client is not None:
            client_summary = ClientSummary(
                id=client.id,
                name=client.name,
                slug=client.slug,
                status=client.status,
            )

    return MeResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        client_id=user.client_id,
        client=client_summary,
    )
