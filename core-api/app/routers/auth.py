"""Authentication router: /login, /refresh, /me, /accept-invite (S6).

Mountato in main.py con `prefix="/api/v1/auth"`.

Endpoint /login, /refresh, /accept-invite, GET /invitation/{token} sono
**pubblici** (nessuna BearerAuth richiesta) — opt-out dal global security
scheme via `openapi_extra={"security": []}`.

Schemi Pydantic estratti in `app/schemas/auth.py` (S6: trigger condition
ADR-0006 §"Schema extraction" superato — 7 schemi distinti).

Vedi ADR-0003 (decisioni JWT) e ADR-0007 (flow accept-invite).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_authenticated_session, get_current_user
from app.core.invitations import (
    InvitationAcceptedError,
    InvitationError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationRevokedError,
    validate_invitation,
)
from app.core.security import (
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.db.session import get_unauthenticated_db
from app.models import Client, User
from app.schemas.auth import (
    AcceptInviteRequest,
    ClientSummary,
    InvitationPreviewResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    RefreshRequest,
)

# Mapping eccezioni invitation → (HTTP status, detail user-facing).
# Differenziato per UX: ogni 4xx suggerisce un'azione diversa al frontend
# (re-login, contatta admin, richiedi nuovo invito, ecc.). Vedi ADR-0007 §3.
INVITATION_ERROR_HTTP_MAP: dict[type[InvitationError], tuple[int, str]] = {
    InvitationNotFoundError: (status.HTTP_404_NOT_FOUND, "invitation not found"),
    InvitationRevokedError: (status.HTTP_410_GONE, "invitation revoked"),
    InvitationAcceptedError: (status.HTTP_410_GONE, "invitation already used"),
    InvitationExpiredError: (status.HTTP_410_GONE, "invitation expired"),
}

log = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter()

PUBLIC_ROUTE: dict[str, Any] = {"security": []}


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


# ─── Invitation preview (S6) ─────────────────────────────────────────────


@router.get(
    "/invitation/{token}",
    response_model=InvitationPreviewResponse,
    openapi_extra=PUBLIC_ROUTE,
    summary="Preview invitation by plaintext token (public, pre-form)",
    responses={
        404: {
            "description": "Invitation non valida (qualsiasi motivo: not found / "
            "expired / accepted / revoked). NO information disclosure.",
            "content": {"application/json": {"example": {"detail": "invitation not found"}}},
        },
    },
)
async def preview_invitation(
    token: str,
    db: Annotated[AsyncSession, Depends(get_unauthenticated_db)],
) -> InvitationPreviewResponse:
    """Preview di un'invitation token per la pagina `/accept-invite` frontend.

    **Sempre 404 generico** per qualsiasi stato invalido (not_found / expired
    / accepted / revoked). Il frontend mostra un messaggio generico tipo
    "Link non valido o scaduto. Chiedi al super_admin di rigenerarlo."
    La differenziazione del motivo arriva solo al POST `/accept-invite` (UX
    su submit > information disclosure su preview). Vedi ADR-0007 §3.

    `get_unauthenticated_db` (postgres role, RLS bypass): l'endpoint è
    pre-auth, nessun JWT. Stesso pattern di `/login` e `/refresh`.
    """
    # Token prefix per correlation log: 8 char di 43 totali = entropy
    # rimanente 35 char a 6 bit = 210 bit, no brute-force concern.
    token_prefix = token[:8] if len(token) >= 8 else token

    try:
        invitation = await validate_invitation(db, token)
    except InvitationError as exc:
        log.info(
            "auth.invitation_preview",
            success=False,
            reason=type(exc).__name__,
            token_prefix=token_prefix,
        )
        # No information disclosure: 404 generico per OGNI stato invalido.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="invitation not found",
        ) from None

    # Lookup client name (single SELECT, no lazy loading)
    client_name = (
        await db.execute(select(Client.name).where(Client.id == invitation.client_id))
    ).scalar_one()

    log.info(
        "auth.invitation_preview",
        success=True,
        token_prefix=token_prefix,
        invitation_id=str(invitation.id),
        client_id=str(invitation.client_id),
    )

    return InvitationPreviewResponse(
        email=invitation.email,
        role=invitation.role,  # CHECK constraint garantisce client_admin/member
        client_name=client_name,
        expires_at=invitation.expires_at,
    )


# ─── Accept invitation (S6) ──────────────────────────────────────────────


@router.post(
    "/accept-invite",
    response_model=LoginResponse,
    openapi_extra=PUBLIC_ROUTE,
    summary="Accept invitation: create user + auto-login",
    responses={
        404: {
            "description": "Token non corrisponde a nessuna invitation. NO information disclosure.",
            "content": {"application/json": {"example": {"detail": "invitation not found"}}},
        },
        410: {
            "description": "Invitation in stato non più utilizzabile.",
            "content": {
                "application/json": {
                    "examples": {
                        "expired": {"value": {"detail": "invitation expired"}},
                        "accepted": {"value": {"detail": "invitation already used"}},
                        "revoked": {"value": {"detail": "invitation revoked"}},
                    },
                },
            },
        },
        409: {
            "description": "Email già registrata come user (race condition).",
            "content": {
                "application/json": {
                    "example": {"detail": "user already exists with this email"},
                },
            },
        },
    },
)
async def accept_invite(
    body: AcceptInviteRequest,
    db: Annotated[AsyncSession, Depends(get_unauthenticated_db)],
) -> LoginResponse:
    """Accept invitation flow: validate token, create user, auto-login.

    **Public Transactional Handler pattern (S6, primo del progetto)**:
    `get_unauthenticated_db` apre solo la `AsyncSession` (no `begin()`).
    Apriamo la transazione esplicitamente qui con `async with db.begin():`
    per garantire atomicità validate + INSERT + UPDATE. Se uno qualsiasi
    dei tre step fallisce → rollback dell'intera transazione → stato DB
    consistente. Se in S7+ avremo un secondo handler simile (password
    reset, magic link), valuteremo `get_unauthenticated_session_tx` helper
    per evitare duplicazione del `session.begin()`. Vedi ADR-0007.

    **Race condition email already exists**: gestita via `IntegrityError`
    catch sul UNIQUE constraint `users_email_key` → 409. NO `SERIALIZABLE`
    isolation: il UNIQUE constraint è esattamente la safety net per questo
    caso, READ COMMITTED + UNIQUE è il pattern Postgres-canonico.

    **Auto-login Opzione A**: dopo il commit, ritorniamo un `LoginResponse`
    identico a `/login`. Il frontend setta i cookies + redirect /dashboard,
    minimizzando friction al primo onboarding. Vedi ADR-0007 §2.

    **Niente plaintext nel log**: solo `email_hash` (SHA-256), `token_prefix`
    (8 char per correlation), UUID. Mai password, mai token completo, mai
    email plaintext.
    """
    token_prefix = body.token[:8] if len(body.token) >= 8 else body.token

    async with db.begin():
        try:
            invitation = await validate_invitation(db, body.token)
        except InvitationError as exc:
            code, detail = INVITATION_ERROR_HTTP_MAP[type(exc)]
            log.warning(
                "auth.invitation_accept_failed",
                reason=type(exc).__name__,
                token_prefix=token_prefix,
            )
            raise HTTPException(status_code=code, detail=detail) from None

        new_user = User(
            email=invitation.email,
            hashed_password=hash_password(body.password),
            role=invitation.role,
            client_id=invitation.client_id,
            is_active=True,
        )
        db.add(new_user)
        try:
            await db.flush()  # genera id + esercita UNIQUE users.email
        except IntegrityError as exc:
            # UNIQUE constraint violation su users_email_key (l'unico realistico
            # qui — gli altri CHECK sono pre-validati da Pydantic). Race-safe
            # senza SERIALIZABLE: il constraint è la safety net.
            log.warning(
                "auth.invitation_accept_failed",
                reason="user_email_already_exists",
                token_prefix=token_prefix,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="user already exists with this email",
            ) from exc
        await db.refresh(new_user)

        invitation.accepted_at = datetime.now(UTC)
        invitation.accepted_by_user_id = new_user.id
        await db.flush()
        # Commit avviene all'exit del `async with db.begin()` context manager.

    log.info(
        "auth.invitation_accepted",
        invitation_id=str(invitation.id),
        new_user_id=str(new_user.id),
        client_id=str(invitation.client_id),
        role=invitation.role,
        email_hash=_email_hash(invitation.email),
    )

    return _build_token_pair(new_user)
