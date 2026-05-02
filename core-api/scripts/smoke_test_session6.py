"""Smoke test E2E per Sessione 6 — Accept invitation flow.

Testa l'intero flusso accept-invite end-to-end:
- `GET /api/v1/auth/invitation/{token}` (preview, public, always-404-on-invalid)
- `POST /api/v1/auth/accept-invite` (create user + auto-login)
- Schema invitation con `accepted_by_user_id` linkage (migration 0003 di S6)

Eseguibile standalone:
    poetry run python scripts/smoke_test_session6.py

Richiede: backend `:8001` attivo, super_admin seedato
(`admin@marketing-os.example`).

Pattern: complementare a `smoke_test_session5.py` (S5 admin endpoints).
Non sostituisce S5: i test possono essere lanciati indipendentemente.

9 paranoid checks (≈30 sub-asserzioni nidificate):

  1. HAPPY PATH FULL FLOW (preview → accept → /me → re-login + linkage DB)
  2. TOKEN ALREADY ACCEPTED → 410 (replay del token di test 1)
  3. TOKEN REVOKED → 410 "invitation revoked"
  4. TOKEN EXPIRED → 410 "invitation expired"
  5. TOKEN NOT FOUND → 404 "invitation not found"
  6. PASSWORD WEAK → 422 (detail menziona password + length)
  7. EMAIL RACE CONDITION → 409 "user already exists with this email"
  8. ACCEPTED_BY_USER_ID LINKAGE (FK puntata correttamente al new_user.id)
  9. GET PREVIEW NO INFORMATION DISCLOSURE (4 stati invalidi → body byte-identical)

Pulisce dopo sé. Idempotente (pre-cleanup di slug `smoke-s6-*`).
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

import httpx
from jose import jwt
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.db.session import async_session_factory

OK_ICON = "✅"
FAIL_ICON = "❌"
SLUG_PREFIX = "smoke-s6"

SETTINGS = get_settings()
BACKEND_URL = "http://localhost:8001"

# Seed credentials (vedi scripts/seed_dev.py). Hardcoded — dev-only.
SUPER_ADMIN_EMAIL = "admin@marketing-os.example"
SUPER_ADMIN_PASS = "-5wROzbHtIFBACicZJmukA"  # noqa: S105 — dev seed cred


# ─── Helpers (consistent with smoke_test_session5.py) ───────────────────────


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


async def _pre_cleanup() -> None:
    """Idempotency: rimuovi smoke-s6-* clients + users orfani da run precedenti."""
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        # CASCADE: clients delete propaga a invitations + users (FK ON DELETE CASCADE)
        await session.execute(
            text("DELETE FROM clients WHERE slug LIKE :prefix"),
            {"prefix": f"{SLUG_PREFIX}-%"},
        )
        # Cleanup users orfani con email del nostro pattern (race test pre-creates user)
        await session.execute(
            text("DELETE FROM users WHERE email LIKE :prefix"),
            {"prefix": f"{SLUG_PREFIX}-%"},
        )


async def _login_super_admin() -> str:
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10.0) as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": SUPER_ADMIN_EMAIL, "password": SUPER_ADMIN_PASS},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def _create_invitation(
    sa_jwt: str,
    slug: str,
    email: str,
    name: str,
) -> tuple[str, str, str]:
    """Crea client + invitation via POST /admin/clients.

    Ritorna (token_plaintext, invitation_id, client_id).
    """
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10.0) as client:
        resp = await client.post(
            "/api/v1/admin/clients",
            headers={"Authorization": f"Bearer {sa_jwt}"},
            json={"name": name, "slug": slug, "admin_email": email},
        )
        resp.raise_for_status()
        body = resp.json()
        invitation_url: str = body["invitation"]["invitation_url"]
        token = invitation_url.split("token=", 1)[1]
        return token, body["invitation"]["id"], body["client"]["id"]


async def _post_accept_invite(token: str, password: str) -> tuple[int, dict]:
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10.0) as client:
        resp = await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": token, "password": password},
        )
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, {}


async def _get_invitation_preview(token: str) -> tuple[int, str]:
    """Ritorna (status_code, raw_body_text) per byte-identity check."""
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10.0) as client:
        resp = await client.get(f"/api/v1/auth/invitation/{token}")
        return resp.status_code, resp.text


async def _login(email: str, password: str) -> int:
    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10.0) as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        return resp.status_code


# ─── Paranoid checks ────────────────────────────────────────────────────────


async def check_1_happy_path(  # noqa: PLR0912, PLR0915 — long sequential e2e check, intentional
    sa_jwt: str,
) -> tuple[int, str | None, str | None]:
    """Returns (fails, token_a, invitation_a_id) for use by check 2 + 8."""
    step("1. HAPPY PATH: super_admin crea → preview → accept → /me + re-login + linkage")
    fails = 0

    slug = f"{SLUG_PREFIX}-1-happy"
    email = f"{SLUG_PREFIX}-1-happy@example.com"
    name = "Smoke S6 Happy"
    password = "ValidSmokeS6Password"  # noqa: S105 — test fixture

    token, invitation_id, client_id = await _create_invitation(sa_jwt, slug, email, name)
    if not check("client + invitation creati (token len=43)", ok=len(token) == 43):
        return 1, None, None

    # 1.1 Preview
    preview_code, preview_text = await _get_invitation_preview(token)
    import json as _json

    preview = _json.loads(preview_text) if preview_code == 200 else {}
    if not check("(1.1) preview status 200", ok=preview_code == 200):
        fails += 1
    if not check(
        "(1.2) preview email match",
        ok=preview.get("email") == email,
        detail=str(preview.get("email")),
    ):
        fails += 1
    if not check(
        "(1.3) preview role=client_admin",
        ok=preview.get("role") == "client_admin",
    ):
        fails += 1
    if not check(
        "(1.4) preview client_name match",
        ok=preview.get("client_name") == name,
    ):
        fails += 1

    # 1.2 Accept
    accept_code, accept_body = await _post_accept_invite(token, password)
    if not check("(1.5) accept status 200", ok=accept_code == 200):
        fails += 1
    if not check("(1.6) access_token present", ok=bool(accept_body.get("access_token"))):
        fails += 1
    if not check("(1.7) refresh_token present", ok=bool(accept_body.get("refresh_token"))):
        fails += 1
    if not check(
        "(1.8) token_type=bearer", ok=accept_body.get("token_type") == "bearer"
    ):
        fails += 1
    if not check(
        "(1.9) expires_in=3600", ok=accept_body.get("expires_in") == 3600
    ):
        fails += 1

    # 1.3 Decode JWT claims (unverified — read-only inspection)
    new_user_id: str | None = None
    if accept_body.get("access_token"):
        claims = jwt.get_unverified_claims(accept_body["access_token"])
        new_user_id = claims.get("sub")
        if not check(
            "(1.10) JWT role=client_admin",
            ok=claims.get("role") == "client_admin",
        ):
            fails += 1
        if not check(
            "(1.11) JWT client_id matches client",
            ok=claims.get("client_id") == client_id,
        ):
            fails += 1
        if not check("(1.12) JWT type=access", ok=claims.get("type") == "access"):
            fails += 1

    # 1.4 Re-login with email + password (trust check end-to-end)
    relogin_code = await _login(email, password)
    if not check("(1.13) re-login con email+password → 200", ok=relogin_code == 200):
        fails += 1

    # 1.5 DB linkage check: invitation.accepted_at + accepted_by_user_id
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        row = (
            await session.execute(
                text(
                    "SELECT accepted_at, accepted_by_user_id FROM invitations "
                    "WHERE id = :id"
                ),
                {"id": invitation_id},
            )
        ).first()
        if row is None:
            check("(1.14) DB row found", ok=False)
            fails += 1
        else:
            accepted_at, accepted_by = row
            if not check("(1.14) accepted_at populated", ok=accepted_at is not None):
                fails += 1
            if not check(
                "(1.15) accepted_by_user_id = new_user.id (migration 0003 link)",
                ok=str(accepted_by) == new_user_id,
                detail=f"db={accepted_by} jwt_sub={new_user_id}",
            ):
                fails += 1

    return fails, token, invitation_id


async def check_2_replay(token_a: str | None) -> int:
    step("2. REPLAY: stesso token già accettato in (1) → 410 'invitation already used'")
    if token_a is None:
        check("token_a non disponibile da (1)", ok=False)
        return 1
    fails = 0
    code, body = await _post_accept_invite(token_a, "AnotherValidPassword12")
    if not check("(2.1) status 410", ok=code == 410):
        fails += 1
    if not check(
        "(2.2) detail = invitation already used",
        ok=body.get("detail") == "invitation already used",
        detail=str(body.get("detail")),
    ):
        fails += 1
    return fails


async def check_3_revoked(sa_jwt: str) -> int:
    step("3. REVOKED: UPDATE revoked_at = NOW() → 410 'invitation revoked'")
    fails = 0
    slug = f"{SLUG_PREFIX}-3-revoked"
    email = f"{SLUG_PREFIX}-3-revoked@example.com"
    token, invitation_id, _ = await _create_invitation(sa_jwt, slug, email, "Smoke S6 Revoked")

    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        await session.execute(
            text("UPDATE invitations SET revoked_at = NOW() WHERE id = :id"),
            {"id": invitation_id},
        )

    code, body = await _post_accept_invite(token, "ValidSmokeS6Password")
    if not check("(3.1) status 410", ok=code == 410):
        fails += 1
    if not check(
        "(3.2) detail = invitation revoked",
        ok=body.get("detail") == "invitation revoked",
    ):
        fails += 1
    return fails


async def check_4_expired(sa_jwt: str) -> int:
    step("4. EXPIRED: UPDATE expires_at past → 410 'invitation expired'")
    fails = 0
    slug = f"{SLUG_PREFIX}-4-expired"
    email = f"{SLUG_PREFIX}-4-expired@example.com"
    token, invitation_id, _ = await _create_invitation(sa_jwt, slug, email, "Smoke S6 Expired")

    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        await session.execute(
            text(
                "UPDATE invitations SET expires_at = NOW() - INTERVAL '1 day' "
                "WHERE id = :id"
            ),
            {"id": invitation_id},
        )

    code, body = await _post_accept_invite(token, "ValidSmokeS6Password")
    if not check("(4.1) status 410", ok=code == 410):
        fails += 1
    if not check(
        "(4.2) detail = invitation expired",
        ok=body.get("detail") == "invitation expired",
    ):
        fails += 1
    return fails


async def check_5_not_found() -> int:
    step("5. NOT FOUND: random 43-char token (no DB row) → 404")
    fails = 0
    random_token = secrets.token_urlsafe(32)  # esattamente 43 char
    code, body = await _post_accept_invite(random_token, "ValidSmokeS6Password")
    if not check("(5.1) status 404", ok=code == 404):
        fails += 1
    if not check(
        "(5.2) detail = invitation not found",
        ok=body.get("detail") == "invitation not found",
    ):
        fails += 1
    return fails


async def check_6_password_weak(sa_jwt: str) -> int:
    step("6. PASSWORD WEAK: 'short' (5 char) → 422 con detail su password+length")
    fails = 0
    slug = f"{SLUG_PREFIX}-6-weakpw"
    email = f"{SLUG_PREFIX}-6-weakpw@example.com"
    token, _, _ = await _create_invitation(sa_jwt, slug, email, "Smoke S6 WeakPw")

    code, body = await _post_accept_invite(token, "short")
    if not check("(6.1) status 422", ok=code == 422):
        fails += 1
    detail_str = str(body.get("detail", "")).lower()
    if not check(
        "(6.2) detail mentions password",
        ok="password" in detail_str,
        detail=detail_str[:100],
    ):
        fails += 1
    if not check(
        "(6.3) detail mentions length constraint",
        ok=any(s in detail_str for s in ("min", "length", "at least", "12", "short")),
    ):
        fails += 1
    return fails


async def check_7_email_race(sa_jwt: str) -> int:
    """Race condition: utente esistente + invitation con stessa email → 409."""
    step("7. EMAIL RACE: pre-existing user + invitation con stessa email → 409")
    fails = 0

    # Step 1: crea user_x via accept-invite (più realistico di INSERT diretto)
    slug_pre = f"{SLUG_PREFIX}-7-pre"
    email_x = f"{SLUG_PREFIX}-7-race@example.com"
    pre_token, _, _ = await _create_invitation(
        sa_jwt, slug_pre, email_x, "Smoke S6 Race Pre"
    )
    pre_code, _ = await _post_accept_invite(pre_token, "ValidSmokeS6Password")
    if not check("(7.1) pre-existing user creato via accept-invite", ok=pre_code == 200):
        fails += 1

    # Step 2: nuovo client + invitation, poi UPDATE email → email_x
    slug_y = f"{SLUG_PREFIX}-7-race"
    email_y_orig = f"{SLUG_PREFIX}-7-orig@example.com"
    token_y, invitation_y_id, _ = await _create_invitation(
        sa_jwt, slug_y, email_y_orig, "Smoke S6 Race Y"
    )
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        await session.execute(
            text("UPDATE invitations SET email = :e WHERE id = :id"),
            {"e": email_x, "id": invitation_y_id},
        )

    # Step 3: tenta accept invitation_y (con email ora puntata a user esistente)
    code, body = await _post_accept_invite(token_y, "AnotherValidPassword12")
    if not check("(7.2) status 409", ok=code == 409):
        fails += 1
    if not check(
        "(7.3) detail = user already exists with this email",
        ok=body.get("detail") == "user already exists with this email",
    ):
        fails += 1
    return fails


async def check_8_accepted_by_linkage(invitation_a_id: str | None) -> int:
    """Verifica che migration 0003 abbia popolato accepted_by_user_id."""
    step("8. accepted_by_user_id linkage: invitation_1 → SELECT FK")
    fails = 0
    if invitation_a_id is None:
        check("invitation_1 id non disponibile", ok=False)
        return 1

    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        row = (
            await session.execute(
                text(
                    "SELECT i.accepted_by_user_id, u.email "
                    "FROM invitations i "
                    "LEFT JOIN users u ON u.id = i.accepted_by_user_id "
                    "WHERE i.id = :id"
                ),
                {"id": invitation_a_id},
            )
        ).first()
        if row is None:
            check("invitation row found", ok=False)
            return 1
        accepted_by, joined_email = row
        if not check(
            "(8.1) accepted_by_user_id NOT NULL",
            ok=accepted_by is not None,
        ):
            fails += 1
        if not check(
            "(8.2) FK joinable: SELECT users via accepted_by_user_id ritorna l'email",
            ok=joined_email == f"{SLUG_PREFIX}-1-happy@example.com",
            detail=str(joined_email),
        ):
            fails += 1
    return fails


async def check_9_no_info_disclosure(sa_jwt: str) -> int:
    """4 stati invalidi → tutti 404 con body byte-identico."""
    step("9. GET preview: 4 stati invalidi → body BYTE-IDENTICAL (no info disclosure)")
    fails = 0

    # 9.1 not_found: random token
    random_token = secrets.token_urlsafe(32)
    code_nf, body_nf = await _get_invitation_preview(random_token)

    # 9.2 revoked
    slug_r = f"{SLUG_PREFIX}-9-rev"
    token_r, inv_r_id, _ = await _create_invitation(
        sa_jwt, slug_r, f"{SLUG_PREFIX}-9-rev@example.com", "Smoke S6 9 Rev"
    )
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        await session.execute(
            text("UPDATE invitations SET revoked_at = NOW() WHERE id = :id"),
            {"id": inv_r_id},
        )
    code_re, body_re = await _get_invitation_preview(token_r)

    # 9.3 accepted (acceptiamo via direct DB UPDATE per evitare side-effects su user creation)
    slug_a = f"{SLUG_PREFIX}-9-acc"
    token_acc, inv_acc_id, _ = await _create_invitation(
        sa_jwt, slug_a, f"{SLUG_PREFIX}-9-acc@example.com", "Smoke S6 9 Acc"
    )
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        await session.execute(
            text("UPDATE invitations SET accepted_at = NOW() WHERE id = :id"),
            {"id": inv_acc_id},
        )
    code_ac, body_ac = await _get_invitation_preview(token_acc)

    # 9.4 expired
    slug_e = f"{SLUG_PREFIX}-9-exp"
    token_exp, inv_exp_id, _ = await _create_invitation(
        sa_jwt, slug_e, f"{SLUG_PREFIX}-9-exp@example.com", "Smoke S6 9 Exp"
    )
    async with async_session_factory() as session, session.begin():
        await _set_super_admin_context(session)
        await session.execute(
            text(
                "UPDATE invitations SET expires_at = NOW() - INTERVAL '1 day' "
                "WHERE id = :id"
            ),
            {"id": inv_exp_id},
        )
    code_ex, body_ex = await _get_invitation_preview(token_exp)

    # Asserzioni
    if not check("(9.1) not_found → 404", ok=code_nf == 404):
        fails += 1
    if not check("(9.2) revoked → 404", ok=code_re == 404):
        fails += 1
    if not check("(9.3) accepted → 404", ok=code_ac == 404):
        fails += 1
    if not check("(9.4) expired → 404", ok=code_ex == 404):
        fails += 1

    # Byte-identity invariant
    bodies = [body_nf, body_re, body_ac, body_ex]
    all_identical = all(b == bodies[0] for b in bodies)
    if not check(
        "(9.5) tutti i 4 body 404 BYTE-IDENTICAL (no info disclosure)",
        ok=all_identical,
        detail=f"first={bodies[0]!r}" if all_identical else "MISMATCH — INFO LEAK",
    ):
        fails += 1
    if not check(
        "(9.6) body = '{\"detail\":\"invitation not found\"}'",
        ok=bodies[0] == '{"detail":"invitation not found"}',
        detail=bodies[0][:80],
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
        await session.execute(
            text("DELETE FROM users WHERE email LIKE :prefix"),
            {"prefix": f"{SLUG_PREFIX}-%"},
        )


# ─── Driver ─────────────────────────────────────────────────────────────────


async def main() -> int:
    print("=" * 60)
    print("Sessione 6 — Accept invitation flow paranoid smoke test")
    print(f"DB: {SETTINGS.database_url[:60]}…")
    print(f"Backend: {BACKEND_URL}")
    print("=" * 60)

    await _pre_cleanup()
    sa_jwt = await _login_super_admin()

    fails = 0
    fails_1, token_a, invitation_a_id = await check_1_happy_path(sa_jwt)
    fails += fails_1
    fails += await check_2_replay(token_a)
    fails += await check_3_revoked(sa_jwt)
    fails += await check_4_expired(sa_jwt)
    fails += await check_5_not_found()
    fails += await check_6_password_weak(sa_jwt)
    fails += await check_7_email_race(sa_jwt)
    fails += await check_8_accepted_by_linkage(invitation_a_id)
    fails += await check_9_no_info_disclosure(sa_jwt)

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
