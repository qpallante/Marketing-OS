"""Admin router: /api/v1/admin/*

Tutti gli endpoint richiedono ruolo `super_admin` (via `require_super_admin`
dependency). Pattern famiglia `/api/v1/admin/*` consolidato in S5: parallela
a `/api/v1/auth/*` ma server-side admin operations invece di cookie ops.

Vedi ADR-0006 §"Pattern: /api/v1/admin/* family".
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_authenticated_session, require_super_admin
from app.core.security import generate_invitation_token
from app.models import Client, Invitation, User
from app.schemas.admin import (
    ClientSummary,
    CreateClientRequest,
    CreateClientResponse,
    InvitationSummary,
    ListClientsResponse,
)

log = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter()

#: TTL invitation in giorni. Hardcoded per ora; env-tunabile in S6+ se serve.
#: Coerente con refresh_token TTL dev (7 giorni). Vedi ADR-0006.
INVITATION_TTL_DAYS = 7


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _email_hash(email: str) -> str:
    """SHA-256 hex dell'email (lowercase) per logging GDPR-friendly: forensics
    senza esporre PII plaintext nei log strutturati. Vedi loginAction S3.
    """
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()


def _client_to_summary(c: Client) -> ClientSummary:
    return ClientSummary(
        id=c.id,
        name=c.name,
        slug=c.slug,
        status=c.status,
        created_at=c.created_at,
    )


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.post(
    "/clients",
    response_model=CreateClientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new client + first admin invitation",
)
async def create_client(
    body: CreateClientRequest,
    super_admin: Annotated[User, Depends(require_super_admin)],
    db: Annotated[AsyncSession, Depends(get_authenticated_session)],
) -> CreateClientResponse:
    """Crea un nuovo `Client` + una pending `Invitation` per il primo
    `client_admin` di quel client.

    Pre-checks (anti UNIQUE constraint violation con error generico 500):
      - `admin_email` non in `users` (1 email = 1 user, vedi ADR-0006
        §"Scope limitations")
      - `slug` non in `clients`

    **Pattern transazione**: la session da `get_authenticated_session` apre
    `session.begin()` come context manager auto-commit. Qui usiamo
    `session.flush()` per ottenere l'`id` generato dal DB e per scrivere
    senza chiudere la transazione. **NON** chiamare `session.commit()`
    esplicitamente: chiuderebbe la transazione prima dell'exit del context
    manager → SQLAlchemy lancia `InvalidRequestError`. Lezione consolidata
    in S5 step 2.

    Il commit avviene automaticamente quando l'handler termina e FastAPI
    consuma il generator dependency (esce dal `async with session.begin()`).

    **Logging**: niente token plaintext né `invitation_url` completo nei log.
    Solo `email_hash` (SHA-256), `invitation_id`, `client_id`, `expires_at`.
    Il plaintext esiste UNA SOLA VOLTA, nel response body. Vedi ADR-0006.
    """
    email_lower = body.admin_email.lower()

    # Pre-check 1: email globalmente unica (1 email = 1 user — scope S5)
    existing_user = (
        await db.execute(select(User).where(User.email == email_lower))
    ).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email already in use",
        )

    # Pre-check 2: slug unico
    existing_client = (
        await db.execute(select(Client).where(Client.slug == body.slug))
    ).scalar_one_or_none()
    if existing_client is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="slug already exists",
        )

    # Crea client (RLS clients_insert: super_admin only — verificato S2 + step 2)
    client = Client(name=body.name, slug=body.slug, status="active")
    db.add(client)
    await db.flush()  # genera id via gen_random_uuid()
    await db.refresh(client)

    # Genera invitation token (plaintext + SHA-256 hash)
    plaintext, token_hash = generate_invitation_token()

    invitation = Invitation(
        client_id=client.id,
        email=email_lower,
        role="client_admin",
        token_hash=token_hash,
        invited_by_user_id=super_admin.id,
        expires_at=datetime.now(UTC) + timedelta(days=INVITATION_TTL_DAYS),
    )
    db.add(invitation)
    await db.flush()
    await db.refresh(invitation)

    invitation_url = f"{settings.frontend_url}/accept-invite?token={plaintext}"

    log.info(
        "invitation_created",
        invitation_id=str(invitation.id),
        client_id=str(client.id),
        email_hash=_email_hash(email_lower),
        role=invitation.role,
        expires_at=invitation.expires_at.isoformat(),
        invited_by=str(super_admin.id),
    )

    return CreateClientResponse(
        client=_client_to_summary(client),
        invitation=InvitationSummary(
            id=invitation.id,
            email=invitation.email,
            role=invitation.role,  # CHECK constraint garantisce literal valido
            expires_at=invitation.expires_at,
            invitation_url=invitation_url,
        ),
    )


@router.get(
    "/clients",
    response_model=ListClientsResponse,
    summary="List all clients",
)
async def list_clients(
    _super_admin: Annotated[User, Depends(require_super_admin)],
    db: Annotated[AsyncSession, Depends(get_authenticated_session)],
) -> ListClientsResponse:
    """Lista tutti i client ordinati per `created_at DESC`.

    Niente paginazione per ora (assumiamo <50 client in fase 1). TODO
    pagination cursor-based quando supereremo la soglia (vedi TODO.md).
    """
    rows = (
        await db.execute(select(Client).order_by(Client.created_at.desc()))
    ).scalars().all()

    return ListClientsResponse(
        clients=[_client_to_summary(c) for c in rows],
    )
