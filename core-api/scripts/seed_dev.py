"""Seed dev: super_admin user + Monoloco client + Monoloco client_admin user.

Idempotente: skip se i record esistono già (matching su email/slug).
Le password sono generate con secrets.token_urlsafe(16) e stampate UNA SOLA VOLTA.
Usa SET LOCAL "app.is_super_admin" = 'true' per bypassare RLS durante il seed.

Run with:
    poetry run python scripts/seed_dev.py
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path
from uuid import UUID

# Ensure core-api/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.models import Client, User


def _hash_password(plain: str) -> str:
    """bcrypt hash — direct API to avoid passlib<>bcrypt 4.x incompat."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

SUPER_ADMIN_EMAIL = "admin@marketing-os.local"
MONOLOCO_SLUG = "monoloco"
MONOLOCO_NAME = "Monoloco"
MONOLOCO_ADMIN_EMAIL = "admin@monoloco.local"


async def _ensure_super_admin(session: AsyncSession) -> tuple[User, str | None]:
    existing = (
        await session.execute(select(User).where(User.email == SUPER_ADMIN_EMAIL))
    ).scalar_one_or_none()
    if existing:
        return existing, None
    password = secrets.token_urlsafe(16)
    user = User(
        email=SUPER_ADMIN_EMAIL,
        hashed_password=_hash_password(password),
        role="super_admin",
        client_id=None,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user, password


async def _ensure_client(session: AsyncSession) -> tuple[Client, bool]:
    existing = (
        await session.execute(select(Client).where(Client.slug == MONOLOCO_SLUG))
    ).scalar_one_or_none()
    if existing:
        return existing, False
    client = Client(name=MONOLOCO_NAME, slug=MONOLOCO_SLUG, status="active")
    session.add(client)
    await session.flush()
    return client, True


async def _ensure_client_admin(
    session: AsyncSession,
    client_id: UUID,
) -> tuple[User, str | None]:
    existing = (
        await session.execute(select(User).where(User.email == MONOLOCO_ADMIN_EMAIL))
    ).scalar_one_or_none()
    if existing:
        return existing, None
    password = secrets.token_urlsafe(16)
    user = User(
        email=MONOLOCO_ADMIN_EMAIL,
        hashed_password=_hash_password(password),
        role="client_admin",
        client_id=client_id,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user, password


def _print_banner(title: str) -> None:
    bar = "=" * 72
    print(bar)
    print(title)
    print(bar)


async def main() -> int:
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url))

    async with engine.begin() as conn:
        # Bypass RLS for the duration of this transaction.
        await conn.execute(text("SET LOCAL \"app.is_super_admin\" = 'true'"))

        async with AsyncSession(bind=conn, expire_on_commit=False) as session:
            super_admin, super_pwd = await _ensure_super_admin(session)
            client, client_created = await _ensure_client(session)
            client_admin, client_admin_pwd = await _ensure_client_admin(session, client.id)
            await session.flush()

    await engine.dispose()

    _print_banner("SEED DEV — credenziali (DEV ONLY, MAI in produzione)")
    print()
    print("[super_admin]")
    print(f"  id:       {super_admin.id}")
    print(f"  email:    {super_admin.email}")
    if super_pwd:
        print(f"  password: {super_pwd}")
    else:
        print("  password: (already existed — non rigenerata)")
    print(f"  role:     {super_admin.role}")
    print()
    print(f"[client] {MONOLOCO_NAME}  (created={'yes' if client_created else 'already existed'})")
    print(f"  id:       {client.id}")
    print(f"  slug:     {client.slug}")
    print(f"  status:   {client.status}")
    print()
    print(f"[client_admin] {MONOLOCO_NAME}")
    print(f"  id:       {client_admin.id}")
    print(f"  email:    {client_admin.email}")
    if client_admin_pwd:
        print(f"  password: {client_admin_pwd}")
    else:
        print("  password: (already existed — non rigenerata)")
    print(f"  role:     {client_admin.role}")
    print(f"  client:   {MONOLOCO_NAME} ({client.id})")
    print()
    if super_pwd or client_admin_pwd:
        print(
            "Le password sono persistite in DB come bcrypt hash. "
            "Salvale ORA — non saranno mostrate di nuovo."
        )
    else:
        print(
            "Tutti i record esistevano già. "
            "Per rigenerare password, eliminare manualmente da DB e ri-eseguire."
        )
    print("=" * 72)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
