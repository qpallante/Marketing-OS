"""Smoke test Sessione 5 — admin clients endpoints + invitations.

Verifica end-to-end le invarianti security/correctness introdotte in S5:

  1. Schema: tabella `invitations` + UNIQUE PARTIAL INDEX presente
  2. RLS: super_admin context può INSERT/SELECT su `invitations` e `clients`
  3. RLS: client_admin context NON può INSERT su `invitations` (S5 baseline)
  4. RLS: client_admin context NON può INSERT su `clients`
  5. UNIQUE PARTIAL INDEX: 2 invitation pending stessa (client_id, email) → 2° fail
  6. UNIQUE PARTIAL INDEX: 1 accepted + 1 nuova pending stessa coppia → entrambi OK
  7. FK CASCADE: DELETE clients → invitations sparite
  8. CHECK email lowercase: INSERT con email mixed-case → constraint violation
  9. Endpoint role enforcement: POST /admin/clients con client_admin Bearer → 403

Pulisce dopo sé. Idempotente (pre-cleanup di slug `smoke-s5-*`).

Run with:
    poetry run python scripts/smoke_test_session5.py

Output: 9 paranoid checks principali con sub-asserzioni nidificate (~26 totali).
Exit 0 se tutti pass, exit 1 con count di failure altrimenti.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import sys
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.db.session import async_session_factory

OK_ICON = "✅"
FAIL_ICON = "❌"
SLUG_PREFIX = "smoke-s5"

# Backend test target: stesso :8001 letto da .env
SETTINGS = get_settings()
BACKEND_URL = "http://localhost:8001"

# Seed credentials (vedi scripts/seed_dev.py). Hardcoded — sono dev-only,
# generate da seed locale, non sono "secrets" in senso operativo.
SUPER_ADMIN_EMAIL = "admin@marketing-os.example"
SUPER_ADMIN_PASS = "-5wROzbHtIFBACicZJmukA"  # noqa: S105 — dev seed cred
MONOLOCO_ADMIN_EMAIL = "admin@monoloco.example"
MONOLOCO_ADMIN_PASS = "epY_6eUdNkIcErdwAAD76Q"  # noqa: S105 — dev seed cred


# ─── Helpers ────────────────────────────────────────────────────────────────


def step(label: str) -> None:
    print(f"\n[{label}]")


def check(label: str, *, ok: bool, detail: str = "") -> bool:
    icon = OK_ICON if ok else FAIL_ICON
    line = f"  {icon} {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


async def _set_super_admin_context(conn) -> None:
    await conn.execute(text("SET LOCAL ROLE authenticated"))
    await conn.execute(text("SELECT set_config('app.is_super_admin', 'true', true)"))


async def _set_client_admin_context(conn, client_id: str) -> None:
    await conn.execute(text("SET LOCAL ROLE authenticated"))
    await conn.execute(
        text("SELECT set_config('app.current_client_id', :v, true)"),
        {"v": client_id},
    )


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


async def _get_monoloco_client_id() -> str:
    """Recupera l'UUID del client Monoloco dal seed."""
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        res = await session.execute(text("SELECT id FROM clients WHERE slug = 'monoloco'"))
        return str(res.scalar_one())


async def _pre_cleanup() -> None:
    """Idempotency: rimuovi smoke-s5-* clients da run precedenti."""
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        await session.execute(
            text("DELETE FROM clients WHERE slug LIKE :prefix"),
            {"prefix": f"{SLUG_PREFIX}-%"},
        )


async def _login_for_token(email: str, password: str) -> str:
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10.0) as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


# ─── Paranoid checks ────────────────────────────────────────────────────────


async def check_1_schema_present() -> int:
    step("1. Schema sanity: tabella invitations + indice partial unique")
    fails = 0
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)

        res = await session.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='invitations'"
            )
        )
        if not check("invitations table exists", ok=res.scalar() == 1):
            fails += 1

        res = await session.execute(
            text(
                "SELECT count(*) FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='invitations' "
                "AND indexdef LIKE '%accepted_at IS NULL%' "
                "AND indexdef LIKE '%revoked_at IS NULL%'"
            )
        )
        if not check("UNIQUE PARTIAL INDEX (pending only) present", ok=res.scalar() == 1):
            fails += 1

        # CHECK constraint on email lowercase. Query pg_constraint directly:
        # information_schema.check_constraints non è visibile al role
        # `authenticated` (privilegi limitati), pg_get_constraintdef sì.
        res = await session.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = 'invitations'::regclass "
                "AND contype = 'c' "
                "AND pg_get_constraintdef(oid) LIKE '%lower%'"
            )
        )
        if not check("CHECK constraint email lowercase present", ok=(res.scalar() or 0) >= 1):
            fails += 1
    return fails


async def check_2_super_admin_can_insert() -> int:
    step("2. RLS: super_admin context può INSERT clients + invitations")
    fails = 0
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)

        slug = f"{SLUG_PREFIX}-test-2"
        res = await session.execute(
            text(
                "INSERT INTO clients (name, slug, status) "
                "VALUES (:n, :s, 'active') RETURNING id"
            ),
            {"n": "Smoke S5 Two", "s": slug},
        )
        client_id = res.scalar_one()
        if not check(
            "INSERT clients (super_admin context) succeeds", ok=client_id is not None
        ):
            fails += 1

        # Invitation insert
        plaintext = secrets.token_urlsafe(32)
        await session.execute(
            text(
                "INSERT INTO invitations "
                "(client_id, email, role, token_hash, expires_at) "
                "VALUES (:c, :e, 'client_admin', :h, NOW() + INTERVAL '7 days')"
            ),
            {"c": client_id, "e": "smoke-2@example.com", "h": _hash(plaintext)},
        )
        # If we reached here without exception, the INSERT was allowed by RLS.
        check("INSERT invitations (super_admin context) succeeds", ok=True)

        # SELECT verifica
        res = await session.execute(
            text("SELECT count(*) FROM invitations WHERE client_id = :c"),
            {"c": client_id},
        )
        if not check("SELECT invitations (super_admin context) returns row", ok=res.scalar() == 1):
            fails += 1
    return fails


async def check_3_client_admin_cannot_insert_invitations() -> int:
    step("3. RLS: client_admin context NON può INSERT invitations (S5 baseline)")
    fails = 0
    monoloco_id = await _get_monoloco_client_id()
    plaintext = secrets.token_urlsafe(32)

    raised = False
    async with async_session_factory() as session, session.begin():
        await _set_client_admin_context(session, monoloco_id)
        try:
            await session.execute(
                text(
                    "INSERT INTO invitations "
                    "(client_id, email, role, token_hash, expires_at) "
                    "VALUES (:c, :e, 'client_admin', :h, NOW() + INTERVAL '7 days')"
                ),
                {"c": monoloco_id, "e": "smoke-3@example.com", "h": _hash(plaintext)},
            )
        except DBAPIError as exc:
            raised = True
            if not check(
                "RLS blocks INSERT (DBAPIError)",
                ok="row-level security" in str(exc).lower()
                or "insufficient privilege" in str(exc).lower()
                or "permission denied" in str(exc).lower()
                or "new row violates row-level security policy" in str(exc).lower(),
                detail=str(exc).splitlines()[0][:120],
            ):
                fails += 1
    if not raised:
        fails += 1
        check("RLS blocks INSERT", ok=False, detail="no exception raised — POLICY MISSING")
    return fails


async def check_4_client_admin_cannot_insert_clients() -> int:
    step("4. RLS: client_admin context NON può INSERT clients")
    fails = 0
    monoloco_id = await _get_monoloco_client_id()

    raised = False
    async with async_session_factory() as session, session.begin():
        await _set_client_admin_context(session, monoloco_id)
        try:
            await session.execute(
                text(
                    "INSERT INTO clients (name, slug, status) "
                    "VALUES (:n, :s, 'active')"
                ),
                {"n": "Smoke S5 Four", "s": f"{SLUG_PREFIX}-test-4"},
            )
        except DBAPIError:
            raised = True
            check("RLS blocks INSERT clients (client_admin context)", ok=True)
    if not raised:
        fails += 1
        check(
            "RLS blocks INSERT clients",
            ok=False,
            detail="client_admin INSERTed a client — POLICY MISSING",
        )
    return fails


async def check_5_partial_index_blocks_double_pending() -> int:
    step("5. UNIQUE PARTIAL INDEX: 2 pending invitations stessa (client_id, email) → 2° fail")
    fails = 0

    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        slug = f"{SLUG_PREFIX}-test-5"
        res = await session.execute(
            text(
                "INSERT INTO clients (name, slug, status) "
                "VALUES (:n, :s, 'active') RETURNING id"
            ),
            {"n": "Smoke S5 Five", "s": slug},
        )
        cid = res.scalar_one()
        email = "smoke-5@example.com"

        # 1° pending
        await session.execute(
            text(
                "INSERT INTO invitations "
                "(client_id, email, role, token_hash, expires_at) "
                "VALUES (:c, :e, 'client_admin', :h, NOW() + INTERVAL '7 days')"
            ),
            {"c": cid, "e": email, "h": _hash(secrets.token_urlsafe(32))},
        )
        check("1st pending invitation OK", ok=True)

    # 2° pending (NEW transaction so the first INSERT is committed)
    raised = False
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        try:
            await session.execute(
                text(
                    "INSERT INTO invitations "
                    "(client_id, email, role, token_hash, expires_at) "
                    "VALUES (:c, :e, 'client_admin', :h, NOW() + INTERVAL '7 days')"
                ),
                {
                    "c": (
                        await session.execute(
                            text("SELECT id FROM clients WHERE slug = :s"),
                            {"s": f"{SLUG_PREFIX}-test-5"},
                        )
                    ).scalar_one(),
                    "e": email,
                    "h": _hash(secrets.token_urlsafe(32)),
                },
            )
        except (IntegrityError, DBAPIError) as exc:
            raised = True
            if not check(
                "2nd pending blocked by UNIQUE PARTIAL INDEX",
                ok="duplicate" in str(exc).lower() or "unique" in str(exc).lower(),
                detail=str(exc).splitlines()[0][:120],
            ):
                fails += 1
    if not raised:
        fails += 1
        check("2nd pending blocked", ok=False, detail="duplicate accepted — INDEX MISSING")
    return fails


async def check_6_partial_index_allows_after_accept() -> int:
    step("6. UNIQUE PARTIAL INDEX: accepted + new pending stessa coppia → entrambi OK")
    fails = 0
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        slug = f"{SLUG_PREFIX}-test-6"
        res = await session.execute(
            text(
                "INSERT INTO clients (name, slug, status) "
                "VALUES (:n, :s, 'active') RETURNING id"
            ),
            {"n": "Smoke S5 Six", "s": slug},
        )
        cid = res.scalar_one()
        email = "smoke-6@example.com"

        # Accepted invitation (one-shot INSERT con accepted_at != NULL).
        await session.execute(
            text(
                "INSERT INTO invitations "
                "(client_id, email, role, token_hash, expires_at, accepted_at) "
                "VALUES (:c, :e, 'client_admin', :h, "
                "NOW() + INTERVAL '7 days', NOW())"
            ),
            {"c": cid, "e": email, "h": _hash(secrets.token_urlsafe(32))},
        )
        check("accepted invitation INSERT OK", ok=True)

        # New pending — same (client_id, email)
        try:
            await session.execute(
                text(
                    "INSERT INTO invitations "
                    "(client_id, email, role, token_hash, expires_at) "
                    "VALUES (:c, :e, 'client_admin', :h, NOW() + INTERVAL '7 days')"
                ),
                {"c": cid, "e": email, "h": _hash(secrets.token_urlsafe(32))},
            )
            check("new pending after accepted: PARTIAL INDEX allows", ok=True)
        except (IntegrityError, DBAPIError) as exc:
            fails += 1
            check(
                "new pending after accepted blocked",
                ok=False,
                detail=f"PARTIAL INDEX too strict: {exc!s}",
            )
    return fails


async def check_7_fk_cascade_clients_to_invitations() -> int:
    step("7. FK CASCADE: DELETE clients → invitations sparite")
    fails = 0
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        slug = f"{SLUG_PREFIX}-test-7"
        res = await session.execute(
            text(
                "INSERT INTO clients (name, slug, status) "
                "VALUES (:n, :s, 'active') RETURNING id"
            ),
            {"n": "Smoke S5 Seven", "s": slug},
        )
        cid = res.scalar_one()

        await session.execute(
            text(
                "INSERT INTO invitations "
                "(client_id, email, role, token_hash, expires_at) "
                "VALUES (:c, :e, 'client_admin', :h, NOW() + INTERVAL '7 days')"
            ),
            {"c": cid, "e": "smoke-7@example.com", "h": _hash(secrets.token_urlsafe(32))},
        )

    # Separate transaction for the DELETE
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        cid = (
            await session.execute(
                text("SELECT id FROM clients WHERE slug = :s"),
                {"s": f"{SLUG_PREFIX}-test-7"},
            )
        ).scalar_one()
        await session.execute(text("DELETE FROM clients WHERE id = :c"), {"c": cid})

    # Verify cascade
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        n = (
            await session.execute(
                text("SELECT count(*) FROM invitations WHERE client_id = :c"),
                {"c": cid},
            )
        ).scalar()
        if not check("invitations CASCADEd (count=0)", ok=n == 0, detail=f"count={n}"):
            fails += 1
    return fails


async def check_8_email_lowercase_check() -> int:
    step("8. CHECK email lowercase: INSERT con email Mixed-Case → fail")
    fails = 0
    monoloco_id = await _get_monoloco_client_id()

    raised = False
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        try:
            await session.execute(
                text(
                    "INSERT INTO invitations "
                    "(client_id, email, role, token_hash, expires_at) "
                    "VALUES (:c, :e, 'client_admin', :h, NOW() + INTERVAL '7 days')"
                ),
                {
                    "c": monoloco_id,
                    "e": "Mixed@CASE.example",
                    "h": _hash(secrets.token_urlsafe(32)),
                },
            )
        except (IntegrityError, DBAPIError) as exc:
            raised = True
            if not check(
                "CHECK constraint blocks mixed-case email",
                ok="check" in str(exc).lower() or "violates" in str(exc).lower(),
                detail=str(exc).splitlines()[0][:120],
            ):
                fails += 1
    if not raised:
        fails += 1
        check(
            "CHECK constraint blocks mixed-case email",
            ok=False,
            detail="mixed case accepted — CHECK MISSING",
        )
    return fails


async def check_9_endpoint_role_enforcement() -> int:
    step("9. Endpoint POST /api/v1/admin/clients: client_admin Bearer → 403")
    fails = 0

    try:
        token = await _login_for_token(MONOLOCO_ADMIN_EMAIL, MONOLOCO_ADMIN_PASS)
    except httpx.HTTPError as exc:
        fails += 1
        check("login client_admin", ok=False, detail=f"{exc!s}")
        return fails
    check("login client_admin → token", ok=bool(token))

    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10.0) as client:
        resp = await client.post(
            "/api/v1/admin/clients",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Should Not Be Created",
                "slug": f"{SLUG_PREFIX}-test-9",
                "admin_email": "smoke-9@example.com",
            },
        )
    if not check("status 403", ok=resp.status_code == 403, detail=f"got {resp.status_code}"):
        fails += 1
    if not check(
        "detail mentions super admin",
        ok="super" in resp.text.lower() and "admin" in resp.text.lower(),
        detail=f"body={resp.text[:120]}",
    ):
        fails += 1
    return fails


# ─── Final cleanup ──────────────────────────────────────────────────────────


async def _post_cleanup() -> None:
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        await session.execute(
            text("DELETE FROM clients WHERE slug LIKE :prefix"),
            {"prefix": f"{SLUG_PREFIX}-%"},
        )


# ─── Driver ─────────────────────────────────────────────────────────────────


async def main() -> int:
    print("=" * 60)
    print("Sessione 5 — Admin/Invitations paranoid smoke test")
    print(f"DB: {SETTINGS.database_url[:60]}…")
    print(f"Backend: {BACKEND_URL}")
    print("=" * 60)

    await _pre_cleanup()

    fails = 0
    fails += await check_1_schema_present()
    fails += await check_2_super_admin_can_insert()
    fails += await check_3_client_admin_cannot_insert_invitations()
    fails += await check_4_client_admin_cannot_insert_clients()
    fails += await check_5_partial_index_blocks_double_pending()
    fails += await check_6_partial_index_allows_after_accept()
    fails += await check_7_fk_cascade_clients_to_invitations()
    fails += await check_8_email_lowercase_check()
    fails += await check_9_endpoint_role_enforcement()

    await _post_cleanup()

    print()
    print("=" * 60)
    if fails == 0:
        print(f"{OK_ICON} 9/9 paranoid checks pass")
        return 0
    print(f"{FAIL_ICON} {fails} sub-asserzioni fallite")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
