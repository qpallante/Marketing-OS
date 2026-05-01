"""Database session management.

Esporta:
  - `engine` e `async_session_factory`: per non-FastAPI callers (script, alembic,
    test fixture). NON applicano contratto ADR-0002 — accedono come `postgres`
    superuser.
  - `get_unauthenticated_db()`: async generator per FastAPI Depends, RLS bypassed.
  - `get_authenticated_db(user)`: async generator che applica contratto ADR-0002
    (SET LOCAL ROLE authenticated + set_config GUC vars) per ogni request autenticata.

L'integrazione FastAPI (wiring con `Depends(get_current_user)`) vive in
`app.core.deps`, per evitare l'import circolare con `core.security`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.models.user import User

log = structlog.get_logger(__name__)
settings = get_settings()

engine = create_async_engine(
    str(settings.database_url),
    echo=False,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_unauthenticated_db() -> AsyncIterator[AsyncSession]:
    """Sessione DB **senza** enforcement RLS — connessione come `postgres` (superuser).

    CRITICAL: usare SOLO per gli endpoint dove l'utente non è ancora identificato:
      - POST /api/v1/auth/login (ricerca user per email per validare la password)
      - POST /api/v1/auth/refresh (lookup user per ricavare client_id/role aggiornati)

    Per QUALSIASI endpoint autenticato di business usare `get_authenticated_db`.
    Una `SELECT * FROM users` qui ignora silenziosamente RLS — esattamente il
    comportamento che vogliamo evitare in tutti i path autenticati.

    Vedi ADR-0002 §"Contratto del middleware FastAPI" e §"Connessioni
    amministrative bypassano RLS" per la motivazione.
    """
    async with async_session_factory() as session:
        yield session


async def get_authenticated_db(user: User) -> AsyncIterator[AsyncSession]:
    """Sessione DB con enforcement RLS via contratto ADR-0002.

    All'apertura della transazione esegue, nell'ordine:
      1. ``SET LOCAL ROLE authenticated`` — fondamentale: il role `postgres` è
         superuser e bypasserebbe RLS. Solo `authenticated` (non superuser)
         applica le policy.
      2. ``set_config('app.current_client_id', user.client_id, true)`` se
         l'utente ha un client_id (cioè è client_admin / client_member).
      3. ``set_config('app.is_super_admin', 'true', true)`` se l'utente è
         super_admin (bypass cross-tenant via flag — vedi ADR-0002).

    `set_config(name, value, is_local)` viene preferito a `SET LOCAL "name" = ...`
    perché supporta parameter binding sicuro contro SQL injection.

    Logga a livello DEBUG i parametri del setting per facilitare debug di
    anomalie RLS in dev (in produzione il livello INFO filtra il log;
    `request_id` viene merged automaticamente da structlog se un middleware
    upstream lo ha bound nei contextvars).
    """
    # SET LOCAL richiede una transazione attiva (sessione + begin combinati)
    async with async_session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))

        client_id_str = str(user.client_id) if user.client_id is not None else None
        is_super = user.role == "super_admin"

        if client_id_str is not None:
            await session.execute(
                text("SELECT set_config('app.current_client_id', :v, true)"),
                {"v": client_id_str},
            )
        if is_super:
            await session.execute(
                text("SELECT set_config('app.is_super_admin', 'true', true)"),
            )

        log.debug(
            "db.authenticated_session",
            user_id=str(user.id),
            client_id=client_id_str,
            is_super_admin=is_super,
        )
        yield session
