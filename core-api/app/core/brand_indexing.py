"""Brand assets indexing pipeline (async via FastAPI BackgroundTasks).

Pipeline per PDF (5b.1 path):
    PDF bytes da filesystem → extract_text → split_text → embed_batch → INSERT chunks

Pipeline per text content (5b.2 path):
    text_content → split_text → embed_batch → INSERT chunks

Stati `indexing_status`:
    pending → completed (success: chunks_count > 0)
    pending → failed   (con `indexing_detail = error message` truncated 500 char)

**Pattern session in BackgroundTasks**: il task gira FUORI dal request scope
(post-response). Apriamo `async_session_factory()` direttamente come
`postgres` superuser → bypass RLS automatico, no `SET LOCAL ROLE`. La
sicurezza è già stata applicata nell'upload endpoint via
`require_client_access` + RLS. Qui siamo in trusted backend code che
opera per conto del client già verificato.

`_mark_failed` apre **fresh session** per non riusare una session in stato
"failed transaction" dopo un rollback inside `_index_text`.

Vedi ADR-0008 §"Indexing pipeline async via BackgroundTasks (S7), Celery in S+".
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from sqlalchemy import text, update

from app.core.ai.chunking import split_text
from app.core.ai.factory import get_embedder
from app.core.ai.pdf import (
    PDFExtractionError,
    PDFNoTextExtractedError,
    PDFPasswordProtectedError,
    extract_text_from_pdf,
)
from app.core.brand_storage import get_asset_path
from app.db.session import async_session_factory
from app.models.brand import BrandAsset

log = structlog.get_logger(__name__)

#: Cap su `indexing_detail` per evitare row con error message giganti
#: (es. trace stack 50K char). 500 è ragionevole per debug + UI display.
_MAX_DETAIL_LENGTH = 500

#: Timing race **FastAPI BackgroundTasks** (osservato in S7 step 5b.2 testing):
#: il bg task viene schedulato all'`add_task()` ma può iniziare PRIMA che la
#: request session abbia visibility del commit (la dep `get_authenticated_session`
#: usa auto-commit context manager che fa il commit a `__aexit__`, e in alcune
#: corse di event loop il commit non è ancora globalmente visibile alla nuova
#: connection del bg task → `ForeignKeyViolationError` su brand_chunks.asset_id).
#: Workaround pragmatico: poll/retry breve (max 3s totali) al inizio del
#: pipeline per attendere che la row asset sia visibile. NON modifica il
#: pattern auto-commit ADR-0002. Vedi ADR-0008 §"Indexing race fix".
_VISIBILITY_MAX_RETRIES = 100  # 10s budget — empirico, FastAPI BackgroundTasks
_VISIBILITY_DELAY_SECONDS = 0.1  # può schedulare prima del commit fully visible


async def _wait_for_asset_visible(asset_id: UUID) -> None:
    """Poll fino a quando la row `brand_assets` è visibile dalla nostra session.

    Solleva `RuntimeError` se dopo `_VISIBILITY_MAX_RETRIES * _VISIBILITY_DELAY_SECONDS`
    la row non è apparsa (anomaly: commit del request fallito o connection issue).
    """
    for _ in range(_VISIBILITY_MAX_RETRIES):
        async with async_session_factory() as db:
            r = await db.execute(
                text("SELECT 1 FROM brand_assets WHERE id = :id"),
                {"id": str(asset_id)},
            )
            if r.scalar() is not None:
                return
        await asyncio.sleep(_VISIBILITY_DELAY_SECONDS)

    raise RuntimeError(
        f"Asset {asset_id} not visible after "
        f"{_VISIBILITY_MAX_RETRIES * _VISIBILITY_DELAY_SECONDS:.1f}s — "
        f"timing race or commit failure",
    )


async def index_pdf_asset_task(asset_id: UUID, client_id: UUID) -> None:
    """Background task: indexa un PDF asset.

    Read file da filesystem (path deterministico via `get_asset_path`),
    extract text via pypdf, poi delega a `_index_text` per chunking +
    embedding + INSERT.

    Su qualunque errore noto (PDF protected/corrupted/no-text) → marca
    `failed` con detail. Su errore inatteso → catch generico + mark failed.
    """
    try:
        pdf_path = get_asset_path(client_id, asset_id, extension="pdf")
        if not pdf_path.exists():
            await _mark_failed(asset_id, f"Asset file missing: {pdf_path}")
            return

        file_bytes = pdf_path.read_bytes()

        try:
            full_text = extract_text_from_pdf(file_bytes)
        except (
            PDFPasswordProtectedError,
            PDFNoTextExtractedError,
            PDFExtractionError,
        ) as exc:
            await _mark_failed(
                asset_id, f"PDF extraction: {type(exc).__name__}: {exc}",
            )
            return

        await _index_text(asset_id, client_id, full_text)
    except Exception as exc:
        log.exception(
            "brand.pdf_index_unexpected_error",
            asset_id=str(asset_id),
            error_type=type(exc).__name__,
        )
        await _mark_failed(asset_id, f"Unexpected: {type(exc).__name__}: {exc}")


async def index_text_asset_task(
    asset_id: UUID,
    client_id: UUID,
    text_content: str,
) -> None:
    """Background task: indexa un text asset (no PDF parsing).

    Stesso pattern di `index_pdf_asset_task` ma senza step PDF parsing —
    delega direttamente a `_index_text`.
    """
    try:
        await _index_text(asset_id, client_id, text_content)
    except Exception as exc:
        log.exception(
            "brand.text_index_unexpected_error",
            asset_id=str(asset_id),
            error_type=type(exc).__name__,
        )
        await _mark_failed(asset_id, f"Unexpected: {type(exc).__name__}: {exc}")


async def _index_text(asset_id: UUID, client_id: UUID, content: str) -> None:
    """Helper condiviso: chunking + embedding + INSERT chunks + UPDATE asset.

    Chiamato da `index_pdf_asset_task` (dopo PDF extraction) e
    `index_text_asset_task` (direttamente con text_content).

    Failure paths espliciti (chunks vuoto, mismatch count) marcano `failed`
    e ritornano normalmente. Failure paths inattesi (DB error, embedder
    error) sollevano l'eccezione: il caller (outer task) catcha + marca.
    """
    # Workaround timing race FastAPI BackgroundTasks: attendi che la row
    # `brand_assets` sia visibile (request commit potrebbe non essere ancora
    # propagato alla nostra fresh connection). Vedi commento in cima al file.
    await _wait_for_asset_visible(asset_id)

    chunks = split_text(content)
    if not chunks:
        await _mark_failed(
            asset_id,
            "No chunks generated from content (empty after split — text vuoto o "
            "whitespace-only)",
        )
        return

    embedder = get_embedder()
    chunk_texts = [c.text for c in chunks]
    result = await embedder.embed_batch(chunk_texts)

    if len(result.vectors) != len(chunks):
        await _mark_failed(
            asset_id,
            f"Embedding count mismatch: {len(result.vectors)} vectors "
            f"vs {len(chunks)} chunks",
        )
        return

    # INSERT chunks via raw SQL (CAST AS vector — lezione step 2: ":" param
    # marker vs "::" cast operator ambiguity in SQLAlchemy/asyncpg).
    # UPDATE asset status nella stessa transazione → atomic: o tutti gli
    # N chunks + status=completed, o nessuno (rollback completo).
    async with async_session_factory() as db, db.begin():
        for chunk, vector in zip(chunks, result.vectors, strict=True):
            # Format vector come Postgres literal: '[v1,v2,...]'
            vec_literal = "[" + ",".join(f"{v:.8f}" for v in vector) + "]"
            await db.execute(
                text(
                    "INSERT INTO brand_chunks "
                    "(client_id, asset_id, chunk_index, chunk_text, "
                    " embedding, token_count) "
                    "VALUES (:cid, :aid, :idx, :txt, "
                    "        CAST(:emb AS vector), :tk)",
                ),
                {
                    "cid": str(client_id),
                    "aid": str(asset_id),
                    "idx": chunk.index,
                    "txt": chunk.text,
                    "emb": vec_literal,
                    "tk": chunk.token_count,
                },
            )

        await db.execute(
            update(BrandAsset)
            .where(BrandAsset.id == asset_id)
            .values(
                indexing_status="completed",
                indexing_detail=None,
                chunks_count=len(chunks),
            ),
        )

    log.info(
        "brand.asset_indexed",
        asset_id=str(asset_id),
        client_id=str(client_id),
        chunks_count=len(chunks),
        total_embedding_tokens=result.tokens_used,
        embedding_model=result.model,
    )


async def _mark_failed(asset_id: UUID, detail: str) -> None:
    """Marca l'asset come `failed` con detail. Trunca a `_MAX_DETAIL_LENGTH`.

    **Apre fresh session**: se siamo qui dopo un rollback in `_index_text`,
    la session originale è in stato "failed transaction". Una nuova session
    è clean.

    Se anche questo UPDATE fallisce (DB unreachable), l'eccezione propaga e
    l'asset resta in `pending` per sempre. Mitigation: cleanup job futuro
    che trova stale `pending` rows oltre TTL ragionevole. S+.
    """
    truncated = detail[:_MAX_DETAIL_LENGTH]
    async with async_session_factory() as db, db.begin():
        await db.execute(
            update(BrandAsset)
            .where(BrandAsset.id == asset_id)
            .values(
                indexing_status="failed",
                indexing_detail=truncated,
            ),
        )

    log.warning(
        "brand.asset_index_failed",
        asset_id=str(asset_id),
        detail=truncated,
    )
