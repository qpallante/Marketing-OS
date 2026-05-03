"""FastAPI dependencies per route autenticate.

Layering (dipendenze in ordine):

  request.state.token_payload  ←  JWTAuthMiddleware  (step 3)
            │
            ▼
  get_token_payload()          ←  raw payload o 401
            │
            ▼
  get_current_user()           ←  payload + DB lookup → User
            │
            ▼
  get_authenticated_session()  ←  user → AsyncSession con contratto ADR-0002
       require_super_admin()   ←  user con check ruolo
       require_client_admin()  ←  user con check ruolo

Il lookup user (`get_current_user`) usa **get_unauthenticated_db** perché
l'utente non è ancora identificato (chicken/egg) e RLS bloccherebbe qualsiasi
SELECT. Vedi commento CRITICAL in `app/db/session.py`.

Strict checks (OPTION A — vedi conversazione Sessione 3):
  - User esiste
  - User.is_active == True
  - Token claim 'role' == User.role nel DB
  - Token claim 'client_id' == User.client_id nel DB

Mismatch su role/client_id forza re-login (es. admin demote, ma user ha
ancora token vecchio). Costo: zero query extra (User row già caricata).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, NoReturn
from uuid import UUID

import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_authenticated_db, get_unauthenticated_db
from app.models import User

log = structlog.get_logger(__name__)


def _reject(
    reason: str,
    *,
    user_id_from_token: str | None,
    path: str,
    status_code: int = status.HTTP_401_UNAUTHORIZED,
    detail: str,
) -> NoReturn:
    """Logga warning strutturato e raise HTTPException.

    Non logga role/client_id del DB anche se diversi dal token (info disclosure).
    `from None` sopprime la chain dell'eventuale eccezione di partenza.
    """
    log.warning(
        "auth.rejected",
        reason=reason,
        user_id_from_token=user_id_from_token,
        path=path,
    )
    raise HTTPException(status_code=status_code, detail=detail) from None


async def get_token_payload(request: Request) -> dict[str, Any]:
    """Legge `request.state.token_payload` (popolato da JWTAuthMiddleware).

    Raises 401 se assente. Dependency low-level: i router useranno
    principalmente `get_current_user`, non questa direttamente.
    """
    payload: dict[str, Any] | None = getattr(request.state, "token_payload", None)
    if payload is None:
        _reject(
            "no_token",
            user_id_from_token=None,
            path=request.url.path,
            detail="authentication required",
        )
    return payload


async def get_current_user(
    request: Request,
    payload: Annotated[dict[str, Any], Depends(get_token_payload)],
    db: Annotated[AsyncSession, Depends(get_unauthenticated_db)],
) -> User:
    """Lookup User dal DB e applica strict checks (OPTION A).

    Performance: 1 query DB per request autenticata. Acceptable per ora;
    se diventa bottleneck, cache Redis con TTL breve in S5+.
    """
    sub = payload.get("sub")
    path = request.url.path

    if not isinstance(sub, str):
        _reject("missing_sub_claim", user_id_from_token=None, path=path,
                detail="authentication required")

    try:
        user_id = UUID(sub)
    except (ValueError, TypeError):
        _reject("invalid_sub_claim", user_id_from_token=sub, path=path,
                detail="authentication required")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    if user is None:
        _reject("user_not_found", user_id_from_token=str(user_id), path=path,
                detail="user not found")

    if not user.is_active:
        _reject("account_disabled", user_id_from_token=str(user_id), path=path,
                detail="account disabled")

    if payload.get("role") != user.role:
        _reject("role_changed", user_id_from_token=str(user_id), path=path,
                detail="role changed, please re-login")

    token_client_id = payload.get("client_id")
    user_client_id = str(user.client_id) if user.client_id is not None else None
    if token_client_id != user_client_id:
        _reject("client_mismatch", user_id_from_token=str(user_id), path=path,
                detail="client mismatch, please re-login")

    return user


async def get_authenticated_session(
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterator[AsyncSession]:
    """Sessione DB con contratto ADR-0002 (SET LOCAL ROLE + GUC vars).

    Wrapper FastAPI-friendly attorno a `get_authenticated_db` (db/session.py).
    Vive qui in deps.py per importare User e usare `Depends(get_current_user)`.
    """
    async for session in get_authenticated_db(user):
        yield session


async def require_super_admin(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """403 se l'utente non è super_admin. Ritorna User per chaining."""
    if user.role != "super_admin":
        _reject(
            "not_super_admin",
            user_id_from_token=str(user.id),
            path=request.url.path,
            status_code=status.HTTP_403_FORBIDDEN,
            detail="super admin required",
        )
    return user


async def require_client_admin(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """403 se l'utente non è super_admin né client_admin. Ritorna User per chaining."""
    if user.role not in ("super_admin", "client_admin"):
        _reject(
            "not_admin",
            user_id_from_token=str(user.id),
            path=request.url.path,
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return user


async def require_client_access(
    request: Request,
    client_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> UUID:
    """403 se l'utente non ha accesso al `client_id` specificato nel path.

    Usata come dependency per la route family `/api/v1/clients/{client_id}/...`
    (S7+: brand, future content/analytics/campaigns).

    Logica:
      - super_admin: accesso a TUTTI i client (per debug/onboarding cross-tenant)
      - client_admin / client_member: accesso SOLO al proprio `client_id`

    Returns:
        Il `client_id` validato (pass-through nel handler — riduce boilerplate).

    **Defense-in-depth a 3 layer** per la family `/api/v1/clients/{id}/...`:
      1. Questa dep (HTTP) — 403 prima di qualsiasi DB read
      2. RLS policy `client_id = current_app_client_id() OR is_super_admin`
         (DB) — 0 rows se HTTP layer bypassed
      3. FK CASCADE su `clients.id` — coerenza referenziale

    **No information disclosure** nel detail: "forbidden: client access denied"
    è generico, non distingue "client X esiste ma non hai accesso" da "client X
    non esiste". Pattern coerente con ADR-0007 §3 (always-404-on-invalid).
    """
    if user.role == "super_admin":
        return client_id

    if user.client_id != client_id:
        _reject(
            "client_access_denied",
            user_id_from_token=str(user.id),
            path=request.url.path,
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forbidden: client access denied",
        )
    return client_id
