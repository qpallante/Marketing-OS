"""Smoke test Sessione 2 — schema + RLS + seed.

Verifica:
  1. Schema: tabelle, enum, trigger, alembic_version presenti
  2. Seed: super_admin + Monoloco + client_admin esistono
  3. RLS fail-closed: query senza SET LOCAL "app.current_client_id" ritorna 0 righe
     (con SET LOCAL ROLE authenticated per bypassare BYPASSRLS del superuser)
  4. RLS scoped: SET LOCAL "app.current_client_id" vede solo i dati del client
  5. RLS audit_log append-only: INSERT consentito, UPDATE e DELETE bloccati

Run with:
    poetry run python scripts/smoke_test_session2.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

OK_ICON = "✅"
FAIL_ICON = "❌"


def _result(label: str, *, ok: bool, detail: str = "") -> bool:
    icon = OK_ICON if ok else FAIL_ICON
    line = f"{icon} {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


async def main() -> int:  # noqa: PLR0912, PLR0915  -- long sequential smoke test, intentional
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url))

    failures = 0

    # ───────────────────────────────────────────────────────────
    # 1. Schema sanity (superuser, no RLS check)
    # ───────────────────────────────────────────────────────────
    print("\n[1] Schema sanity")
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' "
                "AND table_name IN ('clients','users','platform_accounts','audit_log')"
            )
        )
        n_tables = result.scalar()
        if not _result(f"4 domain tables present (got {n_tables}/4)", ok=n_tables == 4):
            failures += 1

        result = await conn.execute(
            text(
                "SELECT count(*) FROM pg_type "
                "WHERE typtype='e' AND typname IN "
                "('user_role','client_status','platform','platform_account_status')"
            )
        )
        n_enums = result.scalar()
        if not _result(f"4 enum types present (got {n_enums}/4)", ok=n_enums == 4):
            failures += 1

        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        version = result.scalar()
        if not _result(
            f"alembic_version = 0001_initial (got {version!r})",
            ok=version == "0001_initial",
        ):
            failures += 1

    # ───────────────────────────────────────────────────────────
    # 2. Seed sanity (superuser bypass)
    # ───────────────────────────────────────────────────────────
    print("\n[2] Seed sanity (superuser)")
    monoloco_id: str | None = None
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT id FROM clients WHERE slug='monoloco'"))
        row = result.one_or_none()
        if not _result("Monoloco client exists", ok=row is not None):
            failures += 1
        if row:
            monoloco_id = str(row[0])
            print(f"     monoloco_id = {monoloco_id}")

        result = await conn.execute(text("SELECT count(*) FROM users WHERE role='super_admin'"))
        n = result.scalar()
        if not _result(
            f"At least 1 super_admin user (got {n})",
            ok=n is not None and n >= 1,
        ):
            failures += 1

        result = await conn.execute(
            text(
                "SELECT count(*) FROM users WHERE role='client_admin' "
                "AND client_id=(SELECT id FROM clients WHERE slug='monoloco')"
            )
        )
        n = result.scalar()
        if not _result(
            f"At least 1 client_admin for Monoloco (got {n})",
            ok=n is not None and n >= 1,
        ):
            failures += 1

    if not monoloco_id:
        print("\n⚠️  Cannot proceed with RLS tests without monoloco_id — aborting.")
        await engine.dispose()
        return 1

    # ───────────────────────────────────────────────────────────
    # 3. RLS fail-closed: SET ROLE authenticated, no settings → 0 rows
    # ───────────────────────────────────────────────────────────
    print("\n[3] RLS fail-closed (ROLE authenticated, no SET LOCAL)")
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE authenticated"))
        # Note: app.current_client_id and app.is_super_admin are intentionally NOT set.

        result = await conn.execute(text("SELECT count(*) FROM clients"))
        n = result.scalar()
        if not _result(f"clients SELECT count = 0 (got {n})", ok=n == 0):
            failures += 1

        result = await conn.execute(text("SELECT count(*) FROM users"))
        n = result.scalar()
        if not _result(f"users SELECT count = 0 (got {n})", ok=n == 0):
            failures += 1

        result = await conn.execute(text("SELECT count(*) FROM platform_accounts"))
        n = result.scalar()
        if not _result(f"platform_accounts SELECT count = 0 (got {n})", ok=n == 0):
            failures += 1

        result = await conn.execute(text("SELECT count(*) FROM audit_log"))
        n = result.scalar()
        if not _result(f"audit_log SELECT count = 0 (got {n})", ok=n == 0):
            failures += 1

    # ───────────────────────────────────────────────────────────
    # 4. RLS scoped: SET LOCAL ROLE authenticated + current_client_id = Monoloco
    # ───────────────────────────────────────────────────────────
    print("\n[4] RLS scoped to Monoloco (ROLE authenticated)")
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE authenticated"))
        await conn.execute(
            text(f"SET LOCAL \"app.current_client_id\" = '{monoloco_id}'")
        )

        result = await conn.execute(text("SELECT id, slug FROM clients"))
        rows = result.fetchall()
        ok = len(rows) == 1 and str(rows[0][0]) == monoloco_id and rows[0][1] == "monoloco"
        if not _result(
            f"clients visible = 1 Monoloco (got {len(rows)} rows)",
            ok=ok,
        ):
            failures += 1

        result = await conn.execute(text("SELECT email, role FROM users ORDER BY role"))
        rows = result.fetchall()
        # Should see ONLY Monoloco's client_admin. super_admin (client_id NULL) NOT visible.
        ok = (
            len(rows) == 1
            and rows[0][1] == "client_admin"
            and rows[0][0] == "admin@monoloco.local"
        )
        if not _result(
            f"users visible = 1 client_admin Monoloco (got {len(rows)} rows: {rows!r})",
            ok=ok,
        ):
            failures += 1

    # ───────────────────────────────────────────────────────────
    # 5. audit_log append-only behavior (ROLE authenticated + super_admin flag)
    # ───────────────────────────────────────────────────────────
    print("\n[5] audit_log append-only (ROLE authenticated + is_super_admin)")
    audit_id: str | None = None
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE authenticated"))
        await conn.execute(text("SET LOCAL \"app.is_super_admin\" = 'true'"))

        # INSERT must succeed
        result = await conn.execute(
            text(
                "INSERT INTO audit_log (action, event_metadata) "
                "VALUES ('smoke_test.run', '{\"source\":\"smoke_test_session2\"}'::jsonb) "
                "RETURNING id"
            )
        )
        audit_id = str(result.scalar())
        _result(f"INSERT into audit_log succeeded (id={audit_id})", ok=True)

    # UPDATE must FAIL (RLS blocks → 0 rows affected, not an exception)
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE authenticated"))
        await conn.execute(text("SET LOCAL \"app.is_super_admin\" = 'true'"))
        result = await conn.execute(
            text("UPDATE audit_log SET action='tampered' WHERE id=:i"),
            {"i": audit_id},
        )
        rows_affected = result.rowcount
        if not _result(
            f"UPDATE on audit_log → 0 rows affected (got {rows_affected})",
            ok=rows_affected == 0,
        ):
            failures += 1

    # DELETE must FAIL (RLS blocks → 0 rows affected)
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE authenticated"))
        await conn.execute(text("SET LOCAL \"app.is_super_admin\" = 'true'"))
        result = await conn.execute(
            text("DELETE FROM audit_log WHERE id=:i"),
            {"i": audit_id},
        )
        rows_affected = result.rowcount
        if not _result(
            f"DELETE on audit_log → 0 rows affected (got {rows_affected})",
            ok=rows_affected == 0,
        ):
            failures += 1

    # Verify the row is still there with original action (read as superuser to confirm)
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT action FROM audit_log WHERE id=:i"),
            {"i": audit_id},
        )
        action = result.scalar()
        if not _result(
            f"audit_log row unchanged (action='{action}')",
            ok=action == "smoke_test.run",
        ):
            failures += 1

    await engine.dispose()

    # ───────────────────────────────────────────────────────────
    # Summary
    # ───────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    if failures == 0:
        print(f"{OK_ICON} ALL SMOKE TESTS PASSED")
        return 0
    print(f"{FAIL_ICON} {failures} smoke test(s) FAILED")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except DBAPIError as e:
        print(f"DB error: {e}")
        sys.exit(2)
