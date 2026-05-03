"""Smoke test paranoid Sessione 7 — Brand Brain RAG.

12 paranoid checks (~30 sub-asserzioni) — pattern S5/S6:
  [k] Pre-flight ambiente (API keys + alembic head + HNSW reloptions)
  [a] Setup login + crea test_client_smoke
  [b] PUT /brand/form upsert (V1 + V2 update verifica last-write-wins)
  [c] POST /assets/text + indexing pipeline + DB embedding dim 1536
  [d] POST /brand/query RAG generation + DB brand_generations row check
  [e] GET /assets list + pagination (?limit=1)
  [f] GET /history list + status='success' check
  [g] DELETE /assets/{id} (204 + CASCADE chunks + history append-only)
  [h] Tenant isolation (Monoloco JWT su random_uuid → 403; cross-tenant → 404)
  [i] Validation (form empty / query empty / upload non-PDF → 415)
  [j] Auth boundary (5 endpoint senza JWT → 401)
  [l] Cleanup CASCADE (DELETE test_client → tutto rimosso, storage dir vuota)

Costo dev stimato: ~$0.001 per run completo (1 query Sonnet + 2 embedding text-3-small).

Eseguibile standalone:
    poetry run python scripts/smoke_test_session7.py

Pre-requisiti:
  - Backend `:8001` attivo
  - Seed dev: `admin@marketing-os.example` (super_admin) + `admin@monoloco.example`
  - `ANTHROPIC_API_KEY` + `OPENAI_API_KEY` popolate in `.env`
  - Migrations alembic 0005 applicate

Idempotente: pre-cleanup di slug `test-s7-smoke-*` da run precedenti.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.db.session import async_session_factory

# ─── Config ──────────────────────────────────────────────────────────────────

BACKEND = "http://localhost:8001"
SA_EMAIL = "admin@marketing-os.example"
SA_PASS = "-5wROzbHtIFBACicZJmukA"  # noqa: S105 — dev seed cred
MONO_EMAIL = "admin@monoloco.example"
MONO_PASS = "epY_6eUdNkIcErdwAAD76Q"  # noqa: S105 — dev seed cred

SLUG_PREFIX = "test-s7-smoke"
INDEXING_WAIT_SECONDS = 30
QUERY_LATENCY_LIMIT_MS = 30_000
EXPECTED_EMBEDDING_DIM = 1536
EXPECTED_ALEMBIC_HEAD_PREFIX = "0005"
HNSW_INDEX_NAME = "ix_brand_chunks_embedding_hnsw"

# ─── Counters + helpers ──────────────────────────────────────────────────────

PASS = 0
FAIL = 0


def hdr(section: str) -> None:
    print(f"\n[{section}]")


def ok(msg: str) -> None:
    global PASS  # noqa: PLW0603
    PASS += 1
    print(f"  ✓ {msg}")


def ko(msg: str) -> None:
    global FAIL  # noqa: PLW0603
    FAIL += 1
    print(f"  ✗ {msg}")


def check(condition: bool, msg_ok: str, msg_ko: str) -> None:
    if condition:
        ok(msg_ok)
    else:
        ko(msg_ko)


# ─── Auth + DB helpers ───────────────────────────────────────────────────────


async def _login(c: httpx.AsyncClient, email: str, password: str) -> str:
    r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return str(r.json()["access_token"])


async def _me(c: httpx.AsyncClient, jwt: str) -> dict[str, Any]:
    r = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {jwt}"})
    r.raise_for_status()
    return dict(r.json())


async def _set_super_admin_context(s: AsyncSession) -> None:
    """ADR-0002 contract: SET LOCAL ROLE authenticated + super_admin GUC."""
    await s.execute(text("SET LOCAL ROLE authenticated"))
    await s.execute(text("SELECT set_config('app.is_super_admin', 'true', true)"))


async def _wait_indexing(asset_id: str, max_wait: int = INDEXING_WAIT_SECONDS) -> str:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        async with async_session_factory() as s, s.begin():
            r = await s.execute(
                text("SELECT indexing_status FROM brand_assets WHERE id = :id"),
                {"id": asset_id},
            )
            st = r.scalar() or "missing"
            if st in ("completed", "failed"):
                return str(st)
        await asyncio.sleep(0.5)
    return "timeout"


async def _delete_client_cascade(client_id: str, storage_dir: Path) -> None:
    """Cancella client + cleanup storage filesystem. Idempotente."""
    async with async_session_factory() as s, s.begin():
        await _set_super_admin_context(s)
        await s.execute(text("DELETE FROM clients WHERE id = :cid"), {"cid": client_id})
    d = storage_dir / client_id
    if d.exists():
        for f in d.glob("*"):
            if f.is_file():
                f.unlink()
        with contextlib.suppress(OSError):
            d.rmdir()


async def _pre_cleanup(storage_dir: Path) -> None:
    """Idempotency: rimuovi test-s7-smoke-* clients da run precedenti."""
    async with async_session_factory() as s, s.begin():
        await _set_super_admin_context(s)
        rows = (
            await s.execute(
                text("SELECT id FROM clients WHERE slug LIKE :p"),
                {"p": f"{SLUG_PREFIX}-%"},
            )
        ).scalars().all()
        for cid in rows:
            d = storage_dir / str(cid)
            if d.exists():
                for f in d.glob("*"):
                    if f.is_file():
                        f.unlink()
                with contextlib.suppress(OSError):
                    d.rmdir()
        await s.execute(
            text("DELETE FROM clients WHERE slug LIKE :p"),
            {"p": f"{SLUG_PREFIX}-%"},
        )


# ─── Driver ──────────────────────────────────────────────────────────────────


async def main() -> int:  # noqa: PLR0912, PLR0915 — long sequential e2e suite, intentional
    settings = get_settings()
    print("=" * 60)
    print("Sessione 7 — Brand Brain paranoid smoke test")
    print(f"Backend: {BACKEND}")
    print(f"DB: {settings.database_url[:60]}…")
    print("=" * 60)

    storage_dir = Path(settings.brand_assets_dir).resolve()
    test_client_id: str | None = None
    test_client_slug = f"{SLUG_PREFIX}-{uuid.uuid4().hex[:8]}"
    asset_a_id: str | None = None
    asset_b_id: str | None = None
    generation_id: str | None = None

    await _pre_cleanup(storage_dir)

    try:
        async with httpx.AsyncClient(base_url=BACKEND, timeout=120.0) as c:
            # ─── (k) Pre-flight ambiente ────────────────────────────────
            hdr("k. Pre-flight ambiente")
            check(
                bool(settings.anthropic_api_key),
                "ANTHROPIC_API_KEY popolata",
                "ANTHROPIC_API_KEY vuota — il test fallirà su /query",
            )
            check(
                bool(settings.openai_api_key),
                "OPENAI_API_KEY popolata",
                "OPENAI_API_KEY vuota — il test fallirà su indexing",
            )
            # NB: alembic_version + pg_class consultati dal role di default
            # (postgres). Il role `authenticated` non ha SELECT su alembic_version
            # — è una tabella interna di Alembic, fuori dal data plane RLS.
            async with async_session_factory() as s, s.begin():
                head = (
                    await s.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar()
                check(
                    isinstance(head, str) and head.startswith(EXPECTED_ALEMBIC_HEAD_PREFIX),
                    f"alembic head = {head}",
                    f"unexpected head: {head}",
                )
                idx_row = (
                    await s.execute(
                        text(
                            "SELECT reloptions FROM pg_class WHERE relname = :n"
                        ),
                        {"n": HNSW_INDEX_NAME},
                    )
                ).first()
                if idx_row is None:
                    ko(f"HNSW index '{HNSW_INDEX_NAME}' non trovato")
                else:
                    reloptions = idx_row[0] or []
                    opts = " ".join(reloptions)
                    check(
                        "m=16" in opts and "ef_construction=64" in opts,
                        f"HNSW reloptions: [{opts}]",
                        f"unexpected reloptions: {opts}",
                    )

            # ─── (a) Setup login + test_client ──────────────────────────
            hdr("a. Setup: super_admin + Monoloco login + crea test_client_smoke")
            sa_jwt = await _login(c, SA_EMAIL, SA_PASS)
            mono_jwt = await _login(c, MONO_EMAIL, MONO_PASS)
            mono = await _me(c, mono_jwt)
            mono_cid = str(mono["client_id"])
            ok(f"super_admin + Monoloco JWT acquisiti (mono_cid={mono_cid[:8]}…)")

            h_sa = {"Authorization": f"Bearer {sa_jwt}", "Content-Type": "application/json"}
            h_mono = {"Authorization": f"Bearer {mono_jwt}", "Content-Type": "application/json"}

            r_create = await c.post(
                "/api/v1/admin/clients",
                json={
                    "name": "Smoke S7 Test",
                    "slug": test_client_slug,
                    "admin_email": f"{test_client_slug}@example.com",
                },
                headers=h_sa,
            )
            check(
                r_create.status_code == 201,
                "POST /admin/clients → 201",
                f"got {r_create.status_code}: {r_create.text[:200]}",
            )
            if r_create.status_code != 201:
                ko("Cannot continue without test_client_id")
                return 1
            test_client_id = str(r_create.json()["client"]["id"])
            ok(f"test_client_id = {test_client_id[:8]}… slug={test_client_slug}")

            # ─── (b) PUT /brand/form upsert ─────────────────────────────
            hdr("b. PUT /brand/form upsert (V1 + V2 last-write-wins)")
            v1 = {
                "tone_keywords": ["premium", "italiano"],
                "dos": ["usa frasi brevi"],
                "donts": ["evita tecnicismi"],
                "colors_hex": ["#000000"],
            }
            r_v1 = await c.put(
                f"/api/v1/clients/{test_client_id}/brand/form",
                json=v1,
                headers=h_sa,
            )
            check(
                r_v1.status_code == 200,
                "V1 PUT → 200",
                f"got {r_v1.status_code}: {r_v1.text[:150]}",
            )

            v2 = {
                "tone_keywords": ["minimal", "elegante", "esclusivo"],
                "dos": ["mantieni il tono autorevole", "concretezza"],
                "donts": ["niente claim infondati"],
                "colors_hex": ["#FF0000", "#FFFFFF"],
            }
            r_v2 = await c.put(
                f"/api/v1/clients/{test_client_id}/brand/form",
                json=v2,
                headers=h_sa,
            )
            check(r_v2.status_code == 200, "V2 PUT → 200", f"got {r_v2.status_code}")
            body_v2 = r_v2.json() if r_v2.status_code == 200 else {}
            check(
                body_v2.get("tone_keywords") == v2["tone_keywords"],
                "response tone_keywords match V2",
                f"got {body_v2.get('tone_keywords')}",
            )
            async with async_session_factory() as s, s.begin():
                await _set_super_admin_context(s)
                cnt = (
                    await s.execute(
                        text(
                            "SELECT count(*) FROM brand_form_data WHERE client_id = :cid"
                        ),
                        {"cid": test_client_id},
                    )
                ).scalar()
                check(cnt == 1, "DB brand_form_data 1 row (UNIQUE client_id)", f"got {cnt}")
                row = (
                    await s.execute(
                        text(
                            "SELECT tone_keywords FROM brand_form_data WHERE client_id = :cid"
                        ),
                        {"cid": test_client_id},
                    )
                ).first()
                if row is not None:
                    check(
                        list(row[0]) == v2["tone_keywords"],
                        "DB tone_keywords = V2 (last write wins)",
                        f"got {list(row[0])}",
                    )

            # ─── (c) POST /assets/text + indexing pipeline ──────────────
            hdr("c. POST /assets/text + indexing pipeline + embedding non-NULL")
            text_content = "Smoke S7 brand profile testo di esempio per RAG. " * 20
            r_a = await c.post(
                f"/api/v1/clients/{test_client_id}/brand/assets/text",
                json={"title": "Smoke S7 brand profile", "text_content": text_content},
                headers=h_sa,
            )
            check(
                r_a.status_code == 201,
                "POST /assets/text → 201",
                f"got {r_a.status_code}: {r_a.text[:200]}",
            )
            if r_a.status_code == 201:
                asset_a_id = str(r_a.json()["id"])
                check(
                    r_a.json().get("indexing_status") == "pending",
                    "initial indexing_status='pending'",
                    f"got {r_a.json().get('indexing_status')}",
                )
                st = await _wait_indexing(asset_a_id)
                check(st == "completed", "asset_a indexed", f"got status={st}")
                # GET /assets verifica chunks_count
                r_list = await c.get(
                    f"/api/v1/clients/{test_client_id}/brand/assets",
                    headers=h_sa,
                )
                items = r_list.json().get("items", []) if r_list.status_code == 200 else []
                target = next((it for it in items if it["id"] == asset_a_id), None)
                if target is not None:
                    check(
                        target.get("chunks_count", 0) >= 1,
                        f"chunks_count={target.get('chunks_count')} ≥ 1",
                        f"chunks_count={target.get('chunks_count')}",
                    )
                # DB direct: embedding non-NULL + dim 1536
                async with async_session_factory() as s, s.begin():
                    await _set_super_admin_context(s)
                    dim = (
                        await s.execute(
                            text(
                                "SELECT vector_dims(embedding) FROM brand_chunks "
                                "WHERE asset_id = :id LIMIT 1"
                            ),
                            {"id": asset_a_id},
                        )
                    ).scalar()
                    check(
                        dim == EXPECTED_EMBEDDING_DIM,
                        f"DB brand_chunks.embedding dim = {EXPECTED_EMBEDDING_DIM}",
                        f"got {dim}",
                    )

            # Asset B per cross-tenant test [h]
            r_b = await c.post(
                f"/api/v1/clients/{test_client_id}/brand/assets/text",
                json={
                    "title": "Smoke S7 secondary",
                    "text_content": "Asset secondario per test cross-tenant. " * 15,
                },
                headers=h_sa,
            )
            if r_b.status_code == 201:
                asset_b_id = str(r_b.json()["id"])
                await _wait_indexing(asset_b_id)

            # ─── (d) POST /brand/query RAG ──────────────────────────────
            hdr("d. POST /brand/query RAG generation + DB brand_generations check")
            r_q = await c.post(
                f"/api/v1/clients/{test_client_id}/brand/query",
                json={"user_prompt": "Genera 1 caption Instagram per il summer drop"},
                headers=h_sa,
            )
            check(
                r_q.status_code == 200,
                "status 200",
                f"got {r_q.status_code}: {r_q.text[:200]}",
            )
            if r_q.status_code == 200:
                qb = r_q.json()
                generation_id = str(qb.get("generation_id"))
                output_text = qb.get("output_text", "")
                check(
                    isinstance(output_text, str) and len(output_text) > 20,
                    f"output_text non vuoto ({len(output_text)} char)",
                    f"got len={len(output_text) if isinstance(output_text, str) else 'NaN'}",
                )
                check(
                    len(qb.get("retrieved_chunks", [])) >= 1,
                    f"retrieved_chunks count={len(qb.get('retrieved_chunks', []))} ≥ 1",
                    "no chunks retrieved (RAG non sta funzionando)",
                )
                check(
                    qb.get("tokens_input", 0) > 50,
                    f"tokens_input={qb.get('tokens_input')} > 50",
                    f"tokens_input={qb.get('tokens_input')} too low",
                )
                check(
                    qb.get("tokens_output", 0) > 10,
                    f"tokens_output={qb.get('tokens_output')} > 10",
                    f"tokens_output={qb.get('tokens_output')} too low",
                )
                check(
                    qb.get("latency_ms", QUERY_LATENCY_LIMIT_MS + 1) < QUERY_LATENCY_LIMIT_MS,
                    f"latency_ms={qb.get('latency_ms')} < {QUERY_LATENCY_LIMIT_MS}",
                    f"latency too high: {qb.get('latency_ms')}ms",
                )
                # DB direct: brand_generations row
                async with async_session_factory() as s, s.begin():
                    await _set_super_admin_context(s)
                    row = (
                        await s.execute(
                            text(
                                "SELECT status, model_used FROM brand_generations "
                                "WHERE id = :id"
                            ),
                            {"id": generation_id},
                        )
                    ).first()
                    if row is None:
                        ko("DB brand_generations row non trovata")
                    else:
                        check(
                            row[0] == "success",
                            "DB status='success'",
                            f"got status={row[0]}",
                        )
                        check(
                            isinstance(row[1], str) and row[1].startswith("claude-"),
                            f"DB model_used='{row[1]}' (claude-*)",
                            f"got model_used={row[1]}",
                        )

            # ─── (e) GET /assets list + pagination ──────────────────────
            hdr("e. GET /assets list + pagination ?limit=1")
            r_l = await c.get(
                f"/api/v1/clients/{test_client_id}/brand/assets", headers=h_sa,
            )
            check(r_l.status_code == 200, "status 200", f"got {r_l.status_code}")
            body = r_l.json() if r_l.status_code == 200 else {}
            assets_total_pre_delete = body.get("total", 0)
            check(
                assets_total_pre_delete >= 1,
                f"total={assets_total_pre_delete} ≥ 1",
                f"total={assets_total_pre_delete}",
            )
            check(
                len(body.get("items", [])) >= 1,
                "items ≥ 1",
                "items empty",
            )
            r_lim = await c.get(
                f"/api/v1/clients/{test_client_id}/brand/assets?limit=1",
                headers=h_sa,
            )
            body_lim = r_lim.json() if r_lim.status_code == 200 else {}
            check(
                len(body_lim.get("items", [])) == 1,
                "limit=1 → 1 item",
                f"got {len(body_lim.get('items', []))}",
            )
            check(
                body_lim.get("total") == assets_total_pre_delete,
                f"total invariato con limit ({assets_total_pre_delete})",
                f"diff: {body_lim.get('total')} vs {assets_total_pre_delete}",
            )

            # ─── (f) GET /history list ──────────────────────────────────
            hdr("f. GET /history list + status='success' check")
            r_h = await c.get(
                f"/api/v1/clients/{test_client_id}/brand/history", headers=h_sa,
            )
            check(r_h.status_code == 200, "status 200", f"got {r_h.status_code}")
            hb = r_h.json() if r_h.status_code == 200 else {}
            history_total_pre_delete = hb.get("total", 0)
            check(
                history_total_pre_delete >= 1,
                f"history total={history_total_pre_delete} ≥ 1",
                f"got {history_total_pre_delete}",
            )
            items_h = hb.get("items", [])
            if items_h:
                check(
                    items_h[0].get("status") == "success",
                    f"items[0].status='{items_h[0].get('status')}'",
                    f"got {items_h[0].get('status')}",
                )
                check(
                    items_h[0].get("tokens_output", 0) > 0,
                    f"items[0].tokens_output={items_h[0].get('tokens_output')} > 0",
                    "tokens_output 0",
                )

            # ─── (g) DELETE /assets/{id} CASCADE + history append-only ──
            hdr("g. DELETE /assets/{id} + CASCADE chunks + history invariata")
            if asset_a_id:
                r_del = await c.delete(
                    f"/api/v1/clients/{test_client_id}/brand/assets/{asset_a_id}",
                    headers=h_sa,
                )
                check(
                    r_del.status_code == 204,
                    "DELETE → 204",
                    f"got {r_del.status_code}",
                )
                r_l2 = await c.get(
                    f"/api/v1/clients/{test_client_id}/brand/assets",
                    headers=h_sa,
                )
                body2 = r_l2.json() if r_l2.status_code == 200 else {}
                check(
                    body2.get("total") == assets_total_pre_delete - 1,
                    f"total ridotto: {assets_total_pre_delete} → {body2.get('total')}",
                    f"total invariato: {body2.get('total')}",
                )
                async with async_session_factory() as s, s.begin():
                    await _set_super_admin_context(s)
                    chunks_left = (
                        await s.execute(
                            text(
                                "SELECT count(*) FROM brand_chunks WHERE asset_id = :id"
                            ),
                            {"id": asset_a_id},
                        )
                    ).scalar()
                    check(
                        chunks_left == 0,
                        "brand_chunks CASCADE → 0 rows per asset cancellato",
                        f"got {chunks_left}",
                    )
                # History append-only: brand_generations row INVARIATA dopo DELETE asset
                r_h2 = await c.get(
                    f"/api/v1/clients/{test_client_id}/brand/history",
                    headers=h_sa,
                )
                hb2 = r_h2.json() if r_h2.status_code == 200 else {}
                check(
                    hb2.get("total", 0) == history_total_pre_delete,
                    f"history append-only invariato ({hb2.get('total')})",
                    f"changed: {history_total_pre_delete} → {hb2.get('total')}",
                )

            # ─── (h) Tenant isolation ──────────────────────────────────
            hdr("h. Tenant isolation: random_uuid 403 + cross-tenant 404 (privacy)")
            r_iso = await c.get(
                f"/api/v1/clients/{uuid.uuid4()}/brand/assets",
                headers=h_mono,
            )
            check(
                r_iso.status_code == 403,
                "Monoloco JWT su random_uuid → 403",
                f"got {r_iso.status_code}",
            )
            if asset_b_id:
                r_xt = await c.delete(
                    f"/api/v1/clients/{mono_cid}/brand/assets/{asset_b_id}",
                    headers=h_mono,
                )
                check(
                    r_xt.status_code == 404,
                    "cross-tenant DELETE asset_id → 404 (privacy)",
                    f"got {r_xt.status_code}",
                )
                async with async_session_factory() as s, s.begin():
                    await _set_super_admin_context(s)
                    still = (
                        await s.execute(
                            text("SELECT count(*) FROM brand_assets WHERE id = :id"),
                            {"id": asset_b_id},
                        )
                    ).scalar()
                    check(
                        still == 1,
                        "asset_b NON cancellato (cross-tenant fail-safe)",
                        f"got count={still}",
                    )

            # ─── (i) Validation ────────────────────────────────────────
            hdr("i. Validation: form empty / query empty / upload non-PDF")
            r_v_form = await c.put(
                f"/api/v1/clients/{test_client_id}/brand/form",
                json={"tone_keywords": [], "dos": [], "donts": [], "colors_hex": []},
                headers=h_sa,
            )
            check(
                r_v_form.status_code == 422,
                "form tone_keywords=[] → 422",
                f"got {r_v_form.status_code}",
            )
            r_v_q = await c.post(
                f"/api/v1/clients/{test_client_id}/brand/query",
                json={"user_prompt": ""},
                headers=h_sa,
            )
            check(
                r_v_q.status_code == 422,
                "query user_prompt='' → 422",
                f"got {r_v_q.status_code}",
            )
            files = {"file": ("smoke.txt", b"not a pdf content", "text/plain")}
            r_v_up = await c.post(
                f"/api/v1/clients/{test_client_id}/brand/assets/upload",
                files=files,
                headers={"Authorization": f"Bearer {sa_jwt}"},
            )
            check(
                r_v_up.status_code == 415,
                "upload non-PDF → 415 (magic bytes mismatch)",
                f"got {r_v_up.status_code}: {r_v_up.text[:150]}",
            )

            # ─── (j) Auth boundary ─────────────────────────────────────
            hdr("j. Auth boundary: 5 endpoint senza JWT → 401")
            no_auth: list[tuple[str, str, dict[str, Any] | None]] = [
                ("GET", f"/api/v1/clients/{test_client_id}/brand/assets", None),
                ("PUT", f"/api/v1/clients/{test_client_id}/brand/form", {}),
                ("POST", f"/api/v1/clients/{test_client_id}/brand/query", {}),
                ("POST", f"/api/v1/clients/{test_client_id}/brand/assets/text", {}),
                ("GET", f"/api/v1/clients/{test_client_id}/brand/history", None),
            ]
            for method, path, payload in no_auth:
                if method == "GET":
                    r = await c.get(path)
                elif method == "PUT":
                    r = await c.put(path, json=payload)
                else:  # POST
                    r = await c.post(path, json=payload)
                check(
                    r.status_code == 401,
                    f"{method} {path.split('/brand')[-1] or '/'} → 401",
                    f"got {r.status_code}",
                )

        # ─── (l) Cleanup CASCADE ───────────────────────────────────────
        hdr("l. Cleanup CASCADE: DELETE test_client → tutto rimosso")
        await _delete_client_cascade(test_client_id, storage_dir)
        async with async_session_factory() as s, s.begin():
            await _set_super_admin_context(s)
            cleanup_queries = {
                "brand_form_data": "SELECT count(*) FROM brand_form_data WHERE client_id = :cid",
                "brand_assets": "SELECT count(*) FROM brand_assets WHERE client_id = :cid",
                "brand_generations": (
                    "SELECT count(*) FROM brand_generations WHERE client_id = :cid"
                ),
            }
            for tname, query in cleanup_queries.items():
                cnt = (
                    await s.execute(text(query), {"cid": test_client_id})
                ).scalar()
                check(
                    cnt == 0,
                    f"{tname} CASCADE'd (0 rows)",
                    f"got {cnt}",
                )
            orph = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM brand_chunks bc "
                        "WHERE NOT EXISTS ("
                        "  SELECT 1 FROM brand_assets ba WHERE ba.id = bc.asset_id"
                        ")"
                    )
                )
            ).scalar()
            check(
                orph == 0,
                f"brand_chunks no orphans ({orph})",
                f"orphans: {orph}",
            )
        d = storage_dir / test_client_id
        is_clean = (not d.exists()) or (d.is_dir() and not any(d.iterdir()))
        check(
            is_clean,
            f"storage dir cleaned: {d.name}/",
            f"still has files: {list(d.glob('*')) if d.exists() else 'ok'}",
        )
        test_client_id = None  # marker: cleanup OK, finally no-op

    finally:
        # Defensive cleanup se [l] non ha avuto chance di girare (early failure)
        if test_client_id:
            try:
                await _delete_client_cascade(test_client_id, storage_dir)
                print(f"  ⚠ defensive cleanup eseguita per {test_client_id[:8]}…")
            except Exception as e:
                print(f"  ⚠ defensive cleanup fallita: {e}")

    print()
    print("=" * 60)
    if FAIL == 0:
        print(f"✅ {PASS}/{PASS} paranoid checks pass")
    else:
        print(f"❌ PASS: {PASS} | FAIL: {FAIL}")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
