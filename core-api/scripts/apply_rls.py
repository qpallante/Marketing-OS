"""Apply RLS policies from supabase/policies/*.sql to the database.

Idempotent: each file uses CREATE OR REPLACE / DROP POLICY IF EXISTS.
Order is alphabetical: 000_helpers.sql first, then 001..N per-table.

Uses raw asyncpg (not SQLAlchemy) because SQLAlchemy via asyncpg uses the
extended query protocol which cannot run multiple commands in one execute()
call. asyncpg's Connection.execute() supports the simple query protocol and
handles multi-statement SQL files (with $$-delimited functions, etc.).

Run with:
    poetry run python scripts/apply_rls.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

# Ensure core-api/ is on sys.path so `app.*` imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg

from app.core.config import get_settings

POLICIES_DIR = Path(__file__).resolve().parent.parent.parent / "supabase" / "policies"


def _to_asyncpg_dsn(sqlalchemy_url: str) -> str:
    """Strip the SQLAlchemy `postgresql+asyncpg://` prefix to a plain DSN."""
    parsed = urlparse(sqlalchemy_url)
    # Replace driver scheme with `postgresql`
    return parsed._replace(scheme="postgresql").geturl()


async def main() -> int:
    if not POLICIES_DIR.is_dir():
        print(f"ERROR: policies dir not found: {POLICIES_DIR}")
        return 1

    files = sorted(POLICIES_DIR.glob("*.sql"))
    if not files:
        print(f"ERROR: no .sql files in {POLICIES_DIR}")
        return 1

    settings = get_settings()
    dsn = _to_asyncpg_dsn(str(settings.database_url))
    conn = await asyncpg.connect(dsn)

    try:
        async with conn.transaction():
            for f in files:
                sql = f.read_text(encoding="utf-8")
                print(f"→ applying {f.name} ({len(sql)} bytes)")
                await conn.execute(sql)
        print(f"✓ applied {len(files)} policy file(s)")
    finally:
        await conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
